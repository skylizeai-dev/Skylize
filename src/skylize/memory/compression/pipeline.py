"""The compression pipeline — `compress(payload, ctx) -> CompressionResult`.

This is the proxy that sits between agent context assembly and the LLM Gateway
egress. It composes the tiers in order:

    L1 (deterministic prune)  →  policy decision  →  L2 (semantic route, if worth it)

then measures the result with tiktoken and produces an audited `CompressionResult`.

Totality is the contract: `compress` ALWAYS returns a `CompressionResult` and
never raises for a recoverable compression failure. A failed L2 degrades to the
L1-only text and records a `compression.l2_degraded` audit action; the
correlation_id from the context is threaded through to that audit record.

Audit egress is injected, not owned here. The event bus / emitter is another
module's concern (and emitting a full `BaseEvent` needs envelope fields this
package does not hold). The pipeline calls an optional `audit_sink(CompressionAudit)`
and always returns the same audit projection on the result, so the caller can
build and publish the `audit.action_recorded` event.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from skylize.memory.compression.budget import (
    BudgetPolicy,
    compute_ratio,
    count_tokens,
)
from skylize.memory.compression.l1_deterministic import (
    DEFAULT_MAX_STRING_CHARS,
    compress_l1,
)
from skylize.memory.compression.l2_semantic import L2SemanticRouter
from skylize.schemas.compression import (
    CompressionAudit,
    CompressionContext,
    CompressionResult,
    CompressionStage,
)

# How chunks are stitched back into a single text after L2 selection. A blank
# line keeps chunk boundaries legible to the model.
_CHUNK_JOINER = "\n\n"

AuditSink = Callable[[CompressionAudit], None]


def _split_chunks(text: str) -> list[str]:
    """Split L1 output into chunks for semantic routing.

    Blank-line-delimited blocks are the chunk unit (the same join L2 reassembles
    with). Empty blocks are dropped so they never occupy a top_k slot.
    """
    return [block for block in text.split(_CHUNK_JOINER) if block.strip()]


def _build_result(
    *,
    ctx: CompressionContext,
    text: str,
    tokens_in: int,
    stages: list[CompressionStage],
    started: float,
    action_type: str,
    degraded_reason: str | None,
) -> CompressionResult:
    """Assemble the measured, audited result. tokens_out is measured here."""
    tokens_out = count_tokens(text)
    ratio = compute_ratio(tokens_in, tokens_out)
    duration_ms = (time.perf_counter() - started) * 1000.0
    audit = CompressionAudit(
        correlation_id=ctx.correlation_id,
        org_id=ctx.org_id,
        action_type=action_type,
        call_class=ctx.call_class,
        stages_applied=stages,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        ratio=ratio,
        duration_ms=duration_ms,
        degraded_reason=degraded_reason,
    )
    return CompressionResult(
        compressed_text=text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        ratio=ratio,
        stages_applied=stages,
        duration_ms=duration_ms,
        audit=audit,
    )


def compress(
    payload: str,
    ctx: CompressionContext,
    *,
    router: L2SemanticRouter | None = None,
    policy: BudgetPolicy | None = None,
    audit_sink: AuditSink | None = None,
) -> CompressionResult:
    """Compress `payload` under `ctx`, returning an audited result.

    L1 always runs. L2 runs only when the budget policy says it is worthwhile AND
    a semantic router was injected AND the context carries a routing query. A
    router failure degrades to L1-only with a `compression.l2_degraded` audit
    record. This function never raises for a recoverable compression failure.

    `audit_sink`, if provided, is invoked with the `CompressionAudit` before the
    result is returned — the hook through which the caller publishes the
    `audit.action_recorded` event with correlation_id pass-through.
    """
    started = time.perf_counter()
    policy = policy or BudgetPolicy()
    max_chars = ctx.max_string_chars or DEFAULT_MAX_STRING_CHARS

    tokens_in = count_tokens(payload)

    # ── L1: always ───────────────────────────────────────────────────────────
    l1_text = compress_l1(payload, max_chars=max_chars)
    stages: list[CompressionStage] = [CompressionStage.L1_DETERMINISTIC]

    # ── Policy: is L2 worth its latency? ─────────────────────────────────────
    l1_tokens = count_tokens(l1_text)
    run_l2 = router is not None and policy.should_run_l2(
        tokens=l1_tokens,
        call_class=ctx.call_class,
        has_query=ctx.query is not None,
        force_l1_only=ctx.force_l1_only,
    )

    if not run_l2:
        result = _build_result(
            ctx=ctx,
            text=l1_text,
            tokens_in=tokens_in,
            stages=stages,
            started=started,
            action_type="compression.applied",
            degraded_reason=None,
        )
        if audit_sink is not None:
            audit_sink(result.audit)
        return result

    # ── L2: semantic routing (router and query guaranteed non-None here) ─────
    assert router is not None and ctx.query is not None  # narrowed by run_l2
    # Chunk the ORIGINAL payload, not the L1 text: L1's whitespace-collapse
    # flattens the blank-line delimiters chunks are split on, so splitting l1_text
    # would yield a single chunk. Each surviving chunk is L1-pruned individually.
    raw_chunks = _split_chunks(payload)
    chunks = [compress_l1(c, max_chars=max_chars) for c in raw_chunks]
    route = router.route(chunks, ctx.query, ctx.top_k)

    if route.degraded:
        # Never block: fall back to the L1-only text, record the degraded path.
        stages.append(CompressionStage.L2_DEGRADED)
        result = _build_result(
            ctx=ctx,
            text=l1_text,
            tokens_in=tokens_in,
            stages=stages,
            started=started,
            action_type="compression.l2_degraded",
            degraded_reason=route.reason,
        )
        if audit_sink is not None:
            audit_sink(result.audit)
        return result

    stages.append(CompressionStage.L2_SEMANTIC)
    routed_text = _CHUNK_JOINER.join(route.chunks)
    result = _build_result(
        ctx=ctx,
        text=routed_text,
        tokens_in=tokens_in,
        stages=stages,
        started=started,
        action_type="compression.applied",
        degraded_reason=None,
    )
    if audit_sink is not None:
        audit_sink(result.audit)
    return result
