"""
Pydantic payload models — the leaf package of the dependency graph.

`schemas/` is imported by every other package and imports none of them.
It carries the canonical wire shapes: the event envelope (`base.py`), the
six event categories (`events/`), and the typed agent I/O models (`agents/`).
"""
