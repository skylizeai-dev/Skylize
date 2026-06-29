"""Model Context Engine — context compression proxy (L1 + L2).

A three-tier compression proxy that sits between agent context assembly and the
LLM Gateway egress, shrinking tool payloads and memory recall before they enter
the model context window. This sprint ships:

  - L1 — deterministic pruning (`l1_deterministic`): HTML→Markdown, null/base64
    stripping, whitespace collapse, string truncation. Pure Python, <10ms.
  - L2 — semantic routing (`l2_semantic`): top_k chunk selection by cosine
    similarity to a query, against an injected `Embedder`. Degrades, never blocks.
  - the budget policy + tiktoken token accounting (`budget`).
  - the orchestrating pipeline (`pipeline.compress`).

L3 (model-assisted summarization) is OUT of scope: `protocols.L3CompressorProtocol`
is the documented seam, with no implementation. The concrete MiniLM embedder is a
Sprint-2 deliverable wired when the memory embedding service lands.

This package is a pure-inner module: it imports no database driver and no vendor
LLM SDK, depending instead on the `Embedder` / `EmbeddingCache` ports.
"""

from __future__ import annotations

from skylize.memory.compression.budget import (
    BudgetPolicy,
    compute_ratio,
    count_tokens,
)
from skylize.memory.compression.l1_deterministic import compress_l1
from skylize.memory.compression.l2_semantic import (
    L2RouteResult,
    L2SemanticRouter,
    chunk_cache_key,
    cosine_similarity,
)
from skylize.memory.compression.pipeline import AuditSink, compress
from skylize.memory.compression.protocols import (
    Embedder,
    EmbeddingCache,
    L3CompressorProtocol,
)

__all__ = [
    "AuditSink",
    "BudgetPolicy",
    "Embedder",
    "EmbeddingCache",
    "L2RouteResult",
    "L2SemanticRouter",
    "L3CompressorProtocol",
    "chunk_cache_key",
    "compress",
    "compress_l1",
    "compute_ratio",
    "cosine_similarity",
    "count_tokens",
]
