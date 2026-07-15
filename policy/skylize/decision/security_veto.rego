# PLACEHOLDER — real policy content pending business spec from owner.
# Tracking: docs/04_decision_engine/guardrails.md §3 (policy class: security_veto) ·
#           authored on branch feat/opa-infra-skeleton (OPA infra skeleton PR).
#
# This is a fail-closed SKELETON, not a real policy. It exists only so the OPA
# server infrastructure is testable without inventing business logic. It answers
# the "security_veto" question — "has a security/safety agent rejected this?" —
# with an unconditional DENY. Fail-closed until authored. (A real security veto
# is never outranked by hierarchy — guardrails.md §5.)
package skylize.decision.security_veto

# Default deny — absence of an explicit allow is a denial (guardrails.md §4).
# No rule below sets `allow := true`, so this veto class defaults to blocking.
default allow := false

# Every evaluation contributes a placeholder denial so a live query returns a
# real, non-empty deny reason rather than silence.
deny_reasons contains "PLACEHOLDER security_veto policy: no rule authored — fail-closed default deny (see docs/04_decision_engine/guardrails.md §3)"
