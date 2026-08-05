# Migrate an App from the Shared `app` Role to Its Own

**Status:** executed on staging and production 2026-08-04 — ten databases. Staging was clean; production cost Tandoor 11 minutes of 500s because step 1 was missing `GRANT <role> TO app`. That statement is now in step 1 and is the single most important line here.
**Access:** `kubectl exec -n database <primary> -c postgres -- psql` on the target cluster.
**Time:** ~10 minutes per database, plus a Flux reconcile between steps.
**Rule:** one application at a time, verified running before the next. Steps 1-5 are ordered, and the order is the whole point — see [Why the order matters](#why-the-order-matters).

Context: [#219](https://github.com/ronaldlokers/homelab/issues/219). Before this, every app authenticated as the shared `app` role, which owns every database. Any app that could read its own `postgres-app-credentials` Secret held a credential valid against all of them, including Nightscout's glucose store.

## Prerequisites

The role must already exist. Roles are declared in `cluster.spec.managed.roles` with a SOPS-encrypted password Secret alongside — see `infrastructure/configs/<env>/cloudnative-pg/postgres-cluster.yaml` and `role-secrets.yaml`. Declaring one is inert: it exists, it can log in, and nothing uses it.

```bash
# role exists and can log in -> expect one row, rolcanlogin = t
kubectl exec -n database <primary> -c postgres --context=<env> -- \
  psql -tAc "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname='<role>';"
```

## Procedure

Substitute `<db>` (database name), `<role>` (new role, same name by convention) and `<primary>` from `kubectl get cluster -n database postgres-cluster -o jsonpath='{.status.currentPrimary}'`.

### 1. Grant, then transfer ownership of the objects

Both in one transaction, `GRANT` first. The app is still connected as `app` throughout — which is the whole reason for the first statement below.

> **`GRANT <role> TO app` is not optional.** `app`'s access to these tables comes from *owning* them and nothing else — it holds no grants. The moment ownership moves, `app` loses every privilege it had, and the running pod starts returning `permission denied for table …` while still holding its old credentials. Making `app` a member of the new role carries its access across the gap. Measured on production 2026-08-04: skipping this took Tandoor down for **11 minutes**, and left five other apps equally broken but silent, because only Tandoor's health probe touched the database.

```sql
\c <db>

BEGIN;

-- Keeps the CURRENTLY CONNECTED role working after ownership moves. Dropped in
-- step 6, once the app is verified on its new credentials.
GRANT <role> TO app;

-- Grants for the new role: these let it work before it owns anything.
GRANT USAGE, CREATE ON SCHEMA public TO <role>;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO <role>;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO <role>;

-- Then ownership, scoped to objects inside THIS database only.
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT schemaname, tablename FROM pg_tables WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO <role>', r.schemaname, r.tablename);
  END LOOP;
  FOR r IN
    SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO <role>', r.sequencename);
  END LOOP;
  FOR r IN
    SELECT viewname FROM pg_views WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER VIEW public.%I OWNER TO <role>', r.viewname);
  END LOOP;
END $$;

ALTER SCHEMA public OWNER TO <role>;

COMMIT;
```

Verify nothing is still owned by `app`, **and that `app` can still read** — the second check is the one that would have caught the production outage:

```sql
SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1;

-- expect t. If this is f, the running pod is already broken.
SELECT has_table_privilege('app', 'public.<any-table>', 'SELECT');
```

> **Do NOT use `REASSIGN OWNED BY app TO <role>`.** It is the obvious command and it is wrong here. `REASSIGN OWNED` also covers **shared objects** — and `app` owns every database in the cluster, so running it inside one database hands *all* of them to that role. The loop above is scoped to objects in the current database on purpose.

### 2. Switch the application's credentials

Two files per app, both in `apps/<env>/<app>/`:

```yaml
# deployment-*-patch.yaml — read the username from the Secret, not a literal, so
# the role name and its password cannot drift apart
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: postgres-app-credentials
      key: username
```

```bash
# postgres-credentials-secret.yaml — username and password from pg-role-<role>
sops apps/<env>/<app>/postgres-credentials-secret.yaml
```

The password is CloudNativePG's, from the `pg-role-<role>` Secret in the `database` namespace. Secrets are namespace-scoped, so the app namespace needs its own copy:

```bash
kubectl get secret pg-role-<role> -n database --context=<env> \
  -o jsonpath='{.data.password}' | base64 -d
```

Commit, let Flux reconcile, and **wait for the pod to be running on the new credentials** before continuing. This step is reversible; the next one is not, cheaply.

### 3. Transfer ownership of the database itself

```yaml
# infrastructure/configs/<env>/cloudnative-pg/<app>-database.yaml
spec:
  owner: <role>   # was: app
```

CloudNativePG issues `ALTER DATABASE <db> OWNER TO <role>` for this field. Confirm it actually landed — this is a controller doing the work, not you:

```bash
kubectl get database <app> -n database --context=<env> \
  -o jsonpath='{.spec.owner} applied={.status.applied} msg={.status.message}{"\n"}'
```

> If `applied=false` with `cluster resource has been deleted, skipping reconciliation`, the Database controller is stuck at a stale generation and is **not** applying anything. Editing `spec.owner` bumps the generation and normally unsticks it — but verify `applied=true` before believing the owner changed, because a silent no-op here makes step 4 look successful while achieving nothing.

### 4. Revoke PUBLIC's CONNECT, and drop the bridge

This is the step that actually creates the isolation. Everything before it was preparation.

```sql
REVOKE CONNECT ON DATABASE <db> FROM PUBLIC;
GRANT  CONNECT ON DATABASE <db> TO <role>;

-- Undo the step-1 bridge. Leaving it in place means `app` still inherits
-- everything <role> owns, which is most of what this migration set out to stop.
\c <db>
REVOKE <role> FROM app;
```

### 5. Verify both directions

One-line matrix for the whole cluster:

```bash
kubectl exec -n database <primary> -c postgres --context=<env> -- psql -tAc \
  "SELECT d.datname, pg_get_userbyid(d.datdba) AS owner,
          has_database_privilege('public', d.datname, 'CONNECT') AS public_connect
   FROM pg_database d WHERE datistemplate=false ORDER BY 1;"
```

Expected after a migration (staging, 2026-08-04):

```
app|app|t
commafeed|commafeed|f      <- migrated
linkding|linkding|f        <- migrated
mealie|mealie|f            <- migrated
nightscout|app|t           <- not yet migrated
postgres|postgres|t
tandoor|tandoor|f          <- migrated
```

Then prove isolation in both directions — the negative test is the one that matters:

```bash
# the app's own role can reach its own database -> expect 1
kubectl exec -n database <primary> -c postgres --context=<env> -- \
  psql "postgresql://<role>:<pw>@localhost/<db>" -tAc "SELECT 1;"

# the shared app role can NOT -> expect: permission denied for database <db>
kubectl exec -n database <primary> -c postgres --context=<env> -- \
  psql "postgresql://app:<app-pw>@localhost/<db>" -tAc "SELECT 1;"

# another app's role can NOT -> expect: permission denied for database <db>
kubectl exec -n database <primary> -c postgres --context=<env> -- \
  psql "postgresql://<other-role>:<pw>@localhost/<db>" -tAc "SELECT 1;"
```

And the app itself, which is the only test that counts:

```bash
kubectl get pods -n <app> --context=<env>          # 1/1 Running, restarts unchanged
kubectl exec -n database <primary> -c postgres --context=<env> -- \
  psql -tAc "SELECT datname, usename, count(*) FROM pg_stat_activity
             WHERE datname='<db>' GROUP BY 1,2;"   # connections as <role>, not app
```

## Why the order matters

Each step depends on the previous one having landed, and three of the four orderings fail:

| If you… | What happens |
|---|---|
| transfer ownership without `GRANT <role> TO app` | **the running app breaks immediately** — `app`'s access was ownership, and it just lost it |
| transfer ownership before granting the new role | a window where the new role can read nothing |
| revoke PUBLIC before switching credentials | the app is locked out of its own data |
| revoke PUBLIC before transferring database ownership | **nothing happens, silently** — an owner holds `CONNECT` implicitly, so `app` still connects |
| use `REASSIGN OWNED` | every database in the cluster changes owner, not just this one |

**The first one is the expensive one, and this runbook originally got it wrong.** The instinct is to protect the role being migrated *to*. The role at risk is the one being migrated *from*: `app` holds no grants anywhere, so ownership is its only access, and step 1 takes it away while step 2 has not yet shipped new credentials to the pod.

Measured on production, 2026-08-04, migrating six databases at once:

```
14:15  ownership transferred away from app
14:16  tandoor pod restarts, reconnects as app, ProgrammingError:
       permission denied for table auth_user   (234 occurrences)
14:26  new credentials roll out, pod recovers
```

Eleven minutes. The other five applications were in exactly the same state and looked fine — their health probes do not query the database, and no user happened to load a page. Tandoor was not the unlucky one; it was the only one instrumented well enough to notice.

The fourth is the one that cost the most *time*, as opposed to uptime. On mealie the revoke *appeared* to succeed — no error, `has_database_privilege('public', …)` returned `f` — and `app` could still connect, because `app` still owned the database. The negative test in step 5 is what caught it; `REVOKE` returning success is not evidence of anything.

## Root cause of the original problem

PostgreSQL grants `CONNECT` on every new database to `PUBLIC` by default. With one shared role owning every database, that default plus a shared credential meant any app's Secret was a cluster-wide credential. [#166](https://github.com/ronaldlokers/homelab/issues/166) moved `pg_hba` from `trust` to `scram-sha-256`, which stopped *unauthenticated* access — it did nothing about an authenticated role reaching databases that were never its own.

Authentication and authorization are separate problems, and fixing the first can look like fixing both.

## Prevention

New apps should never touch the `app` role. Create the database with `spec.owner: <role>` from the start and the whole procedure above collapses into declaring the role — there are no existing objects to transfer and PUBLIC's `CONNECT` can be revoked immediately.

## Revoking on the `postgres` database breaks replication

Everything above concerns per-app databases. The shared `postgres` database is a
different animal, and revoking `PUBLIC`'s `CONNECT` there **breaks CloudNativePG
replica rejoin** — silently, until the next time a replica restarts.

```
FATAL: permission denied for database "postgres"   user=streaming_replica
```

`streaming_replica` is **not** a superuser, so it does not bypass `CONNECT`, and
CNPG uses `database=postgres` for the connection a replica makes when it rejoins.
Nothing fails at the moment of the revoke: existing replicas hold open
connections and keep streaming. The break only appears on a switchover, a node
failure, or any pod restart — precisely when HA is supposed to save you.

Measured on production 2026-08-05: a planned switchover left the demoted instance
stuck `0/1 Running` for 13 minutes, retrying every 5 seconds, until:

```sql
GRANT CONNECT ON DATABASE postgres TO streaming_replica;
```

after which it rejoined immediately.

**So if you revoke on `postgres`, grant `streaming_replica` back in the same
transaction.** Checking live connections beforehand does not protect you —
`streaming_replica` only connects when a replica rejoins, so it is absent from
`pg_stat_activity` exactly when you are deciding whether the revoke is safe.

```sql
-- expect t on every cluster
SELECT has_database_privilege('streaming_replica', 'postgres', 'CONNECT');
```

Worth knowing: none of this is declarative. The revokes and grants are hand-run
SQL, so a cluster rebuilt from backup comes back with PostgreSQL's defaults —
`PUBLIC` able to connect everywhere, and this problem gone along with the
isolation it came from.

## Not covered by this runbook

**Apps sharing the `postgres` database.** Nightscout reaches Postgres through FerretDB, which stores its data in the shared `postgres` database rather than a per-app one. Revoking `CONNECT` there would also hit the CloudNativePG operator and the monitoring exporter. That needs its own design, not this recipe.

**The `streaming_replica` role.** Still `host all streaming_replica all trust` in `pg_hba`, and it has no password set, so it cannot be switched to scram as-is. CNPG authenticates replicas with client certificates over TLS, which makes the line look redundant — removing it needs a verified test that replication survives, staging first.

## Related

- [PostgreSQL Cluster Disaster Recovery](postgresql-cluster-disaster-recovery.md)
- [PostgreSQL Replica NetworkPolicy Dataplane Sync](postgres-replica-networkpolicy-dataplane-sync.md)
