"""A REAL local HTTP server implementing the Anthropic Messages contract.

Why this exists (the key-ready foundation): the project's definition of
"key-ready" is that when a real API key is inserted, NO code path executes for
the first time. Every retry / failure test used to mock the SDK client, so the
SDK's own HTTP layer, response parsing, and error mapping had NEVER RUN. This
fake is served on an ephemeral port and reached by pointing the adapter's
``base_url`` at it, so the Anthropic SDK genuinely opens a socket, sends a real
request, parses a real response, and maps real error statuses.

Public surface:
  * ``FakeProvider`` — the programmable control object + request recorder.
  * response factories: ``success`` / ``status`` / ``hang`` / ``malformed``.
  * ``running_fake_provider()`` — a context manager that serves the app with
    uvicorn on an ephemeral 127.0.0.1 port and yields ``(base_url, FakeProvider)``.
"""

from .app import (
    Behavior,
    FakeProvider,
    RecordedRequest,
    build_app,
    hang,
    malformed,
    status,
    success,
)
from .server import running_fake_provider

__all__ = [
    "Behavior",
    "FakeProvider",
    "RecordedRequest",
    "build_app",
    "hang",
    "malformed",
    "status",
    "success",
    "running_fake_provider",
]
