"""Serve the fake provider app with uvicorn on an ephemeral 127.0.0.1 port.

A REAL server in a daemon thread, not an in-process transport shim: the SDK under
test connects to it over an actual TCP socket. ``running_fake_provider`` yields
the ``base_url`` to hand to the adapter and the ``FakeProvider`` control object.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator

import uvicorn

from .app import FakeProvider, build_app


def _free_port() -> int:
    """Grab an unused ephemeral TCP port on the loopback interface."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@contextlib.contextmanager
def running_fake_provider(
    *, startup_timeout: float = 15.0
) -> Iterator[tuple[str, FakeProvider]]:
    """Run the fake provider; yield ``(base_url, FakeProvider)``.

    The base_url is ``http://127.0.0.1:<port>`` — the Anthropic SDK appends
    ``/v1/messages`` itself (verified against the installed SDK), so the fake
    serves that exact path.
    """
    fake = FakeProvider()
    app = build_app(fake)
    port = _free_port()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="fake-anthropic", daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            thread.join(timeout=5.0)
            raise RuntimeError("fake provider server did not start in time")
        time.sleep(0.02)

    try:
        yield f"http://127.0.0.1:{port}", fake
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
