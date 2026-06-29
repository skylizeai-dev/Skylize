"""Unit tests for the compression pipeline and budget policy.

Covers the budget decision (token counting, ratio, per-class thresholds, L2
gating), and the `compress` orchestration: L1-always, policy-gated L2, the
non-blocking degraded path, audit-sink emission, correlation_id pass-through, and
the totality contract (compress never raises for a recoverable failure).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from skylize.memory.compression.budget import (
    BudgetPolicy,
    compute_ratio,
    count_tokens,
)
from skylize.memory.compression.l2_semantic import L2SemanticRouter
from skylize.memory.compression.pipeline import compress
from skylize.schemas.compression import (
    CallClass,
    CompressionAudit,
    CompressionContext,
    CompressionResult,
    CompressionStage,
)


# ── Test doubles ─────────────────────────────────────────────────────────────
class KeywordEmbedder:
    """Same toy embedder as the L2 suite: 'match' → axis 0, else axis 1."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "match" in t else [0.0, 1.0] for t in texts]


class ExplodingEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model unavailable")


def _ctx(**overrides: object) -> CompressionContext:
    base: dict[str, object] = {"correlation_id": uuid4(), "org_id": "org_test"}
    base.update(overrides)
    return CompressionContext(**base)  # type: ignore[arg-type]


# ── Budget ───────────────────────────────────────────────────────────────────
class TestTokenCounting:
    def test_empty_is_zero(self) -> None:
        assert count_tokens("") == 0

    def test_nonempty_is_positive(self) -> None:
        assert count_tokens("hello world") > 0

    def test_special_token_sequence_does_not_raise(self) -> None:
        # cl100k special-token literal in scraped data must count as plain text.
        assert count_tokens("<|endoftext|> appears in the body") > 0


class TestComputeRatio:
    def test_empty_input_is_neutral_one(self) -> None:
        assert compute_ratio(0, 0) == 1.0

    def test_reduction(self) -> None:
        assert compute_ratio(100, 40) == pytest.approx(0.4)


class TestBudgetPolicy:
    def test_below_threshold_skips_l2(self) -> None:
        policy = BudgetPolicy()
        assert not policy.should_run_l2(
            tokens=10,
            call_class=CallClass.TOOL_RESULT,
            has_query=True,
            force_l1_only=False,
        )

    def test_at_threshold_runs_l2(self) -> None:
        policy = BudgetPolicy()
        threshold = policy.l2_threshold_for(CallClass.MEMORY_RECALL)
        assert policy.should_run_l2(
            tokens=threshold,
            call_class=CallClass.MEMORY_RECALL,
            has_query=True,
            force_l1_only=False,
        )

    def test_no_query_never_runs_l2(self) -> None:
        policy = BudgetPolicy()
        assert not policy.should_run_l2(
            tokens=100_000,
            call_class=CallClass.GENERIC,
            has_query=False,
            force_l1_only=False,
        )

    def test_force_l1_only_overrides(self) -> None:
        policy = BudgetPolicy()
        assert not policy.should_run_l2(
            tokens=100_000,
            call_class=CallClass.GENERIC,
            has_query=True,
            force_l1_only=True,
        )

    def test_custom_thresholds(self) -> None:
        policy = BudgetPolicy(l2_thresholds={CallClass.GENERIC: 5})
        assert policy.l2_threshold_for(CallClass.GENERIC) == 5


# ── Pipeline: L1-only paths ──────────────────────────────────────────────────
class TestL1OnlyPaths:
    def test_l1_only_when_no_router(self) -> None:
        result = compress("<p>hello</p>\n\n<p>world</p>", _ctx(query="hello"))
        assert result.stages_applied == [CompressionStage.L1_DETERMINISTIC]
        assert "<p>" not in result.compressed_text

    def test_l1_only_when_below_threshold(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        # Tiny payload — under every per-class threshold, so L2 is skipped.
        result = compress("short text", _ctx(query="match"), router=router)
        assert result.stages_applied == [CompressionStage.L1_DETERMINISTIC]

    def test_l1_only_when_force_flag(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        big = "\n\n".join(f"block {i} match" for i in range(200))
        result = compress(big, _ctx(query="match", force_l1_only=True), router=router)
        assert CompressionStage.L2_SEMANTIC not in result.stages_applied

    def test_returns_compression_result_type(self) -> None:
        result = compress("anything", _ctx())
        assert isinstance(result, CompressionResult)
        assert isinstance(result.audit, CompressionAudit)


# ── Pipeline: L2 path ────────────────────────────────────────────────────────
class TestL2Path:
    def _big_payload(self) -> str:
        # Many blank-line-delimited chunks, half containing 'match', sized to clear
        # the GENERIC threshold so policy elects L2.
        blocks = []
        for i in range(60):
            tag = "match" if i % 2 == 0 else "noise"
            blocks.append(f"chunk {i} {tag} " + "filler words here " * 5)
        return "\n\n".join(blocks)

    def test_l2_runs_and_routes(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        ctx = _ctx(query="match", top_k=5, call_class=CallClass.MEMORY_RECALL)
        result = compress(self._big_payload(), ctx, router=router)
        assert CompressionStage.L2_SEMANTIC in result.stages_applied
        # Only 'match' chunks should survive top-5.
        assert "match" in result.compressed_text
        assert "noise" not in result.compressed_text

    def test_l2_reduces_tokens_below_l1(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        ctx = _ctx(query="match", top_k=5, call_class=CallClass.MEMORY_RECALL)
        result = compress(self._big_payload(), ctx, router=router)
        assert result.tokens_out < result.tokens_in
        assert result.ratio < 1.0


# ── Pipeline: degraded path ──────────────────────────────────────────────────
class TestDegradedPath:
    def test_embedder_failure_degrades_to_l1(self) -> None:
        router = L2SemanticRouter(ExplodingEmbedder())
        big = "\n\n".join(f"chunk {i} match " + "filler " * 8 for i in range(60))
        ctx = _ctx(query="match", top_k=5, call_class=CallClass.MEMORY_RECALL)
        result = compress(big, ctx, router=router)

        assert CompressionStage.L2_DEGRADED in result.stages_applied
        assert CompressionStage.L2_SEMANTIC not in result.stages_applied
        assert result.audit.action_type == "compression.l2_degraded"
        assert result.audit.degraded_reason is not None
        # Still a valid result — never raised.
        assert isinstance(result, CompressionResult)

    def test_compress_never_raises(self) -> None:
        router = L2SemanticRouter(ExplodingEmbedder())
        for payload in ["", "{", "<<>>", "\x00", json.dumps({"a": None})]:
            result = compress(payload, _ctx(query="match"), router=router)
            assert isinstance(result, CompressionResult)


# ── Pipeline: audit emission ─────────────────────────────────────────────────
class TestAuditEmission:
    def test_audit_sink_invoked_with_projection(self) -> None:
        captured: list[CompressionAudit] = []
        compress("hello world", _ctx(), audit_sink=captured.append)
        assert len(captured) == 1
        assert captured[0].action_type == "compression.applied"

    def test_correlation_id_passes_through(self) -> None:
        cid = uuid4()
        captured: list[CompressionAudit] = []
        result = compress("hello", _ctx(correlation_id=cid), audit_sink=captured.append)
        assert result.audit.correlation_id == cid
        assert captured[0].correlation_id == cid

    def test_org_id_and_call_class_in_audit(self) -> None:
        result = compress(
            "payload", _ctx(org_id="org_42", call_class=CallClass.TOOL_RESULT)
        )
        assert result.audit.org_id == "org_42"
        assert result.audit.call_class == CallClass.TOOL_RESULT

    def test_audit_token_figures_match_result(self) -> None:
        result = compress("some content here", _ctx())
        assert result.audit.tokens_in == result.tokens_in
        assert result.audit.tokens_out == result.tokens_out
        assert result.audit.ratio == result.ratio

    def test_degraded_audit_emitted_via_sink(self) -> None:
        captured: list[CompressionAudit] = []
        router = L2SemanticRouter(ExplodingEmbedder())
        big = "\n\n".join(f"chunk {i} match " + "filler " * 8 for i in range(60))
        ctx = _ctx(query="match", call_class=CallClass.MEMORY_RECALL)
        compress(big, ctx, router=router, audit_sink=captured.append)
        assert captured[0].action_type == "compression.l2_degraded"

    def test_successful_l2_audit_emitted_via_sink(self) -> None:
        captured: list[CompressionAudit] = []
        router = L2SemanticRouter(KeywordEmbedder())
        big = "\n\n".join(
            f"chunk {i} {'match' if i % 2 == 0 else 'noise'} " + "filler " * 8
            for i in range(60)
        )
        ctx = _ctx(query="match", top_k=5, call_class=CallClass.MEMORY_RECALL)
        compress(big, ctx, router=router, audit_sink=captured.append)
        assert len(captured) == 1
        assert captured[0].action_type == "compression.applied"
        assert CompressionStage.L2_SEMANTIC in captured[0].stages_applied


# ── Pipeline: measurement integrity ──────────────────────────────────────────
class TestMeasurement:
    def test_duration_is_recorded(self) -> None:
        result = compress("content", _ctx())
        assert result.duration_ms >= 0.0

    def test_stages_applied_ordered(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        big = "\n\n".join(f"chunk {i} match " + "filler " * 8 for i in range(60))
        ctx = _ctx(query="match", call_class=CallClass.MEMORY_RECALL)
        result = compress(big, ctx, router=router)
        assert result.stages_applied[0] == CompressionStage.L1_DETERMINISTIC
