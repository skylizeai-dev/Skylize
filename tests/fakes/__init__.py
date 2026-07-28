"""Test fakes that stand in for external services over a REAL socket.

These are NOT mocks: each fake is served by a real ASGI server on an ephemeral
port so the client SDK under test opens a genuine HTTP connection to it. See
``fake_provider_api`` for the Anthropic Messages contract fake.
"""
