"""Principal-scoped memory namespaces, and the leak the contract grant alone leaves.

`memory_read_access` is STATIC and shared by every employee using a given agent.
So `cowork_agent`'s grant of `principal:*` says only that the SHAPE is allowed —
on its own it would let Devon's session read `principal:alice:notes`, because the
pattern matches and nothing else in the gateway knows who is calling.

These tests pin the second check that closes it: the concrete principal is bound
at CALL time and a mismatch is refused. Both halves are required, and the tests
below assert each one independently so neither can be removed silently.
"""

from __future__ import annotations

import pytest

from skylize.contracts.registry import MVP_REGISTRY
from skylize.memory.exceptions import MemoryNamespaceViolation, MemoryPermissionDenied
from skylize.memory.gateway import MemoryGateway, _principal_of
from skylize.schemas.memory import MemoryEntry, MemoryScope

ORG = "org_test"
DEVON = "devon"
ALICE = "alice"


class _FakeAdapter:
    """Records what actually reached storage, so a test can prove a denial
    stopped BEFORE the data layer rather than after it."""

    def __init__(self) -> None:
        self.retrieved: list[MemoryScope] = []
        self.stored: list[tuple[MemoryScope, MemoryEntry]] = []

    async def retrieve(self, scope: MemoryScope) -> list[MemoryEntry]:
        self.retrieved.append(scope)
        return []

    async def store(self, scope: MemoryScope, entry: MemoryEntry) -> None:
        self.stored.append((scope, entry))


def _gateway() -> tuple[MemoryGateway, _FakeAdapter]:
    adapter = _FakeAdapter()
    return MemoryGateway(adapter=adapter, registry=MVP_REGISTRY), adapter


def _scope(namespace: str | None) -> MemoryScope:
    return MemoryScope(org_id=ORG, department=namespace)


def _entry() -> MemoryEntry:
    return MemoryEntry(
        org_id=ORG,
        agent_id="cowork_agent",
        tier="working",
        content_text="a note",
        importance_score=0.9,
    )


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "namespace, expected",
    [
        ("principal:devon:notes", "devon"),
        ("principal:devon", "devon"),
        ("principal:devon:deep:nested", "devon"),
        ("creative:briefs", None),  # not principal-scoped at all
        ("principal:", None),  # claims to be, names nobody
        ("principal::notes", None),  # empty id segment
    ],
)
def test_principal_of(namespace: str, expected: str | None) -> None:
    assert _principal_of(namespace) == expected


# --------------------------------------------------------------------------- #
# The leak that the contract grant alone would leave
# --------------------------------------------------------------------------- #


async def test_own_namespace_is_readable() -> None:
    gw, adapter = _gateway()
    await gw.read(
        "cowork_agent",
        _scope(f"principal:{DEVON}:notes"),
        caller_org_id=ORG,
        caller_principal_id=DEVON,
    )
    assert len(adapter.retrieved) == 1


async def test_another_principals_namespace_is_refused_on_read() -> None:
    """THE cross-principal leak. The contract pattern `principal:*` matches
    Alice's namespace perfectly — only the call-time binding stops this."""
    gw, adapter = _gateway()
    with pytest.raises(MemoryNamespaceViolation, match="may not read"):
        await gw.read(
            "cowork_agent",
            _scope(f"principal:{ALICE}:notes"),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )
    assert adapter.retrieved == []  # refused BEFORE the data layer


async def test_another_principals_namespace_is_refused_on_write() -> None:
    """Writing into someone else's namespace is as bad as reading out of it."""
    gw, adapter = _gateway()
    with pytest.raises(MemoryNamespaceViolation, match="may not write"):
        await gw.write(
            "cowork_agent",
            _scope(f"principal:{ALICE}:notes"),
            _entry(),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )
    assert adapter.stored == []


async def test_no_principal_bound_reaches_no_principal_namespace() -> None:
    """An autonomous token carries no on_behalf_of claim, so it has no principal
    — and must therefore reach nobody's principal memory, not everybody's."""
    gw, adapter = _gateway()
    with pytest.raises(MemoryNamespaceViolation, match="no principal bound"):
        await gw.read(
            "cowork_agent",
            _scope(f"principal:{DEVON}:notes"),
            caller_org_id=ORG,
            caller_principal_id=None,
        )
    assert adapter.retrieved == []


async def test_namespace_naming_no_principal_is_refused_not_matched() -> None:
    """`principal:` names nobody. Refusing is the only safe reading — treating it
    as a wildcard would match everybody."""
    gw, _ = _gateway()
    with pytest.raises(MemoryNamespaceViolation, match="names no principal"):
        await gw.read(
            "cowork_agent",
            _scope("principal:"),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )


async def test_prefix_collision_does_not_grant_access() -> None:
    """`devon2` must not be reachable by `devon` through a prefix match — the
    owner is compared as a whole segment, not with startswith."""
    gw, _ = _gateway()
    with pytest.raises(MemoryNamespaceViolation, match="may not read"):
        await gw.read(
            "cowork_agent",
            _scope("principal:devon2:notes"),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )


# --------------------------------------------------------------------------- #
# The contract check is still required — the binding did not replace it
# --------------------------------------------------------------------------- #


async def test_stateless_agent_still_denied_before_any_principal_logic() -> None:
    """cfo_agent has memory_read_access=[]; binding a principal must not become
    a way around that."""
    gw, adapter = _gateway()
    with pytest.raises(MemoryPermissionDenied, match="no memory_read_access"):
        await gw.read(
            "cfo_agent",
            _scope(f"principal:{DEVON}:notes"),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )
    assert adapter.retrieved == []


async def test_agent_without_the_principal_pattern_is_denied() -> None:
    """seo_keyword_agent has memory access, but not to `principal:*` — the
    contract check refuses it before the binding is ever consulted."""
    gw, _ = _gateway()
    with pytest.raises(MemoryPermissionDenied, match="not granted read access"):
        await gw.read(
            "seo_keyword_agent",
            _scope(f"principal:{DEVON}:notes"),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )


async def test_org_guard_still_fires_first() -> None:
    """Tenant isolation is the outermost check and must not be reordered."""
    gw, _ = _gateway()
    with pytest.raises(MemoryNamespaceViolation, match="does not match caller"):
        await gw.read(
            "cowork_agent",
            MemoryScope(org_id="other_org", department=f"principal:{DEVON}:notes"),
            caller_org_id=ORG,
            caller_principal_id=DEVON,
        )


async def test_non_principal_namespace_is_unaffected_by_the_binding() -> None:
    """A department namespace behaves exactly as before, with or without a
    principal bound — the new check is scoped to `principal:` only."""
    gw, adapter = _gateway()
    await gw.read(
        "seo_keyword_agent",
        _scope("seo:keywords"),
        caller_org_id=ORG,
        caller_principal_id=None,
    )
    assert len(adapter.retrieved) == 1
