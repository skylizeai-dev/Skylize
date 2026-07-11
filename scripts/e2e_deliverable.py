"""End-to-end happy-path: onboarding -> creative crew -> deliverable round-trip.

Drives the real HTTP surface (create_app) against the memory backend:
  1. register an owner            POST /api/v1/auth/register
  2. trigger the creative crew    POST /api/v1/agents/execute   (hook_generator_agent)
  3. read the deliverable back    GET  /api/v1/deliverables/{id}
  4. confirm it shows in the list GET  /api/v1/deliverables

The LLM provider is whatever bootstrap selects: the deterministic demo adapter
by default, or real Claude when SKYLIZE_ANTHROPIC_API_KEY is set. The provider
that actually served is printed so a live run is unmistakable.

Run:
    python scripts/e2e_deliverable.py
Live (real Claude):
    SKYLIZE_ANTHROPIC_API_KEY=sk-... python scripts/e2e_deliverable.py
(or put the key in .env)
"""

from __future__ import annotations

import sys
from uuid import uuid4

from fastapi.testclient import TestClient

from skylize.config import get_settings
from skylize.edge.gateway import create_app

# Dev-auth headers (owner) authorize the agent-execute call locally.
OWNER = {"X-Dev-Org": "acme", "X-Dev-User": "owner-1", "X-Dev-Roles": "owner"}


def main() -> int:
    settings = get_settings()
    live = bool(settings.anthropic_api_key)
    print(f"provider mode: {'LIVE Anthropic' if live else 'demo adapter'} "
          f"(backend={settings.backend})\n")

    with TestClient(create_app()) as c:
        # 1. Onboard: first user in an org becomes owner.
        r = c.post(
            "/api/v1/auth/register",
            json={"org_id": "acme", "email": "founder@acme.com", "password": "hunter2pw"},
        )
        assert r.status_code == 201, f"register failed: {r.status_code} {r.text}"
        print(f"[1/4] registered owner: {r.json()['email']} roles={r.json()['roles']}")

        # 2. Trigger the creative crew (hook_generator_agent is single-shot, no tools).
        r = c.post(
            "/api/v1/agents/execute",
            headers=OWNER,
            json={
                "agent_id": "hook_generator_agent",
                "input": {
                    "brief_id": str(uuid4()),
                    "product": "an AI-native business operating system",
                    "audience": "seed-stage startup founders",
                },
            },
        )
        if r.status_code != 201:
            print(f"[2/4] EXECUTE FAILED: {r.status_code} {r.text}")
            return 1
        body = r.json()
        deliverable_id = body["deliverable_id"]
        print(f"[2/4] crew produced deliverable: {body['title']!r} "
              f"(id={deliverable_id}, status={body['status']})")

        # 3. Read it back.
        r = c.get(f"/api/v1/deliverables/{deliverable_id}", headers=OWNER)
        assert r.status_code == 200, f"get failed: {r.status_code} {r.text}"
        d = r.json()
        print(f"[3/4] fetched deliverable, provider={d.get('metadata_json', {}).get('llm_provider', '?')}")
        print("----- content preview -----")
        print((d.get("content_markdown") or "")[:600])
        print("---------------------------")

        # 4. Confirm it lists (what the console would show).
        r = c.get("/api/v1/deliverables", headers=OWNER)
        assert r.status_code == 200, f"list failed: {r.status_code} {r.text}"
        payload = r.json()
        total = payload.get("pagination", {}).get("total", len(payload.get("data", [])))
        assert total >= 1, f"expected the new deliverable in the list, got {total}"
        print(f"[4/4] deliverables list shows {total} item(s) for org 'acme'")

    print("\nOK — deliverable round-tripped end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
