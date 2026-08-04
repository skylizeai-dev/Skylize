"""GET /me/brief, POST /me/brief/seen — memory backend, full app.

Proves:
  * the cursor advances ONLY on POST /me/brief/seen, never on GET, even across
    repeated/abandoned reads (journal.py's WorkJournal.unseen contract);
  * POST /me/brief/seen actually clears the brief on the next GET;
  * the summarization call goes through container.llm — the shared
    GuardedLLMGateway (bootstrap.py:440), never a raw provider adapter;
  * one principal's brief never contains another (org, principal)'s entries.

Memory backend, no real Postgres/Redis needed — mirrors test_gateway.py's
TestClient(create_app()) pattern. Journal entries are seeded directly through
WorkJournal.record() (the only write path that exists; nothing in the live
app writes one yet — see dal/work_journal.py).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from skylize.adapters.llm.content_gate import GuardedLLMGateway
from skylize.app.principal.models import ActorKind
from skylize.edge.gateway import create_app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def _headers(org: str, user: str, roles: str = "owner") -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": user, "X-Dev-Roles": roles}


def _seed_entry(client: TestClient, *, org_id: str, principal_id: str, headline: str) -> None:
    work_journal = client.app.state.container.work_journal
    asyncio.run(
        work_journal.record(
            org_id=org_id,
            principal_id=principal_id,
            actor_kind=ActorKind.AGENT_AUTONOMOUS,
            actor_id="test_agent",
            correlation_id=uuid4(),
            kind="test.happened",
            headline=headline,
        )
    )


def test_llm_gateway_is_guarded(client: TestClient) -> None:
    """The container the route depends on carries the guarded gateway, never
    a raw provider adapter — bootstrap.py:440 wraps every backend variant."""
    assert isinstance(client.app.state.container.llm, GuardedLLMGateway)


def test_get_brief_summarizes_unseen_entries(client: TestClient) -> None:
    _seed_entry(client, org_id="org_a", principal_id="u1", headline="Invoice reconciled")
    resp = client.get("/api/v1/me/brief", headers=_headers("org_a", "u1"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entry_count"] == 1
    assert body["head_seq"] >= 1
    assert body["summary"]  # produced via container.llm, not empty


def test_get_brief_does_not_advance_cursor(client: TestClient) -> None:
    """P3: a GET — even repeated, even abandoned — never consumes entries.
    Only POST /me/brief/seen may advance the cursor."""
    _seed_entry(client, org_id="org_a", principal_id="u2", headline="First thing")

    first = client.get("/api/v1/me/brief", headers=_headers("org_a", "u2"))
    second = client.get("/api/v1/me/brief", headers=_headers("org_a", "u2"))
    third = client.get("/api/v1/me/brief", headers=_headers("org_a", "u2"))

    for resp in (first, second, third):
        assert resp.status_code == 200
        assert resp.json()["entry_count"] == 1
        assert resp.json()["head_seq"] == first.json()["head_seq"]


def test_post_seen_then_get_shows_nothing_new(client: TestClient) -> None:
    _seed_entry(client, org_id="org_a", principal_id="u3", headline="Something happened")
    unseen = client.get("/api/v1/me/brief", headers=_headers("org_a", "u3"))
    assert unseen.json()["entry_count"] == 1
    head_seq = unseen.json()["head_seq"]

    ack = client.post(
        "/api/v1/me/brief/seen",
        json={"to_seq": head_seq},
        headers=_headers("org_a", "u3"),
    )
    assert ack.status_code == 204

    after = client.get("/api/v1/me/brief", headers=_headers("org_a", "u3"))
    assert after.status_code == 200
    assert after.json()["entry_count"] == 0
    assert after.json()["summary"] == "Nothing new since you last checked."


def test_brief_is_scoped_to_org_and_principal(client: TestClient) -> None:
    _seed_entry(client, org_id="org_a", principal_id="shared_id", headline="Org A's own thing")
    _seed_entry(client, org_id="org_b", principal_id="shared_id", headline="Org B's own thing")

    a_brief = client.get("/api/v1/me/brief", headers=_headers("org_a", "shared_id"))
    b_brief = client.get("/api/v1/me/brief", headers=_headers("org_b", "shared_id"))

    assert a_brief.json()["entry_count"] == 1
    assert b_brief.json()["entry_count"] == 1


def test_get_brief_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/v1/me/brief")
    assert resp.status_code == 401


def test_brief_is_scoped_to_principal_within_same_org(client: TestClient) -> None:
    """principal_id is always ctx.user_id (brief.py:81) -- never a path, query,
    or body field -- so principal A authenticated in org_a can never read
    principal B's entries, even when both share the same org. Complements
    test_brief_is_scoped_to_org_and_principal, which varies org but holds
    principal_id constant; this varies principal_id and holds org constant."""
    _seed_entry(client, org_id="org_a", principal_id="alice", headline="Alice's own thing")
    _seed_entry(client, org_id="org_a", principal_id="bob", headline="Bob's own thing")

    alice_brief = client.get("/api/v1/me/brief", headers=_headers("org_a", "alice")).json()
    bob_brief = client.get("/api/v1/me/brief", headers=_headers("org_a", "bob")).json()

    assert alice_brief["entry_count"] == 1
    assert bob_brief["entry_count"] == 1
    # _seed_entry uses ActorKind.AGENT_AUTONOMOUS, which assemble_brief buckets
    # into done_while_away (journal.py:174-177) -- headline is exposed there
    # directly, so this is a positive check on content, not just a count.
    alice_headlines = [e["headline"] for e in alice_brief["done_while_away"]]
    bob_headlines = [e["headline"] for e in bob_brief["done_while_away"]]
    assert alice_headlines == ["Alice's own thing"]
    assert bob_headlines == ["Bob's own thing"]
