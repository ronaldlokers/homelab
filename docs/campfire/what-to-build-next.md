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
  * The two Secret readers can move behind that identity. Both are pure reads.
  * **`netpol-check` stays where it is.** `pods/exec` is write-shaped, and a
    daily reporting job should not be able to run commands in arbitrary pods.
    If its findings should be visible, that is option A for that one script.

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
