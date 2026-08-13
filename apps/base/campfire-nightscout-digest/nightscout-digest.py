#!/usr/bin/env python3
"""Post yesterday's glucose statistics into the private #Health room.

**This is not an alarm path and must never become one.** Campfire cannot carry
CGM alarms: Web Push works only from an installed PWA, delivery is suppressed
while the room is connected (CONNECTION_TTL is 60s), it depends on the room's
involvement staying `everything`, and there is no escalation and no
acknowledgement. Nightscout's own alarms exist because low and high glucose are
safety-critical. This is a review of a day already over — nothing here is
urgent, and nothing urgent should be added here.

The one judgement in the code is the uptime floor. Nightscout stores a reading
roughly every five minutes, so a full day is about 288. Time in range computed
from a handful of readings is a number that looks authoritative and is not:
34% coverage reporting "78% in range" describes the fraction of the day the
sensor happened to be on, not the day. Below MIN_UPTIME_PERCENT the statistics
are withheld and the coverage is reported instead.

A day with no readings at all says so plainly. That is a real state, not an
edge case — a sensor between sessions produces exactly it.

Units: entries are stored in mg/dL whatever DISPLAY_UNITS says, so everything
is computed in mg/dL and converted for display. The thresholds below are the
international consensus targets, in their mg/dL form.

Posts as a dedicated **Health** bot rather than the Kubernetes one. The
Kubernetes bot holds an Anthropic API key, reads pod logs and can ask the actor
to change the cluster — not an identity that should also publish health data.
It is not a member of the room.

Env:
    CAMPFIRE_URL                full bot URL including the room and bot key
    NIGHTSCOUT_URL              base URL, default the in-cluster Service
    NIGHTSCOUT_API_SECRET_SHA1  sent as the api-secret header
    DIGEST_TIMEZONE             which day "yesterday" means, default
                                Europe/Amsterdam
"""

import html
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CAMPFIRE_URL = os.environ.get("CAMPFIRE_URL", "")
NIGHTSCOUT_URL = os.environ.get(
    "NIGHTSCOUT_URL", "http://nightscout.nightscout.svc.cluster.local:1337"
)
# Either the SHA1 of API_SECRET or a subject token — Nightscout accepts the
# same header for both, so swapping to a read-only subject changes only this.
API_SECRET_SHA1 = os.environ.get("NIGHTSCOUT_API_SECRET_SHA1", "")
TIMEZONE = os.environ.get("DIGEST_TIMEZONE", "Europe/Amsterdam")
TIMEOUT = 20

MMOL = 18.0182  # mg/dL per mmol/L

# One reading per five minutes.
EXPECTED_READINGS = 288
# Below this, report the coverage and withhold the statistics.
MIN_UPTIME_PERCENT = float(os.environ.get("MIN_UPTIME_PERCENT", "70"))

# Consensus targets, in the mg/dL the entries are stored in:
#   very low  < 3.0        low  3.0-3.8      in range  3.9-10.0
#   high  10.1-13.9        very high  > 13.9   (mmol/L)
BANDS = (
    ("very low", 0, 53),
    ("low", 54, 69),
    ("in range", 70, 180),
    ("high", 181, 250),
    ("very high", 251, 10_000),
)


def log(message):
    print(message, flush=True)


def fetch(start_ms, end_ms):
    query = urllib.parse.urlencode(
        {
            "find[date][$gte]": start_ms,
            "find[date][$lt]": end_ms,
            # A day is ~288; ask for well over so a dense uploader is not
            # silently truncated into a low reading count, which would look
            # like poor sensor uptime rather than a capped query.
            "count": 1000,
        }
    )
    request = urllib.request.Request(
        f"{NIGHTSCOUT_URL}/api/v1/entries.json?{query}",
        headers={"api-secret": API_SECRET_SHA1, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        entries = json.load(response)
    # Treatments and calibrations share the endpoint; only sensor values count.
    return [
        e["sgv"]
        for e in entries
        if e.get("type") == "sgv" and isinstance(e.get("sgv"), (int, float))
    ]


def yesterday_bounds():
    local = ZoneInfo(TIMEZONE)
    midnight = datetime.now(local).replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=1)
    return start, int(start.timestamp() * 1000), int(midnight.timestamp() * 1000)


def render(day, values):
    date = day.strftime("%A %-d %B")
    uptime = len(values) / EXPECTED_READINGS * 100

    if not values:
        return (
            f"<div><strong>🩺 {date}</strong></div>"
            "<div>No readings. Nothing was uploaded for this day — a sensor "
            "between sessions looks exactly like this.</div>"
        )

    if uptime < MIN_UPTIME_PERCENT:
        return (
            f"<div><strong>🩺 {date}</strong></div>"
            f"<div>Only {uptime:.0f}% sensor coverage "
            f"({len(values)} of ~{EXPECTED_READINGS} readings), so the "
            "statistics are left out: a range figure from a partial day "
            "describes when the sensor was on, not the day.</div>"
        )

    mean = statistics.fmean(values)
    rows = "".join(
        f"<li>{name}: <code>{sum(1 for v in values if low <= v <= high) / len(values) * 100:.0f}%</code></li>"
        for name, low, high in BANDS
    )
    return (
        f"<div><strong>🩺 {date}</strong></div>"
        f"<ul>{rows}</ul>"
        "<div>"
        f"average <code>{mean / MMOL:.1f}</code> mmol/L · "
        # Glucose Management Indicator, the standard estimate of HbA1c from
        # mean glucose. Defined on mg/dL, hence the unconverted mean.
        f"GMI <code>{3.31 + 0.02392 * mean:.1f}%</code> · "
        f"SD <code>{statistics.pstdev(values) / MMOL:.1f}</code> · "
        f"sensor <code>{uptime:.0f}%</code>"
        "</div>"
    )


def post(body):
    request = urllib.request.Request(
        CAMPFIRE_URL,
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def main():
    if not CAMPFIRE_URL or not API_SECRET_SHA1:
        log("CAMPFIRE_URL or NIGHTSCOUT_API_SECRET_SHA1 unset")
        return 1

    day, start_ms, end_ms = yesterday_bounds()
    try:
        values = fetch(start_ms, end_ms)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # Say so in the room rather than failing quietly. A digest that simply
        # stops arriving is indistinguishable from a day nobody looked at.
        log(f"could not read Nightscout: {error!r}")
        body = (
            "<div><strong>🩺 could not read Nightscout</strong></div>"
            f"<pre>{html.escape(str(error))}</pre>"
        )
    else:
        log(f"{day.date()}: {len(values)} readings")
        body = render(day, values)

    log(f"posted, campfire returned {post(body)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
