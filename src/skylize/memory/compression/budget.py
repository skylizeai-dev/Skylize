"""Token counting and the L2 routing policy.

tiktoken `cl100k_base` is the ground-truth tokenizer (acceptance criteria): every
`tokens_in` / `tokens_out` / `ratio` figure in a `CompressionResult` is measured
with it — no custom length heuristics. The encoder is loaded once and reused.

The policy is a pure decision: given a payload's token count and the call class,
decide whether the L2 semantic stage is worth its 50–200ms latency, or whether
L1-only suffices. Small payloads skip L2.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from skylize.schemas.compression import CallClass

# The single tokenizer of record. cl100k_base is the encoding behind the models
# the gateway routes to; using it here makes our budget accounting match the
# provider's so the run ledger and our ratios agree.
ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    """Load the cl100k_base encoder once per process.

    Cached so the BPE table is built a single time; tiktoken caches the table on
    disk after first load, so steady-state callers pay nothing.
    """
    return tiktoken.get_encoding(ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Return the cl100k_base token count of `text`.

    The empty string is zero tokens. `disallowed_special=()` ensures sequences
    that look like special tokens (e.g. ``<|endoftext|>`` appearing in scraped
    payloads) are encoded as ordinary text rather than raising.
    """
    if not text:
        return 0
    return len(_encoder().encode(text, disallowed_special=()))


def compute_ratio(tokens_in: int, tokens_out: int) -> float:
    """tokens_out / tokens_in, defined as 1.0 when the input is empty.

    A ratio of 1.0 means "no reduction"; < 1.0 means the payload shrank. Empty in
    cannot shrink, so the neutral 1.0 avoids a divide-by-zero and reads correctly
    in the audit trail.
    """
    if tokens_in <= 0:
        return 1.0
    return tokens_out / tokens_in


# Per-call-class token thresholds. Below the threshold, L2's latency is not worth
# the marginal token savings, so the pipeline runs L1-only. These are deliberate
# defaults, overridable per policy instance.
_DEFAULT_L2_THRESHOLDS: dict[CallClass, int] = {
    CallClass.TOOL_RESULT: 512,
    CallClass.MEMORY_RECALL: 256,
    CallClass.PROMPT_CONTEXT: 1024,
    CallClass.GENERIC: 512,
}


@dataclass(frozen=True)
class BudgetPolicy:
    """Decides whether the L2 semantic stage runs for a given payload.

    Frozen and pure: the same inputs always yield the same decision, so policy is
    trivially testable and the audit trail is reproducible.
    """

    l2_thresholds: dict[CallClass, int] | None = None

    def l2_threshold_for(self, call_class: CallClass) -> int:
        """The token count at/above which L2 is worthwhile for this class."""
        table = self.l2_thresholds or _DEFAULT_L2_THRESHOLDS
        return table.get(call_class, _DEFAULT_L2_THRESHOLDS[CallClass.GENERIC])

    def should_run_l2(
        self,
        *,
        tokens: int,
        call_class: CallClass,
        has_query: bool,
        force_l1_only: bool,
    ) -> bool:
        """Return True iff the L2 semantic stage should run.

        L2 runs only when all hold:
          - the caller did not force L1-only;
          - a routing query was supplied (L2 scores chunks against intent — with
            no query there is nothing to route toward);
          - the post-L1 token count meets the per-class threshold.
        """
        if force_l1_only:
            return False
        if not has_query:
            return False
        return tokens >= self.l2_threshold_for(call_class)
