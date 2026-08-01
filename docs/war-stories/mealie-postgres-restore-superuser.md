# Restoring a Mealie Backup: Superuser, Version Match, and a Login Loop

A single "restore my backup" turned into a chain of four distinct failures, each
masking the next. None of them were about the thing the first error implied.

## The Problem

A freshly-deployed production Mealie (`v3.21.0`, empty) needed to be seeded from
a backup taken on another Mealie instance. Mealie's docs say the restore requires
a **superuser** database account:

> Restoring the Database when using Postgres requires Mealie to be configured with
> a postgres superuser account. [...] you will need to temporarily set the Mealie
> user to a superuser account.

That one sentence hides four separate traps in this repo's setup.

## Trap 1 — There is no `mealie` database role

Mealie's generic docs assume `POSTGRES_USER=mealie`. This repo does **not** do
that. Every app connects to the shared CloudNative-PG cluster as the single **`app`**
role (see any `deployment-postgres-patch.yaml`), authenticated by `pg_hba`
`host all app all trust`. So:

```sql
ALTER USER mealie WITH SUPERUSER;   -- ERROR: role "mealie" does not exist
```

The correct target is `app` — but see Trap 4 for why that's a bigger deal than it
looks.

## Trap 2 — A backup only restores into the *same* Mealie version

The first backup was one Mealie release behind production. Mealie restores the
backup's **raw schema as-is and does not migrate it forward**. The restore reverted
the live schema one Alembic revision (`d7b3ce6fa31a` → `7cf3054cbbcc`) and then the
`v3.21.0` code hit a column its ORM expected but the older schema lacked:

```
psycopg2.errors.UndefinedColumn: column users.show_announcements does not exist
```

The restore had already dropped all data before aborting, leaving the DB **empty
and one revision behind**. Confirmation:

```
backup  alembic_version = 7cf3054cbbcc      # from database.json inside the zip
live    alembic_version = 7cf3054cbbcc      # reverted by the failed restore
users/recipes/groups     = 0                # wiped, nothing imported
```

**Fix:** upgrade the *source* instance to match (`v3.21.0`), take a **fresh** backup
(`mealie_v3.21.0_*.zip`), and don't reuse the stale one. To un-break the empty live
DB, restart the pod — Mealie runs `alembic upgrade head` on startup, which advanced
it to the real `v3.21.0` head (`2187537c52b8`) and re-created `show_announcements`.

## Trap 3 — The restore *deletes users first*, so a mid-restore failure locks you out

With a version-matched backup but the superuser grant **not yet applied**, the
restore failed on exactly the privileged statement:

```
[SQL: SET session_replication_role = 'replica';]   # superuser-only
```

Crucially, the restore's **delete phase runs as the app role (no superuser needed)
and executes first**. So it wiped the default admin, *then* aborted at the
superuser step before importing anything:

```
users = 0        # default admin deleted
```

Now there's no account to log in with → no way to reach the admin UI → no way to
trigger a restore. A catch-22.

**Fix:** restart the pod. Mealie re-seeds the default admin
(`changeme@example.com` / `MyPassword`) whenever the users table is empty on
startup. Log in with that, *then* run the restore (this time with superuser granted
first).

## Trap 4 — `app` is shared, and the grant won't auto-revert

`app` owns **every** app's database in the cluster (linkding, immich, nightscout,
speedtest, mealie). Temporarily making it superuser means, for the restore window,
*every* app authenticates with a superuser credential — a cluster-wide blast radius,
not the single-app scope Mealie's docs assume.

Worse, `app` is **not** a CNPG-managed role (no `spec.managed.roles` in the Cluster
spec), so the operator neither reverts nor reconciles the change. The grant
**persists until reverted by hand**:

```sql
ALTER USER app WITH SUPERUSER;      -- before restore
-- ... run the restore in the Mealie UI ...
ALTER USER app WITH NOSUPERUSER;    -- IMMEDIATELY after; nothing does this for you
```

Keep the window as short as the restore itself.

## Trap 5 — Restore succeeds, but login loops forever

The restore finally worked (`49 recipes, 1 user: ronald@lokers.email`). But the UI
just bounced back to the login page. The logs showed authentication *succeeding*
and then the fresh token being *rejected on the very next call*:

```
POST /api/auth/token   → 200 OK     # credentials valid, token issued
GET  /api/users/self   → 401        # that same token rejected
```

Mealie's backup includes the JWT signing secret (`/app/data/.secret`). The restore
swapped it on disk out from under the running process, desyncing its
issue-vs-verify paths.

**Fix:** restart the pod so it reloads the restored secret consistently. Login
worked immediately after (`/api/users/self → 200`).

## The Working Sequence (what it should have been)

1. **Match versions.** Upgrade the source instance to the target version; take a
   fresh backup. Never restore an older backup into a newer Mealie.
2. **Get a login.** If the users table is empty, restart the pod to re-seed the
   default admin, and log in as `changeme@example.com` / `MyPassword`.
3. **Open the superuser window** — on the correct role — right before restoring:
   `ALTER USER app WITH SUPERUSER;`
4. **Restore** via the admin UI.
5. **Revert immediately:** `ALTER USER app WITH NOSUPERUSER;` (it will not
   auto-revert).
6. **Restart the pod** to realign the JWT secret, then log in with the *source*
   instance's credentials.

## Lessons Learned

1. **"Make the Mealie user a superuser" ≠ `ALTER USER mealie`** in a shared-`app`
   CNPG setup — the connecting role here is `app`, and elevating it exposes every
   database in the cluster. Scope and revert deliberately.
2. **Mealie backups are version-locked.** They carry a raw schema + an
   `alembic_version`; restore does not migrate. Restore into the matching version,
   *then* let startup migrations carry the data forward.
3. **The restore deletes before it imports.** A failure partway leaves you with an
   empty, admin-less instance. An empty users table + a pod restart re-seeds the
   default admin — the escape hatch out of the lockout.
4. **The first error names the symptom, not the cause.** `show_announcements does
   not exist` was a version mismatch; `SET session_replication_role` was a missing
   grant; the login loop was a swapped secret. Each needed the *live* DB state
   (`alembic_version`, row counts, `pg_roles.rolsuper`) to diagnose, not the error
   text alone.
5. **An imperative grant on a non-managed CNPG role is sticky.** Nothing reverts it
   for you — treat the revert as part of the same operation, not a later cleanup.
6. **Restart after a Mealie restore.** The JWT secret ships in the backup; without a
   restart you get a login that authenticates and then immediately 401s.

## Related Documentation

- [Repository Architecture](/docs/architecture.md) — shared `app` role / CNPG layout
- [PostgreSQL Cluster Disaster Recovery Bootstrap Mode](postgres-bootstrap-recovery.md)

## Commands Reference

**Read the backup's Mealie schema revision (before restoring)**:
```bash
# inside the mealie pod, from an unzipped backup
grep -oiE '"version_num"\s*:\s*"[a-z0-9]+"' database.json | head
```

**Compare live schema revision + row counts (the ground truth when errors mislead)**:
```bash
kubectl --context=production -n database exec postgres-cluster-3 -c postgres -- \
  psql -U postgres -d mealie -tAc \
  "SELECT version_num FROM alembic_version;
   SELECT count(*) FROM users; SELECT count(*) FROM recipes;"
```

**Temporarily elevate the shared app role for a restore, then revert**:
```bash
kubectl --context=production -n database exec postgres-cluster-3 -c postgres -- \
  psql -U postgres -c "ALTER USER app WITH SUPERUSER;"
# ...restore in the Mealie UI...
kubectl --context=production -n database exec postgres-cluster-3 -c postgres -- \
  psql -U postgres -c "ALTER USER app WITH NOSUPERUSER;"
# verify it actually reverted:
kubectl --context=production -n database exec postgres-cluster-3 -c postgres -- \
  psql -U postgres -tAc "SELECT rolname, rolsuper FROM pg_roles WHERE rolname='app';"
```

**Re-seed the default admin / realign the auth secret (pod restart)**:
```bash
kubectl --context=production -n mealie rollout restart deploy/mealie
kubectl --context=production -n mealie rollout status deploy/mealie
```
