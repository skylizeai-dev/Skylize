"""
Application Boundary — the brain: Orchestrator, Governance Authority, Decision
Engine, and Audit. The only layer permitted to mint tokens, resolve contracts,
and authorize data access. Depends on the DAL *ports*, never on SQL.
"""
