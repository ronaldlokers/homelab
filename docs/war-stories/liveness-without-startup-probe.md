# 47 Restarts in 19 Hours, Every One Exiting Zero

Linkding restarted 47 times in 19 hours. Every termination reported
`reason: Completed, exit code: 0` — the signature of a clean shutdown, not a
crash. The application was fine. The probe configuration was killing it.

## The Problem

### Initial Symptoms

Noticed while auditing restart counts after a host reboot:

```
restarts  podAge   lastRestart   pod
      47   18.1h      18m ago    linkding/linkding-84bf655c4c-p5n6q  Completed
      35   19.7h      17m ago    nightscout/nightscout-574d674bf8    Error
```

47 restarts over 18 hours is roughly one every 23 minutes — far more than the
one host reboot in that window could explain.

The termination detail was the interesting part:

```
reason: Completed   exit: 0   signal: None
started:  2026-08-04T11:14:12Z
finished: 2026-08-04T11:16:11Z
```

Exit code 0 means the main process returned normally. Nothing crashed. And it ran
for exactly **119 seconds** before doing so.

### The Number That Gave It Away

The liveness probe:

```yaml
livenessProbe:
  httpGet: { path: /health, port: http }
  initialDelaySeconds: 30
  periodSeconds: 30
  failureThreshold: 3
```

`30 + (3 × 30) = 120 seconds.`

The container died at 119. That is not a coincidence — it is kubelet sending
`SIGTERM` after the liveness probe failed three consecutive times, and the
application shutting down gracefully in response, which is why the exit code is
0 and the reason is `Completed`.

## The Root Cause

There was no `startupProbe`.

Without one, `livenessProbe` begins counting from `initialDelaySeconds` — it does
not wait for the application to finish starting. Its budget therefore has to
cover the **worst-case startup time**, not the steady-state response time, and
120 seconds is a startup budget disguised as a health-check budget.

On an idle cluster that is ample and nothing goes wrong. During convergence —
when a host reboots and 45 pods start simultaneously, contending for one disk and
two cores — startup crosses the budget. The probe fires, the container is killed
mid-startup, and the restart begins the same slow startup again under the same
contention.

The failure is self-reinforcing: each restart adds load, which slows the next
startup, which makes the kill more likely.

## Why This Was Easy to Miss

**Exit 0 reads as healthy.** Restart counts get scanned for `Error`,
`CrashLoopBackOff` or `OOMKilled`. `Completed` looks like a job that finished, and
in a Deployment it is easy to read past.

**The application logged nothing wrong**, because nothing was wrong with it. It
was still starting when it was terminated.

**It self-healed.** Once the cluster was quiet, startup fit inside 120 seconds and
the pod stayed up. A snapshot taken at any calm moment shows a healthy pod, and
the restart counter is cumulative and easy to attribute to "that reboot last
week".

## The Solution

```yaml
startupProbe:
  httpGet: { path: /health, port: http }
  periodSeconds: 10
  failureThreshold: 30
```

Five minutes for startup. `livenessProbe` is unchanged and only begins once the
startup probe has succeeded once, so it still catches a process that dies later —
which is what it is actually for.

An audit found the gap was not unique:

| App | startupProbe | Liveness kills after |
|---|---|---|
| linkding | **no** | 120s |
| mealie | **no** | 120s |
| gatus | **no** | 120s |
| commafeed, tandoor, nightscout, pgadmin | yes | — |

## The Variant a Probe Cannot Fix

Commafeed crash-looped through the same reboot, seven times, and it **already
had** a `startupProbe`. Different mechanism:

```
Caused by: org.postgresql.util.PSQLException: Connection to
postgres-cluster-rw.database.svc.cluster.local:5432 refused
```

It exits 1 when the database is unreachable. The process is gone before any probe
runs, so no probe configuration can help. Each attempt was a full JVM plus
Hibernate plus Liquibase startup, burning exactly the CPU and disk that are
scarce during convergence, to achieve nothing.

PriorityClasses do not solve this either — they order *scheduling*, not
*dependency readiness*. What does:

```yaml
initContainers:
  - name: wait-for-postgres
    image: busybox:1.37
    command:
      - sh
      - -c
      - |
        until nc -z postgres-cluster-rw.database.svc.cluster.local 5432; do
          echo "waiting for postgres..."
          sleep 3
        done
```

`nc` rather than `pg_isready`: the failure mode is a refused TCP connection, and
CloudNativePG's `-rw` Service only has endpoints once an instance is primary, so
TCP-open is a sufficient signal and costs no extra image pull.

## Lessons

**`livenessProbe` without `startupProbe` is a startup timeout.** Its budget must
cover worst-case cold start, not steady-state latency. Anything that runs
database migrations, compiles assets, or warms a cache at boot will eventually
exceed a budget sized for a healthy running process.

**Exit 0 with a `Completed` reason can mean the probe killed it.** A well-behaved
application handling `SIGTERM` cleanly is indistinguishable, in the exit code,
from one that finished its work. Match the container's lifetime against
`initialDelaySeconds + periodSeconds × failureThreshold`; if they agree, the probe
is the cause.

**Cumulative restart counters hide rates.** 47 restarts on an 18-hour-old pod is a
rate, not history. Restart counts are only meaningful next to pod age and the
timestamp of the last restart.

**Two failure shapes need two fixes.** An app killed mid-startup needs a
`startupProbe`. An app that *exits* when a dependency is missing needs something
that refuses to start it — an init container. Applying either fix to the other
problem does nothing, and both look like "it keeps restarting" from the outside.

## Related

- [The App That Started Perfectly and Served Nothing](tandoor-service-env-var-collision.md)
- [Switching to `Recreate` Strategy Couldn't Fix Itself Through Git Alone](deployment-recreate-strategy-ssa-conflict.md)
