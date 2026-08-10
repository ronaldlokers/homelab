#!/usr/bin/env python3
"""Assert that Campfire would actually notify someone if an alert fired.

This is the failure that hides itself. A room's involvement reverts to
`mentions`, or the last push subscription is dropped, and alerts keep being
delivered to a room nobody is notified about. Nothing errors — Campfire logs a
successful send, Alertmanager gets its 201, and the silence is
indistinguishable from a quiet week.

Two properties, both read from Campfire's own database because it has no API:

  1. Every human membership of a room that a bot posts into is set to
     `everything`. Campfire pushes to memberships involved in everything, or
     involved in mentions *and actually mentioned* — and a bot mentions nobody,
     because mentions are signed ActionText attachments rather than text.
  2. At least one Push::Subscription exists. Without one there is no device to
     deliver to, however correct the involvement settings are.

Scope is closed rooms that have a bot member. Two rejected alternatives, both
of which produce findings nobody would act on:

  * "any room with a bot member" pulls in All Talk, because an open room has
    every user as a member, bots included. It is a chat room; holding it to
    alerting rules would fail the check the day it is legitimately set to
    mentions.
  * "any room a bot has posted into" is worse in both directions — All Talk has
    bot messages from testing, and #Gatus has none because nothing has broken
    yet, so the room most in need of checking would be the one skipped.

The assumption, worth knowing if this ever fires oddly: a closed room with a
bot in it is an alerting room. Adding a bot to a closed social room would make
this ask for `everything` there too.

Reporting is the house pattern — exit non-zero, let the Job fail, let
KubeJobFailed reach Alertmanager. Note the circularity that creates: since
Alertmanager now routes to Campfire only, a failure here is announced through
the channel being checked. The message still lands in the room, so it is seen
whenever Campfire is next opened; it just will not push. Fixing that properly
means a second, independent channel, which is the trade recorded on the card.
"""

import json
import os
import subprocess
import sys

# Unset in-cluster, where kubectl uses the ServiceAccount and there is exactly
# one cluster to talk to. Set it to run this against production from a laptop,
# the same escape hatch recovery-source-check carries.
CONTEXT = os.environ.get("KUBE_CONTEXT", "")
NAMESPACE = "campfire"
DEPLOYMENT = "deploy/campfire"
CONTAINER = "campfire"
TIMEOUT = 120

# Printed either side of the payload so the JSON can be found regardless of
# whatever Rails decides to log around it — boot warnings are noisy and are not
# a failure.
MARKER = "CAMPFIRE_NOTIFY_CHECK"

QUERY = f"""
require "json"
rooms = Room.where(type: "Rooms::Closed").filter_map do |room|
  next unless room.memberships.joins(:user).where(users: {{ role: :bot }}).exists?
  humans = room.memberships.joins(:user).where.not(users: {{ role: :bot }}).map do |m|
    {{ user: m.user.name, involvement: m.involvement }}
  end
  {{ name: room.name, humans: humans }}
end
puts "{MARKER}" + JSON.generate({{ rooms: rooms, subscriptions: Push::Subscription.count }})
"""


def log(message):
    print(message, flush=True)


def read_state():
    prefix = ["kubectl"] + ([f"--context={CONTEXT}"] if CONTEXT else [])
    result = subprocess.run(
        [*prefix, "exec", "-n", NAMESPACE, DEPLOYMENT, "-c", CONTAINER,
         "--", "bin/rails", "runner", QUERY],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rails runner failed: {result.stderr.strip()[-400:]}")
    for line in result.stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise RuntimeError("no payload in rails output; the query did not run")


def violations(state):
    found = []

    if state["subscriptions"] < 1:
        found.append(
            "no Push::Subscription exists — no device would receive anything, "
            "whatever the room settings say"
        )

    for room in state["rooms"]:
        if not room["humans"]:
            found.append(f"room {room['name']!r} has a bot but no human member")
            continue
        for human in room["humans"]:
            if human["involvement"] != "everything":
                found.append(
                    f"room {room['name']!r}: {human['user']} is set to "
                    f"{human['involvement']!r}, so bot messages arrive silently"
                )
    return found


def main():
    try:
        state = read_state()
    except (subprocess.SubprocessError, RuntimeError, ValueError) as error:
        log(f"could not read Campfire state: {error}")
        return 1

    rooms = ", ".join(r["name"] for r in state["rooms"]) or "(none)"
    log(f"checked rooms: {rooms}; subscriptions: {state['subscriptions']}")

    found = violations(state)
    if not found:
        log("all bot rooms notify: every human involvement is 'everything'")
        return 0

    for problem in found:
        log(f"PROBLEM {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
