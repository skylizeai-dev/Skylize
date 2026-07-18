# PLACEHOLDER — real policy content pending business spec from owner.
# Tracking: docs/04_decision_engine/guardrails.md §3 (policy class: authority) ·
#           authored on branch feat/opa-infra-skeleton (OPA infra skeleton PR).
#
# This is a fail-closed SKELETON, not a real policy. It exists only so the OPA
# server infrastructure is testable without inventing business logic. It answers
# the "authority" question — "is the proposer's authority_level >= the action's
# required level?" — with an unconditional DENY. Fail-closed until authored.
package skylize.decision.authority

# Default deny — absence of an explicit allow is a denial (guardrails.md §4).
# No rule below sets `allow := true`, so this class can never approve anything.
default allow := false

# Every evaluation contributes a placeholder denial so a live query returns a
# real, non-empty deny reason rather than silence.
deny_reasons contains "PLACEHOLDER authority policy: no rule authored — fail-closed default deny (see docs/04_decision_engine/guardrails.md §3)"
