# What to build next

Decided together, ordered by value. Nothing here is built yet.

## 1. Give the silent checkers a room

`netpol-check`, `secret-refs-check` and `recovery-source-check` run on a
schedule, work out something real, and report to nobody. Their only signal is
the job's exit status, which surfaces as `KubeJobFailed` with the finding
itself buried in a pod log that is garbage-collected.

The third is the one that matters most. Its own docstring says a wrong
`bootstrap.recovery` pointer is invisible until a rebuild — the one moment
nobody is in a position to notice — and that **it has been wrong three times**.
That check is the difference between a recoverable cluster and a surprise, and
its output currently goes nowhere.

### The wrinkle

Each exists twice on purpose: `scripts/<name>.py` is canonical and runnable
locally against a context, `apps/base/<name>/` is the CronJob copy, and
`validate.sh` enforces that they match. So this is not a straight port.

**Option A — give them a destination.** Ten lines each: post findings when
there are any, stay silent otherwise. Keeps one implementation, keeps the local
runs, adds no new machinery. Against it: three more things holding a room URL,
in a repository that just finished moving that responsibility out.

**Option B — fold them into the briefing.** They become checks in
`copy/cluster/`, the CronJobs go, and the briefing reports them beside flux and
pods, which is exactly the shape it already has. Against it: the canonical
Python goes too, so `--context=staging` local runs go with it, and the
briefing's ServiceAccount inherits their RBAC.

**Decided: B, for two of the three.** Looking at what they need settled it in a
way neither option anticipated.

`secret-refs-check` and `recovery-source-check` read Secrets.
`netpol-check` runs `kubectl exec` inside app pods to probe reachability. And
the briefing shared a ServiceAccount with the responder — the process that
reads pod logs and feeds them to a model, whose containment is precisely that
its token cannot read a Secret, cannot exec and cannot write.

So folding all three in would have traded a hard boundary for a soft one:
"the tool list happens not to expose it".

  * The briefing has its own ServiceAccount now, so a grant it needs is not a
    grant the responder gets.
  * **`secret-refs-check` moved.** It is a pure read, and the way it reads
    matters: the cluster-wide list asks for metadata only, so no value crosses
    the wire, and drift is reported as SHA-256 prefixes rather than values.
  * **`recovery-source-check` cannot move either** — this is the part the plan
    got wrong. It reads Secrets *and* runs `barman-cloud-backup-list` inside a
    postgres pod, deliberately, so that the catalogue is read by the tool a real
    restore would use rather than by this repository's idea of barman. That
    design is right and it needs `pods/exec`. So it stays too.
  * **`netpol-check` stays where it is**, for the same reason. `pods/exec` is
    write-shaped, and a daily reporting job should not be able to run commands
    in arbitrary pods.

So the answer is one of three, not three. The two that stay are not stuck: if
their findings should be visible, that is option A for those two scripts, and
worth deciding on its own.

### One more thing the move did not fix

Campfire is production-only, so staging keeps running the Python CronJob — 4
mirrored credentials there, and no briefing to fold them into. Two
implementations of one check, in two languages, which is exactly the drift the
`validate.sh` copy gate exists to prevent, one layer up.

**Done, and not the way that paragraph said.** ntfy was the wrong answer: staging
already talks to production's Campfire, and has since the Flux alerts were
pointed at `campfire-bridge.ronaldlokers.nl`. It holds no bot key to do it —
it POSTs to the bridge, which holds the key.

So the briefing crosses the same way. `BRIEFING_BRIDGE_URL` on the beat,
`/briefing` on the bridge, `CLUSTER: staging` on the message. What crosses is
the briefing, not the markup: the bridge's only authentication is a LAN-only
ingress, so a path taking rendered HTML would let anything on that network post
arbitrary markup into a room.

Staging gained the other eight checks rather than keeping one, and
`scripts/secret-refs-check.py`, its base CronJob and its copy-match gate entry
are gone.

## 2. Speedtest, as the press's second tenant

The data is already collected and nothing reads it. A weekly line of what you
actually get against what you pay for is naturally a chart, which makes it the
honest way to prove the press is a house style rather than one sheet.

Wants: a `speedtest` beat, a `press/speedtest/` with its own palette and
layout, and the same treatment glucose got — what is the sentence at the top,
and what does a quiet week look like. A chart that says "the internet was fine"
every Sunday is the briefing problem again.

## 3. Three things for glucose

- **The encouraging line comes back.** Asked for twice, eaten by the redesign,
  parked. The findings engine already knows how the day went; the sheet has
  room under the bar.
- **Sensor-change prediction.** Coverage collapses on a predictable cycle.
  Saying "this sensor is on day 9" beats reporting the gap after it happens.
- **Week against week.** The findings compare the halves of the fortnight
  already; the rows do not show it. A delta, or a second block of marks, would
  make the trend visible rather than merely stated.

The first is small. The second needs the sensor's own history rather than the
readings — sessions are inferable from the gaps, which is a real piece of work
and the interesting one.

## 4. Push the press

Every beat that has something shaped like a quantity should draw it. Today the
renderer has one tenant, so its "house style" is really one sheet's style.
Speedtest is the test of that: if `press/tokens.ts` survives a second beat
without changes, it was a house style. If it does not, what it learns belongs
back in the tokens.

## Not chosen, still worth remembering

- **Backup restore verification.** Nothing tests that a backup restores; the
  runbook documents RTO and RPO and no job proves either. Highest-value
  reliability work available, and the largest.
- **Immich on this day.** The only idea on the table that is purely a pleasure.
  Needs a round that carries real images, which the press does not cover.
- **Mealie and Tandoor.** Two recipe managers in one cluster is its own
  question.
