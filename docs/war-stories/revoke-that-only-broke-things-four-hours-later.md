# The Revoke That Waited Four Hours to Break Anything

Finishing off a database isolation project, one `REVOKE` closed the last gap. I
checked who was connected first, confirmed the only user was a superuser, ran it,
verified nothing broke, and moved on.

It had broken high availability. Nothing showed it for four hours, because the
thing it broke only happens when a replica restarts — and no replica restarted
until I caused one myself, doing unrelated work.

## The Change

[#219](https://github.com/ronaldlokers/homelab/issues/219) gave every application
its own PostgreSQL role owning its own database. The mechanism that actually
enforces it is revoking PostgreSQL's default `CONNECT` grant to `PUBLIC`. Eight
databases were done. The ninth was the shared `postgres` database.

Before running it I enumerated who was connected:

```
postgres | pg_cron scheduler | 1
postgres | psql              | 1
```

Only `postgres`, a superuser. Superusers bypass permission checks entirely, so
the revoke could not affect it. I ran it, waited, and re-checked pg_cron:

```
succeeded | 42        (in the last 40 seconds)
```

Green. Every database now refused `PUBLIC` and refused the shared `app` role, and
nothing had broken. That is what I reported.

## Four Hours Later

Different task: draining stale Longhorn instance-manager pods. One held the
PostgreSQL primary's volume engine, so the safe move was a planned switchover
first — promote a replica, then drain the node the old primary was on.

The switchover promoted cleanly. Then the demoted instance would not come back:

```
postgres-cluster-3   0/1   Running   1 (11m ago)   44h
```

Its logs, once every five seconds:

```
DB not available, will retry
err: failed to connect to `user=streaming_replica database=postgres`:
     FATAL: permission denied for database "postgres" (SQLSTATE 42501)
```

## The Root Cause

`streaming_replica` is the role CloudNativePG uses for replication. It is **not**
a superuser:

```sql
SELECT rolname, rolsuper FROM pg_roles WHERE rolname = 'streaming_replica';
-- streaming_replica | f
```

so it does not bypass `CONNECT`. And CNPG connects with `database=postgres` when
a replica rejoins the cluster.

`REVOKE CONNECT ON DATABASE postgres FROM PUBLIC` therefore removed the only
grant that role had. One statement:

```sql
GRANT CONNECT ON DATABASE postgres TO streaming_replica;
```

and the replica rejoined immediately, going from thirteen minutes of failing
retries to `3/3 Cluster in healthy state`.

## Why the Check Beforehand Was Worthless

I checked `pg_stat_activity` and concluded that only a superuser used this
database. That was true, and it was the wrong question.

**`streaming_replica` connects only when a replica rejoins.** In steady state it
holds a replication connection that is already open and keeps streaming
regardless of `CONNECT` — the privilege is evaluated at connection time, not
continuously. So the role is *guaranteed* to be absent from `pg_stat_activity`
at exactly the moment you are deciding whether the revoke is safe, and existing
replicas are *guaranteed* to keep working right after you run it.

The check confirmed the change was safe for everything already connected. Nothing
about it spoke to what would connect next.

## Why Nothing Surfaced It

Between the revoke and the discovery, the cluster reported healthy the entire
time:

- three instances ready, both replicas streaming, zero lag
- all Flux Kustomizations `True`, zero non-running pods
- pg_cron succeeding, applications serving

The break was in a code path — replica rejoin — that a healthy cluster never
executes. HA machinery is only exercised by failure, so a latent break in it is
invisible until the failure it exists to survive.

Had this stayed hidden until a real node failure, the sequence would have been: a
node dies, the replica cannot rejoin, the cluster runs without redundancy, and
the operator discovers the reason during the incident rather than before it.

## Lessons

**Checking who is connected does not tell you who will connect.** `CONNECT` is
evaluated at connection time. Any role that connects rarely — replication, a
backup job, a nightly batch, a scaling event — is missing from
`pg_stat_activity` precisely when you are reasoning about it. Enumerate roles
that *could* connect (`pg_roles` with `rolcanlogin`), not sessions that happen to
be open.

**"Superusers bypass this" is a claim about superusers, not about the
component.** I verified that pg_cron was unaffected and generalised it to
"internal PostgreSQL machinery is unaffected". pg_cron runs as `postgres` and
bypasses everything; `streaming_replica` deliberately does not, because it is a
least-privilege role. Two pieces of the same database's internals, opposite
answers.

**A change that verifies clean can still be latent.** Every signal after the
revoke was green, and all of them were measuring steady state. When a change
touches a failure path, steady-state verification is not evidence — the only
real test is to trigger the failure, which here meant a deliberate switchover.

**Isolation work removes privileges from things you did not think of as
applications.** The mental model was "app A must not reach app B's data". The
database's own replication is neither app A nor app B, holds no special
exemption, and was the one consumer of that database I had not enumerated.

## Related

- [Runbook: Migrate an App from the Shared PostgreSQL `app` Role](../runbooks/postgres-per-app-role-migration.md)
- [The Role I Was Migrating *From* Was the One That Broke](postgres-ownership-transfer-revokes-the-old-role.md)
