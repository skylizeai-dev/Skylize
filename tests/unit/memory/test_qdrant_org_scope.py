"""The Qdrant org scope is STRUCTURAL — proven against a fake client.

`platform_knowledge` is one collection shared by every tenant, so the `org_id`
filter is the whole tenant boundary; there is no store-level equivalent of
Postgres RLS behind it. Before this contract, `search(vector, k, {})` and
`delete_by_filter({})` were both legal calls that silently addressed the WHOLE
collection, and the org condition was assembled by each call site inside a
free-form dict.

These tests pin the two halves of the fix:

  * UNSCOPED CALLS DO NOT EXIST. Omitting `org_id` raises TypeError; blanking it
    raises OrgScopeRequired; smuggling it through `filters` raises
    OrgScopeRequired. The reflection test makes this total, so a NEW public
    method added without an org scope fails here rather than shipping.
  * A SCOPED CALL REALLY REACHES THE CLIENT SCOPED. The fake client records the
    exact Filter/payload it was handed, so "the org condition was applied" is
    asserted against the wire, not against the adapter's intent.

The fake client is deliberate: Qdrant is not reachable in unit CI, and a mock
that accepts anything would make every assertion here vacuous.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from skylize.memory.org_scope import OrgScopeRequired
from skylize.memory.qdrant_adapter import QdrantAdapter, QdrantPoint

ORG_A = "org_a"
ORG_B = "org_b"
VECTOR = [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# Fake client — records what the adapter actually put on the wire
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self) -> None:
        from qdrant_client.http.models import UpdateStatus

        self.status = UpdateStatus.COMPLETED


class _Record:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.score = 1.0


class _Collections:
    def __init__(self) -> None:
        self.collections: list[Any] = []


class FakeClient:
    """Records every call; stores points so retrieve/search can be real."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.search_filters: list[Any] = []
        self.delete_filters: list[Any] = []
        self.upserted_payloads: list[dict[str, Any]] = []

    async def get_collections(self) -> _Collections:
        return _Collections()

    async def create_collection(self, **_: Any) -> None:
        return None

    async def upsert(self, *, collection_name: str, points: list[Any]) -> _Result:
        for p in points:
            self.points[str(p.id)] = dict(p.payload)
            self.upserted_payloads.append(dict(p.payload))
        return _Result()

    async def retrieve(
        self, *, collection_name: str, ids: list[str], with_payload: bool
    ) -> list[_Record]:
        return [_Record(self.points[i]) for i in ids if i in self.points]

    async def delete(self, *, collection_name: str, points_selector: Any) -> _Result:
        self.delete_filters.append(points_selector.filter)
        return _Result()

    async def search(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        limit: int,
        query_filter: Any,
        with_payload: bool,
    ) -> list[_Record]:
        self.search_filters.append(query_filter)
        conditions = {c.key: c.match.value for c in (query_filter.must or [])}
        return [
            _Record(payload)
            for payload in self.points.values()
            if all(payload.get(k) == v for k, v in conditions.items())
        ][:limit]


@pytest.fixture()
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture()
def adapter(client: FakeClient) -> QdrantAdapter:
    # __new__ rather than __init__: the real ctor opens an AsyncQdrantClient and
    # would reach for an unreachable server. Only _client/_collection_ready are
    # instance state, and both are set here.
    a = QdrantAdapter.__new__(QdrantAdapter)
    a._client = client  # type: ignore[assignment]
    a._collection_ready = False
    return a


def _point(org_id: str, point_id: str, **payload: Any) -> QdrantPoint:
    return QdrantPoint(org_id=org_id, point_id=point_id, vector=VECTOR, payload=payload)


def _conditions(qdrant_filter: Any) -> dict[str, Any]:
    return {c.key: c.match.value for c in (qdrant_filter.must or [])}


# ---------------------------------------------------------------------------
# 1. An unscoped call does not exist — reflection over the whole public surface
# ---------------------------------------------------------------------------

# upsert_points takes no org_id argument: each QdrantPoint carries its own
# (required) org_id, which is asserted separately below.
_SCOPE_EXEMPT = {"upsert_points"}


def test_every_public_collection_method_requires_an_org_id_keyword() -> None:
    """Total, not enumerated: a new unscoped public method fails this test."""
    checked = []
    for name, member in inspect.getmembers(QdrantAdapter, inspect.isfunction):
        if name.startswith("_") or name in _SCOPE_EXEMPT:
            continue
        sig = inspect.signature(member)
        org = sig.parameters.get("org_id")
        assert org is not None, f"{name}() can address the shared collection without an org scope"
        assert org.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name}(org_id=...) must be keyword-only so it cannot be filled positionally by accident"
        )
        assert org.default is inspect.Parameter.empty, (
            f"{name}() has a DEFAULT org scope; forgetting it must be an error, not a fallback"
        )
        checked.append(name)
    # Guard against the reflection silently checking nothing.
    assert set(checked) >= {
        "search",
        "delete_by_filter",
        "verify_point",
        "point_doc_hash",
        "verify_document",
        "upsert_vector",
    }


def test_qdrant_point_cannot_be_built_without_an_org_id() -> None:
    """The one exempt method is safe because its input type carries the scope."""
    with pytest.raises(Exception) as exc:
        QdrantPoint(point_id="p1", vector=VECTOR, payload={})  # type: ignore[call-arg]
    assert "org_id" in str(exc.value)


async def test_search_without_org_id_is_a_type_error(adapter: QdrantAdapter) -> None:
    with pytest.raises(TypeError):
        await adapter.search(VECTOR, 5)  # type: ignore[call-arg]


async def test_delete_by_filter_without_org_id_is_a_type_error(adapter: QdrantAdapter) -> None:
    with pytest.raises(TypeError):
        await adapter.delete_by_filter({"parent_doc_id": "d"})  # type: ignore[call-arg]


@pytest.mark.parametrize("blank", ["", "   "])
async def test_blank_org_scope_fails_closed(adapter: QdrantAdapter, blank: str) -> None:
    """A blank scope must raise, never widen to the whole collection."""
    with pytest.raises(OrgScopeRequired):
        await adapter.search(VECTOR, 5, org_id=blank)


async def test_org_id_smuggled_through_filters_is_rejected(adapter: QdrantAdapter) -> None:
    """A call site must not be able to shadow the scope it was handed."""
    with pytest.raises(OrgScopeRequired):
        await adapter.search(VECTOR, 5, {"org_id": ORG_B}, org_id=ORG_A)
    with pytest.raises(OrgScopeRequired):
        await adapter.delete_by_filter({"org_id": ORG_B}, org_id=ORG_A)


# ---------------------------------------------------------------------------
# 2. A scoped call reaches the client scoped
# ---------------------------------------------------------------------------


async def test_search_passes_the_org_filter_through_to_the_client(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    await adapter.search(VECTOR, 5, {"department": "finance"}, org_id=ORG_A)
    assert len(client.search_filters) == 1
    assert _conditions(client.search_filters[0]) == {
        "org_id": ORG_A,
        "department": "finance",
    }


async def test_search_with_no_extra_filters_is_still_org_filtered(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    """The regression this whole contract exists for: empty filters used to mean
    query_filter=None, i.e. an unfiltered read of every tenant's points."""
    await adapter.search(VECTOR, 5, org_id=ORG_A)
    assert _conditions(client.search_filters[0]) == {"org_id": ORG_A}

    await adapter.search(VECTOR, 5, {}, org_id=ORG_A)
    assert _conditions(client.search_filters[1]) == {"org_id": ORG_A}


async def test_delete_with_no_filters_cannot_reach_beyond_the_tenant(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    """delete_by_filter({}) used to build Filter(must=[]) — the whole collection."""
    await adapter.delete_by_filter(org_id=ORG_A)
    assert _conditions(client.delete_filters[0]) == {"org_id": ORG_A}


async def test_search_cannot_return_another_tenants_points(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    await adapter.upsert_points([_point(ORG_A, "p-a", content_text="A secret")])
    await adapter.upsert_points([_point(ORG_B, "p-b", content_text="B secret")])

    hits_b = await adapter.search(VECTOR, 10, org_id=ORG_B)
    assert [h["content_text"] for h in hits_b] == ["B secret"]


# ---------------------------------------------------------------------------
# 3. Writes are labelled by the adapter, not by caller convention
# ---------------------------------------------------------------------------


async def test_upsert_points_stamps_org_id_into_the_payload(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    await adapter.upsert_points([_point(ORG_A, "p-1", content_text="hello")])
    assert client.upserted_payloads[0]["org_id"] == ORG_A


async def test_upsert_points_org_id_wins_over_a_mislabelled_payload(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    """Payload is caller data; the scope is not. The scope wins."""
    mislabelled = QdrantPoint(
        org_id=ORG_A,
        point_id="p-1",
        vector=VECTOR,
        payload={"org_id": ORG_B, "content_text": "x"},
    )
    await adapter.upsert_points([mislabelled])
    assert client.upserted_payloads[0]["org_id"] == ORG_A


async def test_upsert_vector_stamps_org_id_over_caller_metadata(
    adapter: QdrantAdapter, client: FakeClient
) -> None:
    await adapter.upsert_vector("doc-1", VECTOR, {"org_id": ORG_B, "text": "x"}, org_id=ORG_A)
    assert client.upserted_payloads[0]["org_id"] == ORG_A


async def test_upsert_points_rejects_a_blank_org_scope(adapter: QdrantAdapter) -> None:
    with pytest.raises(OrgScopeRequired):
        await adapter.upsert_points([_point("", "p-1", content_text="x")])


# ---------------------------------------------------------------------------
# 4. Reads addressed by point id fail closed on a foreign point
# ---------------------------------------------------------------------------


async def test_verify_point_reads_a_foreign_point_as_absent(
    adapter: QdrantAdapter,
) -> None:
    await adapter.upsert_points(
        [_point(ORG_A, "p-a", content_hash="h1", doc_content_hash="d1")]
    )
    assert await adapter.verify_point("p-a", "h1", org_id=ORG_A) is True
    assert await adapter.verify_point("p-a", "h1", org_id=ORG_B) is False


async def test_point_doc_hash_is_none_for_a_foreign_point(adapter: QdrantAdapter) -> None:
    await adapter.upsert_points(
        [_point(ORG_A, "p-a", content_hash="h1", doc_content_hash="d1")]
    )
    assert await adapter.point_doc_hash("p-a", org_id=ORG_A) == "d1"
    assert await adapter.point_doc_hash("p-a", org_id=ORG_B) is None


async def test_verify_document_is_false_for_a_foreign_tenant(
    adapter: QdrantAdapter,
) -> None:
    """The legacy md5 id is NOT org-injective, so two tenants sharing a doc_id
    land on one point. The org check is therefore on the stored payload: a
    collision cannot leak, even though the id alone would not distinguish them."""
    await adapter.upsert_vector("shared-doc", VECTOR, {"content_hash": "h1"}, org_id=ORG_A)
    assert await adapter.verify_document("shared-doc", "h1", org_id=ORG_A) is True
    assert await adapter.verify_document("shared-doc", "h1", org_id=ORG_B) is False
