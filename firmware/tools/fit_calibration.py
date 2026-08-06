#!/usr/bin/env python3
"""Fit a node's kv = adc_mv * gain + offset calibration constant.

Takes 3+ (adc_mv, handheld_tester_kv) point pairs collected while the node
is in calibration mode (see firmware/README.md#calibration) and does a
plain closed-form least-squares fit. Stdlib only, deliberately — this is a
bench tool run a handful of times per node/assignment, not worth a numpy
dependency.

Usage:
    python3 fit_calibration.py 1872:6.93 2010:7.40 1350:5.10

Prints the fitted gain/offset and the mosquitto_pub command line to push
them to the node via its fence/<node_id>/calib/set command topic.
"""

import argparse
import sys


def fit_least_squares(points):
    """Closed-form least-squares fit of y = gain * x + offset."""
    n = len(points)
    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xy = sum(x * y for x, y in points)
    sum_xx = sum(x * x for x, _ in points)

    denominator = n * sum_xx - sum_x * sum_x
    if denominator == 0:
        raise ValueError(
            "all adc_mv points are identical; can't fit a slope from a single x value"
        )

    gain = (n * sum_xy - sum_x * sum_y) / denominator
    offset = (sum_y - gain * sum_x) / n
    return gain, offset


def parse_point(text):
    try:
        adc_mv_str, kv_str = text.split(":", 1)
        return float(adc_mv_str), float(kv_str)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected ADC_MV:KV (e.g. 1872:6.93), got {text!r}"
        ) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "points",
        nargs="+",
        type=parse_point,
        help="ADC_MV:KV pairs, e.g. 1872:6.93 2010:7.40 1350:5.10 (3-5 recommended, spread across 5-10 kV)",
    )
    parser.add_argument("--node-id", help="node_id, for the printed mosquitto_pub command")
    parser.add_argument("--mqtt-host", default="192.168.1.10", help="broker host for the printed command")
    args = parser.parse_args()

    if len(args.points) < 2:
        print("error: need at least 2 points to fit a slope (3-5 recommended)", file=sys.stderr)
        sys.exit(1)
    if len(args.points) < 3:
        print(
            "warning: fewer than 3 points can't confirm the response is linear, only fit a line through it",
            file=sys.stderr,
        )

    try:
        gain, offset = fit_least_squares(args.points)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"gain (kv_per_mv):   {gain:.6f}")
    print(f"offset (kv_offset): {offset:.4f}")
    print()
    print("Residuals (fitted kv vs. handheld tester kv):")
    for adc_mv, kv in args.points:
        fitted = adc_mv * gain + offset
        print(f"  adc_mv={adc_mv:8.1f}  tester={kv:6.3f} kV  fitted={fitted:6.3f} kV  diff={fitted - kv:+.3f} kV")

    node_id = args.node_id or "<node_id>"
    payload = f'{{"kv_per_mv": {gain:.6f}, "kv_offset": {offset:.4f}}}'
    print()
    print("To push this to the node (it applies on its next report wake):")
    print(
        f"  mosquitto_pub -h {args.mqtt_host} -t fence/{node_id}/calib/set "
        f"-m '{payload}' -r"
    )
    print()
    print("Then record this calibration in docs/calibration.md.")


if __name__ == "__main__":
    main()
