"""Mock publisher entrypoint -- D2. Simulates 1-5 fake nodes against the
fence-state contract. See docs/dashboard-plan.md's "Mock publisher" section.

Live mode (default): publishes over MQTT on a configurable cadence, one
reading per message (see scenarios.build_payload for the split-cadence
payload-shape decision).

Backfill mode (--backfill): writes N days of history directly to Postgres
instead, at today's single 600s interval, optionally seeding a board-swap /
relocation / uncalibrated history (--history) alongside the base
fence-condition scenario.
"""

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mock-publisher")

MQTT_HOST = os.environ.get("FENCE_MQTT_HOST", "broker")
MQTT_PORT = int(os.environ.get("FENCE_MQTT_PORT", "1883"))
DATABASE_URL = os.environ.get(
    "FENCE_DATABASE_URL", "postgresql+asyncpg://fence:fence@db:5432/fence"
)

SCENARIO_CHOICES = [
    "normal",
    "slow-decline",
    "low-voltage",
    "fence-down",
    "node-silent",
    "battery-drain",
]
HISTORY_CHOICES = ["none", "board-swap", "relocation", "uncalibrated"]


def _parse_cadence(value: str):
    if value in ("realtime", "realtime-split"):
        return value
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid cadence {value!r}; use seconds, 'realtime', or 'realtime-split'"
        ) from None
    if seconds <= 0:
        raise argparse.ArgumentTypeError("cadence must be positive")
    return seconds


def _parse_days(value: str) -> int:
    text = value.removesuffix("d")
    try:
        days = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid duration {value!r}; use e.g. '90d'") from None
    if days <= 0:
        raise argparse.ArgumentTypeError("duration must be positive")
    return days


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--nodes",
        type=int,
        default=1,
        choices=range(1, 6),
        help="number of simulated nodes, 1-5 (default 1); ignored if --scenarios is given",
    )
    parser.add_argument(
        "--scenario",
        default="normal",
        choices=SCENARIO_CHOICES,
        help="scenario applied to every node, or the base fence-condition scenario "
        "in --backfill mode (default normal)",
    )
    parser.add_argument(
        "--scenarios",
        default=None,
        help="live mode only: comma-separated per-node scenario list, e.g. "
        "normal,low-voltage,node-silent (overrides --nodes and --scenario)",
    )
    parser.add_argument(
        "--cadence",
        type=_parse_cadence,
        default=10,
        help="live mode only: seconds between publishes, 'realtime' (600s, today's "
        "single-interval firmware -- what D6 rehearses against), or "
        "'realtime-split' (60s sample / 900s report, the recommended split, with "
        "report-by-exception on fault crossings) (default 10)",
    )
    parser.add_argument(
        "--backfill",
        type=_parse_days,
        default=None,
        metavar="Nd",
        help="backfill mode: write N days of history at real 600s spacing directly "
        "to Postgres, ending now, instead of publishing live (e.g. --backfill 90d)",
    )
    parser.add_argument(
        "--backfill-nodes",
        type=int,
        default=1,
        choices=range(1, 6),
        help="backfill mode: number of nodes to backfill (default 1)",
    )
    parser.add_argument(
        "--history",
        default="none",
        choices=HISTORY_CHOICES,
        help="backfill mode: board-swap / relocation / uncalibrated history alongside "
        "the base scenario (default none)",
    )
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    if args.backfill is not None:
        from backfill import run_backfill

        run_backfill(
            days=args.backfill,
            scenario_name=args.scenario,
            node_count=args.backfill_nodes,
            history_mode=args.history,
            database_url=DATABASE_URL,
        )
        return

    if args.scenarios:
        node_scenarios = [s.strip() for s in args.scenarios.split(",")]
        for s in node_scenarios:
            if s not in SCENARIO_CHOICES:
                sys.exit(f"unknown scenario {s!r}; choices: {', '.join(SCENARIO_CHOICES)}")
        if len(node_scenarios) > 5:
            sys.exit("at most 5 nodes are supported (see mock-000N reservation)")
    else:
        node_scenarios = [args.scenario] * args.nodes

    from live import run_live

    log.info(
        "mock-publisher live: %d node(s), cadence=%s, scenarios=%s",
        len(node_scenarios),
        args.cadence,
        node_scenarios,
    )
    run_live(node_scenarios, args.cadence, MQTT_HOST, MQTT_PORT)


if __name__ == "__main__":
    main()
