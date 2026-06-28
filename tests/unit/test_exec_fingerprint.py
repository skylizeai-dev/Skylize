"""
Tool-execution fingerprint + pre-dispatch dedup (runtime/exec_fingerprint.py).

Proves: the fingerprint is stable and key-order independent; it isolates tenants;
the dedup cache gives SETNX semantics so two concurrent identical calls collapse
to one dispatch with both callers served the same Pydantic-round-tripping result;
and reservations expire on TTL.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict

from skylize.runtime.exec_fingerprint import (
    DEFAULT_DEDUP_TTL_SECONDS,
    InMemoryDedupCache,
    canonical_args_json,
    compute_exec_fingerprint,
    dedup_key,
    normalize_args,
)

ORG = "org_test"


class _ToolResponse(BaseModel):
    """Stand-in for a real tool response; the cache value must round-trip via this."""

    model_config = ConfigDict(extra="forbid")
    ok: bool
    value: int
    note: str


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_deterministic() -> None:
    args = {"prompt": "hi", "max_tokens": 32}
    a = compute_exec_fingerprint(org_id=ORG, tool_name="llm.generate", args=args)
    b = compute_exec_fingerprint(org_id=ORG, tool_name="llm.generate", args=args)
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_fingerprint_is_key_order_independent() -> None:
    a = compute_exec_fingerprint(
        org_id=ORG, tool_name="llm.generate", args={"a": 1, "b": 2}
    )
    b = compute_exec_fingerprint(
        org_id=ORG, tool_name="llm.generate", args={"b": 2, "a": 1}
    )
    assert a == b


def test_fingerprint_nested_key_order_independent() -> None:
    a = compute_exec_fingerprint(
        org_id=ORG, tool_name="t", args={"outer": {"x": 1, "y": 2}, "list": [1, 2]}
    )
    b = compute_exec_fingerprint(
        org_id=ORG, tool_name="t", args={"list": [1, 2], "outer": {"y": 2, "x": 1}}
    )
    assert a == b


def test_fingerprint_isolates_tenant() -> None:
    args = {"prompt": "hi"}
    a = compute_exec_fingerprint(org_id="org_a", tool_name="llm.generate", args=args)
    b = compute_exec_fingerprint(org_id="org_b", tool_name="llm.generate", args=args)
    assert a != b


def test_fingerprint_distinguishes_tool_and_args() -> None:
    base = compute_exec_fingerprint(org_id=ORG, tool_name="llm.generate", args={"p": 1})
    other_tool = compute_exec_fingerprint(org_id=ORG, tool_name="memory.search", args={"p": 1})
    other_args = compute_exec_fingerprint(org_id=ORG, tool_name="llm.generate", args={"p": 2})
    assert base != other_tool
    assert base != other_args


def test_list_order_is_significant() -> None:
    a = compute_exec_fingerprint(org_id=ORG, tool_name="t", args={"xs": [1, 2]})
    b = compute_exec_fingerprint(org_id=ORG, tool_name="t", args={"xs": [2, 1]})
    assert a != b


def test_normalize_args_sorts_nested_dicts() -> None:
    assert normalize_args({"b": {"d": 1, "c": 2}, "a": 3}) == {
        "a": 3,
        "b": {"c": 2, "d": 1},
    }


def test_normalize_args_tuple_becomes_list_with_normalized_items() -> None:
    # Tuples are coerced to lists (JSON has no tuple) with each item normalized.
    assert normalize_args(({"b": 1, "a": 2}, 3)) == [{"a": 2, "b": 1}, 3]


def test_tuple_and_list_args_fingerprint_identically() -> None:
    as_tuple = compute_exec_fingerprint(org_id=ORG, tool_name="t", args={"xs": (1, 2)})
    as_list = compute_exec_fingerprint(org_id=ORG, tool_name="t", args={"xs": [1, 2]})
    assert as_tuple == as_list


def test_canonical_args_json_is_sorted_and_compact() -> None:
    assert canonical_args_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_dedup_key_shape() -> None:
    fp = "deadbeef"
    assert dedup_key(ORG, fp) == f"toolexec:{ORG}:{fp}"


# ---------------------------------------------------------------------------
# Dedup cache — SETNX semantics
# ---------------------------------------------------------------------------

async def test_first_caller_reserves_second_is_deduped() -> None:
    cache = InMemoryDedupCache()
    key = dedup_key(ORG, "fp1")

    first = await cache.try_reserve(key, ttl_seconds=60)
    assert first.reserved is True  # cache miss → this caller dispatches

    # Winner dispatches and stores the result.
    result = _ToolResponse(ok=True, value=7, note="done")
    await cache.store(key, result.model_dump_json(), ttl_seconds=60)

    second = await cache.try_reserve(key, ttl_seconds=60)
    assert second.reserved is False  # cache hit → deduped
    assert second.cached_result is not None
    # The cached value round-trips back into the same Pydantic model.
    assert _ToolResponse.model_validate_json(second.cached_result) == result


async def test_two_concurrent_identical_calls_one_dispatch() -> None:
    """Acceptance: 2 concurrent identical tool calls → 1 dispatch, both same result."""
    cache = InMemoryDedupCache()
    fp = compute_exec_fingerprint(
        org_id=ORG, tool_name="llm.generate", args={"prompt": "same"}
    )
    key = dedup_key(ORG, fp)
    dispatch_count = 0

    async def call() -> str:
        nonlocal dispatch_count
        outcome = await cache.try_reserve(key, ttl_seconds=60)
        if outcome.reserved:
            dispatch_count += 1
            response = _ToolResponse(ok=True, value=42, note="dispatched")
            await cache.store(key, response.model_dump_json(), ttl_seconds=60)
            return response.model_dump_json()
        # Deduped caller: serve the winner's result (await it if still in flight).
        if outcome.cached_result is not None:
            return outcome.cached_result
        cached = await cache.get(key)
        assert cached is not None
        return cached

    a, b = await asyncio.gather(call(), call())

    assert dispatch_count == 1  # exactly one real dispatch
    # Both callers observe the identical result.
    assert _ToolResponse.model_validate_json(a) == _ToolResponse.model_validate_json(b)


async def test_reservation_expires_after_ttl() -> None:
    cache = InMemoryDedupCache()
    key = dedup_key(ORG, "fp_ttl")

    first = await cache.try_reserve(key, ttl_seconds=1)  # 1 logical tick
    assert first.reserved is True

    # The in-memory cache advances its logical clock on every op; a couple of
    # subsequent ops push past a 1-tick TTL, so the key is free to reserve again.
    await cache.get(key)
    await cache.get(key)
    again = await cache.try_reserve(key, ttl_seconds=1)
    assert again.reserved is True  # prior reservation expired


async def test_distinct_fingerprints_do_not_collide() -> None:
    cache = InMemoryDedupCache()
    k1 = dedup_key(ORG, "fpA")
    k2 = dedup_key(ORG, "fpB")
    assert (await cache.try_reserve(k1, ttl_seconds=60)).reserved is True
    assert (await cache.try_reserve(k2, ttl_seconds=60)).reserved is True  # independent


async def test_default_ttl_constant_used_when_unspecified() -> None:
    cache = InMemoryDedupCache()
    key = dedup_key(ORG, "fp_default")
    # Reserve with the module default; key stays held across a couple of reads.
    assert (await cache.try_reserve(key)).reserved is True
    assert (await cache.try_reserve(key)).reserved is False
    assert DEFAULT_DEDUP_TTL_SECONDS == 60
