# PLACEHOLDER — real policy content pending business spec from owner.
# Tracking: docs/04_decision_engine/guardrails.md §3 (policy class: brand_legal) ·
#           authored on branch feat/opa-infra-skeleton (OPA infra skeleton PR).
#
# This is a fail-closed SKELETON, not a real policy. It exists only so the OPA
# server infrastructure is testable without inventing business logic. It answers
# the "brand_legal" question — "is the content brand/legal/compliance-sensitive?"
# — with an unconditional DENY. Fail-closed until authored.
package skylize.decision.brand_legal

# Default deny — absence of an explicit allow is a denial (guardrails.md §4).
# No rule below sets `allow := true`, so no content can be cleared here.
default allow := false

# Every evaluation contributes a placeholder denial so a live query returns a
# real, non-empty deny reason rather than silence.
deny_reasons contains "PLACEHOLDER brand_legal policy: no rule authored — fail-closed default deny (see docs/04_decision_engine/guardrails.md §3)"
