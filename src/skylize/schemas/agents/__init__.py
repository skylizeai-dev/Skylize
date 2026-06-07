"""
Typed agent I/O models referenced by `AgentContract.input_schema` /
`output_schema` (dotted-path strings resolved at runtime by the Orchestrator).

These are the validated shapes an agent consumes as work and produces as
output; the Orchestrator wraps the output into the appropriate event.
"""
