"""The one governed tool that stops a customer's VM and drops its external IP.

THE ONLY EXTERNALLY-MUTATING GCP VERB IN THE REGISTRY. There is deliberately no
disk verb, no `addresses.delete`, and no project-level verb; see
`app/gcp/actions.py` for why each is excluded rather than merely absent.

HOW IT IS GOVERNED, and why none of it lives in this handler
------------------------------------------------------------
Every check that decides WHETHER this may run happens before the handler is
entered, at the `ToolProxy` gates (tools/proxy.py):

  * token validation  - signature, expiry, revocation, scope, live-state. The
    platform kill switch composes here: engaging it revokes the governance token
    and this tool becomes uninvokable, which is the correct direction to fail.
  * the `wif` gate    - a connection exists, its `connection_state` is 'valid'
    (so a federation the health probe already found broken is refused before
    anything is minted), and the requested instance is an ENABLED row in the
    customer's own `gcp_wif_targets` allow-list.

The handler therefore does no authorization of its own. What it DOES require is
`ToolContext.hitl_id`, and that requirement is load-bearing rather than
defensive: it is the only value on this path that survives a HITL retry, so it
is the only honest basis for the provider-side idempotency this action depends
on. `GcpKillSwitchExecutor` refuses without it.

NOT GRANTED TO ANY STATELESS AGENT. `cfo_agent` and the four safety agents may
observe an overspend and PROPOSE; the proposal then goes through the same
decision gate as any other action and a human approves it. They never hold this
tool, and `tests/contract/test_stateless_agents_no_oauth_access.py` asserts it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...app.gcp.actions import (
    DEFAULT_ACCESS_CONFIG,
    DEFAULT_NETWORK_INTERFACE,
    GcpActionError,
    GcpKillSwitchExecutor,
    MissingIdempotencyAnchor,
)
from ...dal.gcp_wif import GcpWifRepository
from ..base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolWifNotConnected,
    ToolWifProfile,
)

GCP_STOP_INSTANCE_TOOL_ID = "integration.gcp_stop_instance"


class StopInstanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1, description="GCP project id owning the instance")
    zone: str = Field(min_length=1, description="Zone of the instance, e.g. us-central1-a")
    instance: str = Field(min_length=1, description="Name of the instance to stop")
    reason: str = Field(
        min_length=1, max_length=2000,
        description="Why this machine is being stopped. Recorded in the audit trail.",
    )
    access_config: str = Field(
        default=DEFAULT_ACCESS_CONFIG,
        description="Name of the external-IP access config to remove.",
    )
    network_interface: str = Field(
        default=DEFAULT_NETWORK_INTERFACE,
        description="Network interface carrying the access config.",
    )


class StopInstanceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: True ONLY when both steps succeeded. A partial result reports False here
    #: and describes what actually happened in `summary` - never a bare failure,
    #: because "stopped but IP still attached" is not the same as "nothing
    #: happened" and the operator must be able to tell them apart.
    fully_succeeded: bool
    stopped: bool
    ip_released: bool
    partial: bool
    summary: str


def build_gcp_tools(
    *,
    wif_repo: GcpWifRepository | None,
    executor_factory: Any,
) -> list[ToolDefinition]:
    """Return the GCP tool list, or EMPTY when the feature is not wired.

    Returning nothing when `wif_repo` or the executor is absent is what keeps
    every existing deployment byte-identical: the tool is not registered, so it
    cannot be resolved, granted, or invoked. Same shape as the Drive/Asana/Notion
    connectors, which register only when their platform credentials exist.
    """
    if wif_repo is None or executor_factory is None:
        return []

    async def _stop_instance(
        data: BaseModel, ctx: ToolContext
    ) -> StopInstanceOutput:
        assert isinstance(data, StopInstanceInput)

        # The gate already proved a valid connection exists; re-read it because
        # the handler needs the row's federation coordinates, not to re-authorize.
        row = await wif_repo.get(ctx.org_id, "")
        if row is None:  # pragma: no cover - the gate refuses this first
            raise ToolWifNotConnected(
                f"org {ctx.org_id!r} has no GCP federation configured"
            )

        executor: GcpKillSwitchExecutor = executor_factory()
        try:
            outcome = await executor.stop_and_release(
                row=row,
                project=data.project,
                zone=data.zone,
                instance=data.instance,
                # NOT ctx.correlation_id. See ToolContext.hitl_id and
                # app/gcp/actions.py: correlation_id is minted fresh on every
                # approval attempt, so keying idempotency on it would defeat the
                # deduplication on exactly the retry path it exists to protect.
                hitl_id=ctx.hitl_id,
                access_config=data.access_config,
                network_interface=data.network_interface,
            )
        except MissingIdempotencyAnchor as exc:
            # Surfaced as a tool execution failure rather than a denial: nothing
            # about the customer's authorization is wrong, the call simply cannot
            # be made safely outside an approved HITL replay.
            raise ToolExecutionError(str(exc)) from exc
        except GcpActionError as exc:
            raise ToolExecutionError(f"GCP kill switch failed: {exc}") from exc

        return StopInstanceOutput(
            fully_succeeded=outcome.fully_succeeded,
            stopped=outcome.stopped,
            ip_released=outcome.ip_released,
            partial=outcome.partial,
            summary=outcome.summary(),
        )

    return [
        ToolDefinition(
            tool_id=GCP_STOP_INSTANCE_TOOL_ID,
            name="Stop GCP instance and release its external IP",
            description=(
                "Stop a specific Google Compute Engine VM and remove its external "
                "IP access config. Use ONLY to contain a runaway or overspending "
                "workload on infrastructure the customer has explicitly listed as "
                "stoppable. The instance can be restarted by the customer; the "
                "external IP is not restored automatically."
            ),
            input_schema=StopInstanceInput,
            output_schema=StopInstanceOutput,
            category="integration",
            handler=_stop_instance,
            # The fourth gate. Names the fields the proxy reads off the VALIDATED
            # input to check the customer's own target allow-list, so what this
            # tool may touch is inspectable in the registry entry rather than
            # buried in the handler.
            wif=ToolWifProfile(
                label="",
                project_field="project",
                zone_field="zone",
                instance_field="instance",
            ),
            # NO ToolSpendProfile, deliberately. Stopping a customer's VM costs
            # Skylize nothing, so there is no ceiling to reserve against. The
            # spend ceiling is what TRIGGERS a proposal to run this tool (see
            # app/gcp/trigger.py); it is not a resource this tool consumes, and
            # conflating the two would put a hold on the customer's LLM budget
            # for a safety action.
        )
    ]
