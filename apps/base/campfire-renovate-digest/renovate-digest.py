#!/usr/bin/env python3
"""Post a weekly summary of open Renovate PRs into Campfire.

Renovate runs hourly and opens PRs; nothing summarises them, so the backlog is
only visible to someone who goes looking on GitHub. The point of this is not
the list — it is surfacing the hazard that a PR left open long enough stops
being a safe single step.

Two things get flagged rather than merely listed:

  * a major bump, which is a breaking change by definition
  * a minor bump that skips more than one minor at once

The second is the one worth the code. Renovate's own Update column says
"minor" whether that is one minor or five, because it describes the *kind* of
change, not its size. A chart PR that has sat open across several releases
lands as one jump, and some of those are invalid as a single step — that is how
a ServiceMonitor gets silently broken. The distance has to be computed from the
from/to versions; the column will not tell you.

Renovate PRs here are authored by `ronaldlokers`, not `renovate[bot]`, because
Renovate runs as a CronJob with a personal token. Author filtering finds
nothing; the `renovate/` branch prefix is the reliable signal.

Env:
    GITHUB_TOKEN   read access to the repository's pull requests
    GITHUB_REPO    owner/name, default ronaldlokers/homelab
    CAMPFIRE_URL   full bot endpoint including the room and bot key
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ronaldlokers/homelab")
CAMPFIRE_URL = os.environ.get("CAMPFIRE_URL", "")
TIMEOUT = 30

# Renovate writes one table row per updated package, but the column count is
# not fixed — a Helm release gives three columns:
#
#   | [reloader](url)             | patch  | `2.2.15` → `2.2.16` |
#
# while a GitHub Action inserts a Type column:
#
#   | [home-operations/flate](url)| action | minor | `v0.4.14` → `v0.5.0` |
#
# So the row is located by the change cell rather than by position, and the
# kind is whatever cell precedes it. A positional regex silently skipped every
# action update.
CHANGE = re.compile(r"`(?P<from>[^`]+)`\s*(?:→|->)\s*`(?P<to>[^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]")

# How many minors may be skipped before it stops being routine.
MINOR_SKIP_LIMIT = 1


def log(message):
    print(message, flush=True)


def get(url):
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "campfire-renovate-digest",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def numeric_parts(version):
    """Leading numeric components of a version, ignoring any suffix.

    Handles the shapes Renovate actually produces here: `2.2.15`, `v1.46.0`,
    `3.13-alpine`, `2026.4.1`. Returns [] for anything without a leading
    number — a digest is not the place to be clever about exotic versions.
    """
    parts = []
    for chunk in version.lstrip("v").split("."):
        match = re.match(r"^\d+", chunk)
        if not match:
            break
        parts.append(int(match.group()))
    return parts


def minors_skipped(before, after):
    """How many minor releases this jump crosses, or None if unknowable."""
    a, b = numeric_parts(before), numeric_parts(after)
    if len(a) < 2 or len(b) < 2 or a[0] != b[0]:
        return None
    return b[1] - a[1]


def updates_in(body):
    """Every package row in a Renovate PR body.

    Header and separator rows fall out for free: neither contains a change
    cell, so neither matches.
    """
    updates = []
    for line in (body or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        change = next(
            ((index, m) for index, cell in enumerate(cells) if (m := CHANGE.search(cell))),
            None,
        )
        if not change or change[0] == 0:
            continue
        index, match = change
        package = LINK.search(cells[0])
        updates.append(
            {
                "package": package.group(1) if package else cells[0],
                "kind": cells[index - 1],
                "from": match.group("from"),
                "to": match.group("to"),
            }
        )
    return updates


def concerns(update):
    """Why this row is worth flagging, or None if it is routine."""
    kind = update["kind"].lower()
    if kind == "major":
        return "major"
    skipped = minors_skipped(update["from"], update["to"])
    if kind == "minor" and skipped is not None and skipped > MINOR_SKIP_LIMIT:
        return f"skips {skipped} minors"
    return None


def age_days(stamp, now):
    created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (now - created).days


def collect():
    """Open Renovate PRs, newest first, with their flagged updates."""
    pulls = get(f"https://api.github.com/repos/{GITHUB_REPO}/pulls?state=open&per_page=100")
    now = datetime.now(timezone.utc)
    rows = []
    for pull in pulls:
        if not pull["head"]["ref"].startswith("renovate/"):
            continue
        flagged = []
        for update in updates_in(pull.get("body")):
            reason = concerns(update)
            if reason:
                flagged.append((update, reason))
        rows.append(
            {
                "number": pull["number"],
                "title": pull["title"],
                "url": pull["html_url"],
                "age": age_days(pull["created_at"], now),
                "flagged": flagged,
            }
        )
    rows.sort(key=lambda r: (-len(r["flagged"]), -r["age"]))
    return rows


def render(rows):
    if not rows:
        return "<div><strong>🌱 No open Renovate PRs</strong></div>"

    flagged = [r for r in rows if r["flagged"]]
    head = f"<div><strong>📦 {len(rows)} open Renovate PR{'s' if len(rows) != 1 else ''}</strong></div>"
    parts = [head]

    if flagged:
        parts.append("<div><strong>⚠️ Read before merging</strong></div>")
        items = []
        for row in flagged:
            why = "; ".join(
                f"{u['package']} {u['from']} → {u['to']} ({reason})" for u, reason in row["flagged"]
            )
            items.append(
                f'<li><a href="{html.escape(row["url"], quote=True)}">#{row["number"]}</a> '
                f"{html.escape(row['title'])} — {html.escape(why)}, open {row['age']}d</li>"
            )
        parts.append("<ul>" + "".join(items) + "</ul>")

    routine = [r for r in rows if not r["flagged"]]
    if routine:
        parts.append("<div><strong>Routine</strong></div>")
        items = [
            f'<li><a href="{html.escape(r["url"], quote=True)}">#{r["number"]}</a> '
            f"{html.escape(r['title'])} — open {r['age']}d</li>"
            for r in routine
        ]
        parts.append("<ul>" + "".join(items) + "</ul>")

    return "".join(parts)


def publish(body):
    request = urllib.request.Request(
        CAMPFIRE_URL,
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def main():
    if not GITHUB_TOKEN or not CAMPFIRE_URL:
        log("GITHUB_TOKEN and CAMPFIRE_URL must both be set")
        return 1
    try:
        rows = collect()
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as error:
        # Fail loudly. A digest that silently stops is indistinguishable from a
        # week with no dependency updates, which is the wrong thing to believe.
        log(f"could not read pull requests: {error}")
        return 1

    flagged = sum(1 for r in rows if r["flagged"])
    log(f"{len(rows)} open Renovate PR(s), {flagged} flagged")
    try:
        log(f"campfire returned {publish(render(rows))}")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log(f"publish failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
