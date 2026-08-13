#!/usr/bin/env python3
"""Write the committed fortnights under `golden/`.

Run once, by hand, when a new shape of day is worth pinning:

    python3 tests/make_fixtures.py

The generator is deterministic and uses its own small PRNG rather than
`random`, so the same numbers come out of any language that implements the
same three lines — which is the point, because these fixtures have to survive
a port to TypeScript.

Regenerating the *findings* files is a deliberate act: they are the record of
what the current implementation claims about this data, and rewriting them
silently would make the tests agree with any change, which is the opposite of
what they are for.
"""

import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from helpers import BANDS, DIGEST, GOLDEN, load  # noqa: E402

LABELS = [f"{23 + i} Jul" if 23 + i <= 31 else f"{i - 8} Aug" for i in range(14)]


def noise(state):
    """A 32-bit linear congruential generator, so the fixtures port cleanly."""
    state = (1664525 * state + 1013904223) % (2**32)
    return state, (state / 2**32 - 0.5) * 14


def fortnight(spike=False, low=False, flat=False):
    days, state = [], 12345
    for index in range(14):
        drift = 0 if flat else 14 * math.sin(index / 3.2) + (26 if index in (4, 5) else 0)
        last = index == 13
        readings, value = [], 100 + drift
        for step in range(288):
            # One day loses an hour and a half to a sensor change.
            if index == 6 and 150 <= step < 168:
                continue
            minute = step * 5
            if flat:
                target = 100
            else:
                meal = sum(
                    size * math.exp(-(((minute - at) / 78) ** 2))
                    for at, size in (
                        (8 * 60, 92 + drift),
                        (13 * 60, 66),
                        (19 * 60, 104 + drift),
                    )
                )
                target = 96 + drift + meal + 10 * math.sin(minute / 210)
            if last and spike and 8 * 60 < minute < 11 * 60:
                target += 120
            if last and low and 120 < minute < 300:
                target = 54
            state, jitter = noise(state)
            value += (target - value) * 0.24 + jitter
            # Every fixture but the low one is floored clear of 70 mg/dL, so
            # each exercises a different headline instead of all of them
            # tripping the low finding, which outranks everything else.
            floor = 45 if low else 74
            readings.append([minute, int(max(floor, min(320, value)))])
        days.append({"label": LABELS[index], "readings": readings})
    return {"days": days}


CASES = {
    "fortnight-ordinary": fortnight(),
    "fortnight-spike": fortnight(spike=True),
    "fortnight-low": fortnight(low=True),
    "fortnight-flat": fortnight(flat=True),
}

if __name__ == "__main__":
    chart = load("chart")
    GOLDEN.mkdir(exist_ok=True)
    for name, data in CASES.items():
        (GOLDEN / f"{name}.json").write_text(json.dumps(data, separators=(",", ":")))
        days = [(d["label"], [tuple(r) for r in d["readings"]]) for d in data["days"]]
        (GOLDEN / f"{name}.findings.json").write_text(
            json.dumps(
                {
                    "findings": chart.findings(days, BANDS),
                    "outliers": chart.outliers(days, BANDS),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"{name}: {sum(len(d['readings']) for d in data['days'])} readings")
