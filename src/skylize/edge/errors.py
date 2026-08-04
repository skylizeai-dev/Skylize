"""Machine-readable refusal codes for the edge boundary.

WHY THIS EXISTS. In a governance product the reason for a refusal has to be
readable by a machine, not only by a person. Before this module every route
raised a bare ``fastapi.HTTPException``; no custom exception handler existed
anywhere in ``src/`` (so FastAPI's default ``http_exception_handler`` shaped
every error as ``{"detail": "<string>"}``); and the ONLY discriminator between
three completely different 403s was a message PREFIX:

  * ``"decision rejected: ..."``  — the decision engine returned a REJECT verdict
  * ``"governance denied: ..."``  — a platform control (kill switch / suspension)
  * ``"requires one of roles: ..."`` — the CALLER is not authorized at all

The console could not tell them apart, so an operator whose service credential
lacked the required role was shown a decision-engine REJECTED verdict: a
configuration error presented as a governance decision.

THE CONTRACT. ``detail`` keeps its exact current type (``str``) and its exact
current value; a stable ``code`` is added as a SIBLING key of the response body.
Every existing consumer that reads a string ``detail`` (e.g.
``website/src/lib/skylize/client.ts:48-51``) is byte-for-byte unaffected, and a
consumer that does not know about ``code`` simply ignores the extra key.

    {"detail": "decision rejected: ...", "code": "decision_rejected"}

SCOPE. Only ``CodedHTTPException`` is registered with the app, so a plain
``HTTPException`` still travels FastAPI's own default handler and still
serializes as ``{"detail": ...}`` with nothing added. Adopting a code is
therefore opt-in per raise site, and no uncoded route changes shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.requests import Request
from starlette.responses import Response


class ErrorCode(str, Enum):
    """The CLOSED vocabulary of machine-readable refusal causes.

    A closed set (not free strings) is the point: a client may switch on these
    exhaustively. Members are added deliberately, one per distinguishable cause;
    a cause whose correct value is ambiguous is left uncoded rather than guessed.

    ``str`` mixin so ``ErrorCode.X == "x"`` and JSON serialization are trivial.
    """

    #: The decision engine evaluated the proposal and returned REJECT. This is a
    #: governance VERDICT about the request (app/agents/execution.py
    #: AgentGovernanceRejected). Nothing executed: no LLM call, no deliverable,
    #: no ledger row.
    DECISION_REJECTED = "decision_rejected"

    #: A platform control forbade the action before any decision was evaluated —
    #: a kill switch (platform / tenant / agent) or an agent suspension
    #: (app/governance/authority.py GovernanceDenied, raised from
    #: ``assert_active``). NOT a verdict about the request's content; the
    #: ``detail`` names which control (app/governance/snapshot.py:53-63).
    GOVERNANCE_DENIED = "governance_denied"

    #: The CALLER is not authorized to invoke this route at all: the presented
    #: credential carries none of the roles the route requires (edge/deps.py
    #: ``require_role`` / ``require_any_role``). Nothing about governance was
    #: consulted — the request never reached the handler.
    AUTHORIZATION_FAILED = "authorization_failed"

    #: The caller's ROLE was sufficient to reach the handler, but the HUMAN
    #: PRINCIPAL behind the request does not hold the authority the action needs
    #: (app/principal/errors.py ``PrincipalError`` — no principal record, a
    #: suspended one, a scope outside their compiled grants, or an authority that
    #: could not be established at all).
    #:
    #: A distinct code because none of the three existing 403s describes it and
    #: the remedy is different from all of them: this is not a decision verdict
    #: about the request, not a platform control, and not a missing role. It says
    #: "this person may not do this", and the fix is a grant, not a redeploy or a
    #: role change. Added deliberately, per this enum's one-member-per-cause rule.
    PRINCIPAL_AUTHORITY_DENIED = "principal_authority_denied"

    #: The org's spend ceiling for the current billing period would be breached
    #: by this call, so it was refused BEFORE any provider egress
    #: (adapters/llm/spend_ceiling.py ``OrgSpendCeilingExceeded``). Nothing was
    #: sent, nothing was charged. The customer's remedy is to raise the ceiling
    #: or wait for the next period.
    SPEND_CEILING_EXCEEDED = "spend_ceiling_exceeded"

    #: The same refusal, different cause: NO ceiling row exists for this org and
    #: period, and the gate fails closed rather than treating "unset" as
    #: "unlimited". A distinct code because the remedy is completely different —
    #: an operator must provision the ceiling; the customer has not overspent.
    #: This is what a brand-new org hits on its first call.
    SPEND_CEILING_NOT_CONFIGURED = "spend_ceiling_not_configured"

    #: The deployment has no ``model_pricing`` row for the concrete model the
    #: agent would call, so the call was refused before egress rather than
    #: producing an unrecordable charge (adapters/llm/gateway.py
    #: ``LLMModelNotPriced``). A SERVER-side provisioning fault, not anything the
    #: caller did.
    MODEL_NOT_PRICED = "model_not_priced"

    #: The LLM provider could not be reached, or returned a body that is not a
    #: parseable Message. An upstream failure; nothing the caller can fix.
    PROVIDER_UNAVAILABLE = "provider_unavailable"

    #: The LLM provider did not answer within the configured timeout. Deliberately
    #: distinct from PROVIDER_UNAVAILABLE: a timed-out call may have COMPLETED and
    #: been billed by the provider, so it is never retried.
    PROVIDER_TIMEOUT = "provider_timeout"

    #: Registration was refused because the requested ``org_id`` cannot be used
    #: to create a NEW organisation — either it already has at least one user, or
    #: a concurrent registration won the owner race. Registration is the only
    #: org-creating path and it creates new orgs only
    #: (app/auth/user_service.py). The wire value and the human message are
    #: deliberately non-confirming: they say the identifier is unavailable, not
    #: that a tenant with that id exists.
    ORG_NOT_AVAILABLE = "org_not_available"


class CodedHTTPException(HTTPException):
    """An ``HTTPException`` that also carries a stable ``ErrorCode``.

    ``status_code``, ``detail`` and ``headers`` behave exactly as on the base
    class — this only adds ``code``. Raising it instead of ``HTTPException`` is
    the whole adoption step for a route.
    """

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        code: ErrorCode,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


async def coded_http_exception_handler(request: Request, exc: Exception) -> Response:
    """Serialize a ``CodedHTTPException`` as ``{"detail": ..., "code": ...}``.

    Mirrors FastAPI's own ``http_exception_handler`` (headers forwarded, no body
    for statuses that forbid one) and only ADDS the ``code`` key, so a coded
    response differs from an uncoded one by exactly that key.
    """
    if not isinstance(exc, CodedHTTPException):  # pragma: no cover - defensive
        raise exc
    headers = getattr(exc, "headers", None)
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)
    body: dict[str, Any] = {"detail": exc.detail, "code": exc.code.value}
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    """Register the coded handler.

    Registered for ``CodedHTTPException`` ONLY. Starlette resolves a handler by
    walking ``type(exc).__mro__``, so a ``CodedHTTPException`` finds this handler
    first while a plain ``HTTPException`` still finds FastAPI's default — which
    is why no existing route's response shape moves.
    """
    app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)
