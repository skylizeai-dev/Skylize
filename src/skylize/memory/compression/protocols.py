"""Compression seams — the injected ports the pipeline depends on.

The compression package stays a pure-inner module (the import-linter "no database
driver" contract covers `skylize.memory`): it never imports a model runtime, a
Redis client, or a vendor SDK. Instead it depends on these `Protocol`s and the
caller injects concretes at the composition root.

  - `Embedder`            — turns text into vectors. Sprint 2 wires a concrete
                            MiniLM adapter when the memory embedding service lands;
                            until then L2 is exercised against a fake in tests.
  - `EmbeddingCache`      — optional SHA-256→vector cache (Redis-backed in prod).
                            L2 takes `cache: EmbeddingCache | None`; `None` means
                            compute-through with no caching.
  - `L3CompressorProtocol`— the documented seam for the out-of-scope L3 tier.
                            No implementation ships this sprint.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Maps a batch of text chunks to dense vectors.

    Batch-in / batch-out so an implementation can vectorize in one model call.
    The returned list is parallel to `texts` (index i → vector for texts[i]).
    Implementations are expected to be deterministic for a given input so the
    embedding cache is sound.
    """

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in input order."""
        ...


@runtime_checkable
class EmbeddingCache(Protocol):
    """A keyed vector cache. Keys are SHA-256 hex digests of the chunk text.

    `get` returns the cached vector or `None` on miss. `set` stores a vector with
    a TTL (24h in the L2 spec). Both are sync — the prod Redis adapter wraps a
    sync client; the pipeline's `compress` is sync on the CPU-bound path.
    """

    def get(self, key: str) -> list[float] | None:
        """Return the cached vector for `key`, or None on miss."""
        ...

    def set(self, key: str, value: list[float], ttl_seconds: int) -> None:
        """Store `value` under `key` with a time-to-live."""
        ...


@runtime_checkable
class L3CompressorProtocol(Protocol):
    """Seam for the L3 (model-assisted summarization) tier — OUT OF SCOPE.

    Documented here so the pipeline has a typed extension point, but no concrete
    L3 compressor is implemented this sprint. A future tier implements `compress`
    to abstractively summarize content that survived L1/L2 when the payload is
    still over budget.
    """

    def compress(self, text: str, *, target_tokens: int) -> str:
        """Reduce `text` toward `target_tokens`, preserving meaning."""
        ...
