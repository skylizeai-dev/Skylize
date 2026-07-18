# PLACEHOLDER — real policy content pending business spec from owner.
# Tracking: docs/04_decision_engine/guardrails.md §5 (evaluation contract) ·
#           authored on branch feat/opa-infra-skeleton (OPA infra skeleton PR).
#
# Aggregate decision entrypoint. This is the document OPAClient queries
# (data.skylize.decision) and it returns {allow, deny_reasons}. It is a
# fail-closed SKELETON, not a real policy — it contains NO business logic and
# can never approve anything. Fail-closed until the per-class rules are authored.
package skylize.decision

# Default deny — absence of an explicit allow is a denial (guardrails.md §4).
# Nothing below can flip this to true, so the platform is fail-closed by default:
# no action can be approved until real policy content is authored per class.
default allow := false

# Surface every policy class's placeholder denial through the aggregate document
# so a live evaluation returns a real, non-empty {allow: false, deny_reasons: [...]}.
# Each class is referenced by its full path (a sibling sub-package), never via the
# parent, so this rule is not self-recursive.
deny_reasons contains msg if { some msg in data.skylize.decision.authority.deny_reasons }
deny_reasons contains msg if { some msg in data.skylize.decision.spend.deny_reasons }
deny_reasons contains msg if { some msg in data.skylize.decision.external_action.deny_reasons }
deny_reasons contains msg if { some msg in data.skylize.decision.brand_legal.deny_reasons }
deny_reasons contains msg if { some msg in data.skylize.decision.security_veto.deny_reasons }
deny_reasons contains msg if { some msg in data.skylize.decision.data_access.deny_reasons }
