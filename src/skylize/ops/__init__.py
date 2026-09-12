"""Operational entrypoints — run by a human operator, never by the request path.

Modules here are one-shot administrative commands executed against a reachable
database, in the shape of ``python -m skylize.ops.<command>``. They are the
deliberate counterpart to ``edge`` (the HTTP surface): anything that must NOT be
reachable over HTTP lives here instead of behind a route flag.

They live under ``src/`` rather than ``scripts/`` because CI's lint and type
gates are scoped to ``src`` and ``tests`` (``ruff check src tests``, ``mypy src``
-- scripts/ci_unit_gate.ps1), and code that mints credentials has to be inside
those gates.
"""
