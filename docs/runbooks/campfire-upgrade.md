# Upgrade Campfire

## Quick Reference

- **Severity**: Planned change, but with a one-way step in the middle
- **Estimated Time**: 10 minutes, of which ~1 minute is downtime
- **Environment**: Production
- **Prerequisites**: A completed backup from the last 26 hours, and the current
  schema version written down
- **Related**: [`sqlite-app-restore-from-longhorn-backup.md`](sqlite-app-restore-from-longhorn-backup.md)
  — the rollback path when a migration has run

## What makes this risky

Not the release cadence, which is roughly weekly. Four properties of *this*
deployment:

- **All state is one PVC.** SQLite and every attachment. There is no second copy
  anywhere else.
- **One replica, `strategy: Recreate`.** The old pod is gone before the new one
  starts, so downtime is unavoidable and the two never overlap.
- **Migrations run on every boot, before the server.** From `bin/start-app` in
  the image:

  ```bash
  #!/bin/bash -e
  rm -f tmp/pids/server.pid
  ./bin/rails db:prepare
  ./bin/rails server
  ```

  `bash -e` means a failed `db:prepare` never reaches `rails server`.
- **A migration is a one-way door.** Once it has altered the schema, the
  previous image may not read the database either — so rollback is *not*
  simply re-pinning the old tag.

## Before you start

### 1. Does this release contain migrations?

**This single question decides what rollback means.** Ask it before upgrading,
not after something breaks.

```bash
# What the database is at now — write this number down
kubectl exec -n campfire deploy/campfire -c campfire -- bin/rails runner \
  'puts ActiveRecord::Base.connection.select_value("SELECT MAX(version) FROM schema_migrations")'

# What the target contains
gh api repos/basecamp/once-campfire/contents/db/migrate --jq '.[].name' | sort | tail -5
```

If the newest upstream migration is one the database already has, the upgrade
adds no migrations and **rollback is a re-pin**. If it is newer, rollback means
a restore.

Measured 2026-08-12: schema at `20251212154340`, 15 migrations, identical to
upstream's newest — so the v1.4.9 → `main` move was migration-free.

### 2. Confirm a recent backup

```bash
kubectl get volumes.longhorn.io -n longhorn-system -o json |
  jq -r '.items[] | select(.status.kubernetesStatus.pvcName=="campfire-data") |
         "\(.status.lastBackup) at \(.status.lastBackupAt)"'
```

Older than 26 hours means the daily job has not run. Stop and find out why —
the morning briefing reports this too, so it should not be a surprise.

### 3. Note what you are on

```bash
kubectl get deploy campfire -n campfire -o jsonpath='{.spec.template.spec.containers[0].image}'
```

## Upgrade

The image is pinned in `apps/production/campfire/kustomization.yaml`. It is
currently a **`main` digest** rather than a release tag — see the Campfire doc
for why. Renovate proposes digest bumps as PRs.

1. Bump `digest:` (or set `newTag:` and drop `digest:` when moving back to a
   release) in a PR.
2. Merge. Flux applies within a minute, or `flux reconcile kustomization apps`.
3. Watch it:

```bash
kubectl rollout status deploy/campfire -n campfire --timeout=300s
kubectl logs -n campfire deploy/campfire -c campfire -f
```

### What a healthy boot looks like

Measured 2026-08-12 — **31 seconds** from pod creation to Ready. The sequence:

```
Redis is starting                                  redis, in the same container
{"msg":"Server started","http":":80"}              thrust is up
{"msg":"Unable to proxy request","path":"/up"}     expected, briefly
{"path":"/up","status":502}                        readiness failing while Rails boots
resque-pool-manager: started manager               workers up
Pool contains worker PIDs: [...]
```

**The `/up` 502s are normal.** thrust answers before Rails does, so the
readiness probe fails for a few seconds on every boot. They are only a problem
if they never stop.

A migration-free boot prints **nothing** from `db:prepare`. Silence there is
success, not a missing step.

## When it goes wrong

### Stuck or failed migration

**Signature**: the pod never becomes Ready and restarts repeatedly —
`CrashLoopBackOff`. Because of `bash -e`, `rails server` is never reached, so
there is no "Server started" line at all.

```bash
kubectl logs -n campfire deploy/campfire -c campfire --previous | grep -iE \
  "StandardError|ActiveRecord|Migration|rails aborted|SQLite3"
```

**Do not delete the pod repeatedly.** Each restart runs `db:prepare` again
against a database a previous attempt may have half-migrated.

Stop the loop first, so nothing else touches the database while you decide:

```bash
kubectl scale deploy/campfire -n campfire --replicas=0
```

### Then pick a rollback, using the answer from "Before you start"

**No migrations in the release** — re-pin and go back:

1. Revert the image PR
2. `flux reconcile kustomization apps --context=production`
3. `kubectl scale deploy/campfire -n campfire --replicas=1`

**Migrations ran** — the old image may not read the schema. Restore the volume:
follow [`sqlite-app-restore-from-longhorn-backup.md`](sqlite-app-restore-from-longhorn-backup.md),
then re-pin the old image before scaling back up. Everything since the last
backup is lost, which is why step 2 of the pre-flight is not optional.

Check whether a migration actually landed before assuming the worst:

```bash
kubectl exec -n campfire deploy/campfire -c campfire -- bin/rails runner \
  'puts ActiveRecord::Base.connection.select_value("SELECT MAX(version) FROM schema_migrations")'
```

Unchanged from the number you wrote down means nothing was altered, and a
re-pin is safe.

### Pod will not start for other reasons

`SECRET_KEY_BASE` and the `VAPID_*` pair **must survive the upgrade**. Rotating
`SECRET_KEY_BASE` invalidates every session; rotating the VAPID pair silently
stops every installed PWA receiving notifications until it re-subscribes. They
come from the SOPS secret and should never change during an upgrade.

## After

```bash
kubectl get pods -n campfire -l app=campfire
curl -sS -o /dev/null -w '%{http_code}\n' https://campfire.ronaldlokers.nl/
```

Then post something through a bot — the alert bridge and the status bot both
authenticate by a key in a URL, and a bot key surviving an upgrade is worth
confirming rather than assuming:

```bash
kubectl exec -n campfire deploy/campfire -c campfire -- bin/rails runner \
  'puts User.where(role: "bot").pluck(:name).join(", ")'
```

## Prevention

- Take the schema version reading **before** every upgrade. It is the only
  thing that makes the rollback decision cheap.
- Read the upstream release notes for migration mentions; roughly weekly
  releases means this happens often enough to be worth a habit rather than a
  one-off investigation.
- Campfire currently runs an unreleased `main` digest for
  basecamp/once-campfire#239. Move back to a release tag as soon as one carries
  it — an unreleased build has no notes to read.
