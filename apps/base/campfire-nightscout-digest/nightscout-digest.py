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
    DIGEST_DATE                 report this day (YYYY-MM-DD) instead of
                                yesterday; unset on the CronJob, for manual
                                runs and backfill
"""

import html
import json
import os
import secrets
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import chart

CAMPFIRE_URL = os.environ.get("CAMPFIRE_URL", "")
NIGHTSCOUT_URL = os.environ.get(
    "NIGHTSCOUT_URL", "http://nightscout.nightscout.svc.cluster.local:1337"
)
# Either the SHA1 of API_SECRET or a subject token — Nightscout accepts the
# same header for both, so swapping to a read-only subject changes only this.
API_SECRET_SHA1 = os.environ.get("NIGHTSCOUT_API_SECRET_SHA1", "")
TIMEZONE = os.environ.get("DIGEST_TIMEZONE", "Europe/Amsterdam")
# Empty on the CronJob. Set it on a one-off Job to report an older day — the
# day a sensor was actually running, or one missed while something was broken.
DIGEST_DATE = os.environ.get("DIGEST_DATE", "").strip()
TIMEOUT = 20
# See fetch_with_retry: the first connection from a new pod can be refused
# while its NetworkPolicy is still being programmed.
FETCH_ATTEMPTS = 3
FETCH_RETRY_SECONDS = 5

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


def fetch_with_retry(start_ms, end_ms):
    """Retry before giving up, because the first attempt is the fragile one.

    A freshly created pod can send its first packet before the CNI has finished
    programming its NetworkPolicy, and k3s enforces with REJECT — so the
    symptom is `connection refused` on attempt one and success moments later.
    Observed twice while building this.

    The graceful fallback below is what makes this necessary rather than
    optional: catching the error and posting "could not read Nightscout" exits
    zero, so the Job succeeds and `backoffLimit` never retries it. Without a
    retry here, one unlucky moment at 07:00 costs the whole day's figures.
    """
    last = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            return fetch(start_ms, end_ms)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
            if attempt + 1 < FETCH_ATTEMPTS:
                log(f"fetch attempt {attempt + 1} failed ({error}), retrying")
                time.sleep(FETCH_RETRY_SECONDS)
    raise last


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
    # Timestamps come along because the day graph needs them; every statistic
    # uses the values alone.
    return [
        (e["date"], e["sgv"])
        for e in entries
        if e.get("type") == "sgv"
        and isinstance(e.get("sgv"), (int, float))
        and isinstance(e.get("date"), (int, float))
    ]


def day_bounds():
    """The day to report, as (local midnight, start ms, end ms).

    Yesterday unless DIGEST_DATE names another day. The day is bounded by local
    midnights and not by 24 hours from a fixed instant, so the two days a year
    that are 23 or 25 hours long still come out as whole days.
    """
    local = ZoneInfo(TIMEZONE)
    if DIGEST_DATE:
        # Deliberately unguarded: a malformed date raises, the Job fails and
        # says why. Falling back to yesterday would silently report a different
        # day than the one asked for, which is worse than a failed manual run.
        start = datetime.strptime(DIGEST_DATE, "%Y-%m-%d").replace(tzinfo=local)
    else:
        midnight = datetime.now(local).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = midnight - timedelta(days=1)
    end = start + timedelta(days=1)
    return start, int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def render(day, readings):
    values = [v for _, v in readings]
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


def post_image(png_bytes, filename="glucose.png"):
    """Post the chart as an attachment.

    The same bot route takes either: Messages::ByBotsController permits
    `attachment` when the multipart form carries one and falls back to the raw
    body otherwise. An attachment message has no text of its own, which is why
    the numbers go in their own message first.
    """
    boundary = "----campfire" + secrets.token_hex(16)
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="attachment"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            png_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        CAMPFIRE_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def main():
    if not CAMPFIRE_URL or not API_SECRET_SHA1:
        log("CAMPFIRE_URL or NIGHTSCOUT_API_SECRET_SHA1 unset")
        return 1

    day, start_ms, end_ms = day_bounds()
    try:
        readings = fetch_with_retry(start_ms, end_ms)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # Say so in the room rather than failing quietly. A digest that simply
        # stops arriving is indistinguishable from a day nobody looked at.
        log(f"could not read Nightscout: {error!r}")
        body = (
            "<div><strong>🩺 could not read Nightscout</strong></div>"
            f"<pre>{html.escape(str(error))}</pre>"
        )
        readings = []
    else:
        log(f"{day.date()}: {len(readings)} readings")
        body = render(day, readings)

    log(f"posted, campfire returned {post(body)}")

    # The chart only when the statistics were shown. A picture of a partial day
    # makes the same false claim the withheld numbers would have, and a picture
    # of nothing is worse than no picture.
    if len(readings) / EXPECTED_READINGS * 100 >= MIN_UPTIME_PERCENT:
        try:
            values = [v for _, v in readings]
            png = chart.render(
                readings,
                BANDS,
                start_ms,
                title=day.strftime("%A %-d %B"),
                subtitle=f"{len(values)} readings   "
                f"{len(values) / EXPECTED_READINGS * 100:.0f}% sensor coverage",
            )
            log(f"chart {len(png)} bytes, campfire returned {post_image(png)}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            # The numbers are already posted; a missing picture is not worth
            # failing the run over.
            log(f"chart failed: {error!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
