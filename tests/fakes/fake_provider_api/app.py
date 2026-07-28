"""The ASGI app + programmable control object for the fake Anthropic Messages API.

Contract implemented (non-streaming only — streaming is out of MVP scope per
gateway.py and is deliberately NOT served here):

  POST /v1/messages
    * success  -> a well-formed non-streaming Message: a stable ``id``, a
      ``model`` field (echoing the request's resolved model id), a real
      ``usage`` block, and either a text block or a ``tool_use`` block.
    * status   -> an Anthropic-shaped error body at a chosen status code
      (429/500/400/401/...) with arbitrary response headers (Retry-After,
      x-should-retry, ...).
    * hang     -> sleep long enough to trip the client's read timeout.
    * malformed-> a truncated/!JSON 200 body, to exercise the SDK's own parser.

Selection is PROGRAMMABLE PER REQUEST: a test calls ``fake.program(b1, b2, ...)``
and each incoming request pops the next ``Behavior`` (the last one repeats once
the script is exhausted; the default is ``success()``). Every request is recorded
(method, path, headers, raw + parsed body) so a test can assert exactly what was
sent and, crucially, HOW MANY attempts arrived at the socket.

The recorder and script are guarded by a lock because uvicorn serves the app in a
separate thread from the test.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route


# ---------------------------------------------------------------------------
# Recorded request + programmable behavior
# ---------------------------------------------------------------------------


@dataclass
class RecordedRequest:
    """One request the SDK actually put on the wire, captured server-side."""

    method: str
    path: str
    headers: dict[str, str]
    raw_body: bytes
    json_body: dict[str, Any] | None


@dataclass
class Behavior:
    """One programmed response for the next incoming POST /v1/messages."""

    kind: str  # "success" | "status" | "hang" | "malformed"

    # -- success --
    text: str = "ok"
    tool_use: dict[str, Any] | None = None  # {"id","name","input"} -> tool_use block
    input_tokens: int = 11
    output_tokens: int = 7
    stop_reason: str | None = None  # None -> "tool_use" if tool_use else "end_turn"
    model: str | None = None  # None -> echo the request's model id
    message_id: str = "msg_fake_0001"

    # -- status error --
    status_code: int = 200
    error_type: str = "api_error"
    error_message: str = "fake provider error"
    response_headers: dict[str, str] = field(default_factory=dict)

    # -- hang --
    hang_seconds: float = 30.0

    # -- malformed --
    raw: bytes = b'{"id": "msg_trunc", "type": "message", "content": [{"type": "text",'


def success(
    *,
    text: str = "ok",
    tool_use: dict[str, Any] | None = None,
    input_tokens: int = 11,
    output_tokens: int = 7,
    stop_reason: str | None = None,
    model: str | None = None,
    message_id: str = "msg_fake_0001",
) -> Behavior:
    """A well-formed non-streaming Message response."""
    return Behavior(
        kind="success",
        text=text,
        tool_use=tool_use,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
        model=model,
        message_id=message_id,
    )


def status(
    code: int,
    *,
    error_type: str = "api_error",
    message: str = "fake provider error",
    headers: dict[str, str] | None = None,
) -> Behavior:
    """An Anthropic-shaped error at ``code`` with optional response headers.

    ``headers`` may carry ``retry-after`` and/or ``x-should-retry`` — the latter
    is honoured by the real SDK (``x-should-retry: false`` suppresses the SDK's
    OWN internal retry, which isolates the adapter's retry policy for a test).
    """
    return Behavior(
        kind="status",
        status_code=code,
        error_type=error_type,
        error_message=message,
        response_headers=dict(headers or {}),
    )


def hang(seconds: float = 30.0) -> Behavior:
    """Accept the request, then sleep — long enough to trip a client read timeout."""
    return Behavior(kind="hang", hang_seconds=seconds)


def malformed(raw: bytes | None = None) -> Behavior:
    """A 200 response whose body is truncated / not valid JSON."""
    b = Behavior(kind="malformed")
    if raw is not None:
        b.raw = raw
    return b


# ---------------------------------------------------------------------------
# The programmable control object + request recorder
# ---------------------------------------------------------------------------


class FakeProvider:
    """Shared, thread-safe state between the test and the served app."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: list[RecordedRequest] = []
        self._script: list[Behavior] = []
        self._default = success()

    # -- programming --
    def program(self, *behaviors: Behavior) -> None:
        """Set the ordered script of responses (one per incoming request)."""
        with self._lock:
            self._script = list(behaviors)
            self._requests = []

    def reset(self) -> None:
        with self._lock:
            self._script = []
            self._requests = []

    # -- recorder --
    @property
    def requests(self) -> list[RecordedRequest]:
        with self._lock:
            return list(self._requests)

    @property
    def attempts(self) -> int:
        """How many requests actually reached the socket."""
        with self._lock:
            return len(self._requests)

    @property
    def message_requests(self) -> list[RecordedRequest]:
        with self._lock:
            return [r for r in self._requests if r.path == "/v1/messages"]

    # -- internal --
    def _record(self, rr: RecordedRequest) -> None:
        with self._lock:
            self._requests.append(rr)

    def _next_behavior(self) -> Behavior:
        with self._lock:
            if len(self._script) > 1:
                return self._script.pop(0)
            if len(self._script) == 1:
                return self._script[0]  # last entry repeats
            return self._default


# ---------------------------------------------------------------------------
# Response construction
# ---------------------------------------------------------------------------


def _message_json(behavior: Behavior, req_model: str | None) -> dict[str, Any]:
    model = behavior.model or req_model or "fake-unknown-model"
    content: list[dict[str, Any]]
    if behavior.tool_use is not None:
        content = [
            {
                "type": "tool_use",
                "id": behavior.tool_use["id"],
                "name": behavior.tool_use["name"],
                "input": behavior.tool_use.get("input", {}),
            }
        ]
        stop = behavior.stop_reason or "tool_use"
    else:
        content = [{"type": "text", "text": behavior.text}]
        stop = behavior.stop_reason or "end_turn"
    return {
        "id": behavior.message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": behavior.input_tokens,
            "output_tokens": behavior.output_tokens,
        },
    }


async def _handle_messages(request: Request, fake: FakeProvider) -> Response:
    raw = await request.body()
    try:
        json_body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        json_body = None
    fake._record(
        RecordedRequest(
            method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            raw_body=raw,
            json_body=json_body,
        )
    )

    behavior = fake._next_behavior()
    req_model = json_body.get("model") if isinstance(json_body, dict) else None

    if behavior.kind == "hang":
        await asyncio.sleep(behavior.hang_seconds)
        return JSONResponse(_message_json(success(), req_model))

    if behavior.kind == "malformed":
        # A 200 whose body is truncated JSON: the SDK must run its OWN parser.
        return Response(
            content=behavior.raw, status_code=200, media_type="application/json"
        )

    if behavior.kind == "status":
        body = {
            "type": "error",
            "error": {"type": behavior.error_type, "message": behavior.error_message},
        }
        return JSONResponse(
            body, status_code=behavior.status_code, headers=behavior.response_headers
        )

    # success
    return JSONResponse(_message_json(behavior, req_model))


def build_app(fake: FakeProvider) -> Starlette:
    """An ASGI app serving the Anthropic Messages contract backed by ``fake``."""

    async def messages(request: Request) -> Response:
        return await _handle_messages(request, fake)

    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok")

    return Starlette(
        routes=[
            Route("/v1/messages", messages, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
        ]
    )
