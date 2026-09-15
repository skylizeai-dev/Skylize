"""Install (or remove) the Temporal Schedule that fires an agent's autonomous runs.

THIS SCRIPT IS THE ON SWITCH. Nothing else in the repo starts an autonomous run:
the composition root builds the service but registers no trigger, and the Temporal
worker registers the workflow without starting it. Paid, governed work that runs
while nobody is watching should require somebody to deliberately turn it on, name
the org, and see the cadence printed back.

    python -m scripts.create_autonomous_schedule --org-id org_acme
    python -m scripts.create_autonomous_schedule --org-id org_acme --dry-run
    python -m scripts.create_autonomous_schedule --org-id org_acme --delete

PRE-FLIGHT, RUN BEFORE ANYTHING IS CREATED
------------------------------------------
The script resolves the org's autonomous principal FIRST, against the real
database, and refuses to create a schedule if it cannot. A cadence installed for
an org whose `human_owner` resolves to no account would fire every hour, fail
every time, and write nothing to any brief — the failure would be invisible
precisely because there is no human attached to see it. `--dry-run` performs that
same check and creates nothing.

IDEMPOTENT. The schedule id is derived from (org_id, agent_id), so re-running
updates the existing cadence rather than stacking a second one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from temporalio.client import Client, ScheduleUpdate, ScheduleUpdateInput

from skylize.app.autonomy.errors import ContractNotAutonomous, PrincipalUnresolvable
from skylize.app.autonomy.pilot import (
    PILOT_AGENT_ID,
    PILOT_AGENT_IDS,
    assert_pilot_agent,
    cron_for,
)
from skylize.app.orchestrator.temporal.autonomous import build_schedule, schedule_id_for
from skylize.bootstrap import build_container
from skylize.config import get_settings


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    container = await build_container(settings)
    try:
        assert_pilot_agent(args.agent_id)
        # Resolved AFTER the gate, so an agent that is not allowlisted is
        # refused by name rather than by a missing cadence.
        cron = args.cron if args.cron is not None else cron_for(args.agent_id)

        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        sched_id = schedule_id_for(args.org_id, args.agent_id)

        if args.delete:
            await client.get_schedule_handle(sched_id).delete()
            print(f"deleted schedule {sched_id}")
            return 0

        if container.autonomous_runs is None:
            print(
                "REFUSED: this composition root built no AutonomousRunService",
                file=sys.stderr,
            )
            return 2
        # Against the REAL database, before anything is created.
        principal_id = await container.autonomous_runs.resolve_principal(
            org_id=args.org_id, agent_id=args.agent_id
        )
        print(
            f"pre-flight OK: runs of {args.agent_id!r} in org {args.org_id!r} will be "
            f"attributed to principal_id={principal_id}"
        )

        _, schedule = build_schedule(
            org_id=args.org_id,
            agent_id=args.agent_id,
            task_queue=settings.temporal_task_queue,
            cron=cron,
        )

        if args.dry_run:
            print(
                f"dry run: would create schedule {sched_id} "
                f"cron={cron!r} task_queue={settings.temporal_task_queue!r}"
            )
            return 0

        try:
            await client.create_schedule(sched_id, schedule)
            print(f"created schedule {sched_id} cron={cron!r}")
        except Exception as exc:  # already exists -> update in place
            if "already" not in str(exc).lower():
                raise

            async def _update(_: ScheduleUpdateInput) -> ScheduleUpdate:
                return ScheduleUpdate(schedule=schedule)

            await client.get_schedule_handle(sched_id).update(_update)
            print(f"updated existing schedule {sched_id} cron={cron!r}")
        return 0
    except (PrincipalUnresolvable, ContractNotAutonomous) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True)
    parser.add_argument(
        "--agent-id",
        default=PILOT_AGENT_ID,
        choices=sorted(PILOT_AGENT_IDS),
        help=f"allowlisted agents only (default: {PILOT_AGENT_ID})",
    )
    parser.add_argument(
        "--cron",
        default=None,
        help="override the cadence; defaults to the one declared for --agent-id "
             "in app/autonomy/pilot.py (each agent's is argued there)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
