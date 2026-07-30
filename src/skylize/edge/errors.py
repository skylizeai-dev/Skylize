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
