"""
IF-INTEGRATION: the only boundary with outbound egress and credentials.

Adapters are the sole holders of provider secrets and the sole egress to
external systems. Agents never reach this package (enforced by import-linter).
Foundation scope here: the LLM Gateway port/specification only.
"""
