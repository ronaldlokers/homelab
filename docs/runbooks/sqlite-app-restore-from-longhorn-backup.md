# Restore a SQLite App from a Longhorn Backup

## Quick Reference

- **Severity**: Varies — this is both a drill and a real recovery procedure
- **Estimated Time to Resolve**: 15 minutes, measured
- **Applies to**: Campfire, Fizzy, linkding — every app whose entire state is
  SQLite plus uploads on one PVC
- **Environment**: Production
- **Prerequisites**: `kubectl`, a completed Longhorn backup, ~200MB of spare
  Longhorn capacity

**Verified 2026-08-12** against `campfire-data`, restoring the 2026-08-11
00:01 backup. The restore was usable: `PRAGMA integrity_check` returned `ok`,
all 7 rooms and 6 users were present, and the 12 `active_storage_blobs` rows
matched exactly 12 files on disk.

## Why this exists

For the Postgres-backed apps a volume backup is a second line of defence behind
CNPG's WAL archiving to B2. For these apps **the volume backup is the entire
story**, and one documented caveat made it worth exercising rather than assuming:

> A Longhorn snapshot of a live SQLite database can capture a torn write,
> because the file may be written at any moment.

That did not happen in this test. It is still a sample of one, so re-run this
after any change to the storage path.

## The two findings that change what you do

### 1. The `-wal` file is not optional, and losing it is silent

The restore contained three files: `production.sqlite3`, `-shm` and `-wal`. The
WAL was 206KB and **newer** than the database.

Measured on one restore, same backup:

| Opened as | Messages | Newest message |
|---|---|---|
| `production.sqlite3` alone (`immutable=1`) | 29 | 2026-08-10 **22:51:23** |
| All three files together | 31 | 2026-08-10 **23:57:10** |

Copying out only `production.sqlite3` loses the last **66 minutes** of writes.
Nothing warns you: the database opens, `integrity_check` passes, and the data
simply is not there.

**Always copy the whole `db/` directory.** Never cherry-pick the `.sqlite3`.

### 2. Opening the restore read-write destroys the evidence

SQLite checkpoints the WAL into the main file on close and then removes
`-wal` and `-shm`. That happened here on the first inspection — the files were
gone afterwards and the main file had grown by 4KB.

It is not data loss, but it mutates the only artefact you have, and it means a
second look answers a different question than the first.

**Inspect a copy, or open with `?immutable=1`:**

```python
sqlite3.connect("file:/restored/db/production.sqlite3?immutable=1", uri=True)
```

## Procedure

### Step 1: Find the backup

```bash
kubectl get volumes.longhorn.io -n longhorn-system -o json |
  jq -r '.items[] | select(.status.kubernetesStatus.pvcName=="campfire-data") |
         "\(.metadata.name) lastBackup=\(.status.lastBackup) at \(.status.lastBackupAt)"'

kubectl get backup <name> -n longhorn-system -o jsonpath='{.status.url}'
```

The `url` is what the restore needs. `state: Completed` is what makes it usable.

### Step 2: Restore into a scratch volume

**Why**: restoring into a *new* volume never touches the running app, so this
is safe to rehearse whenever.

```yaml
apiVersion: longhorn.io/v1beta2
kind: Volume
metadata:
  name: campfire-restore-test
  namespace: longhorn-system
spec:
  fromBackup: "<the url from step 1>"
  size: "5368709120"        # must match the original
  numberOfReplicas: 1       # a scratch copy needs no redundancy
  frontend: blockdev
```

Wait for it — the volume attaches itself, pulls the backup, then detaches:

```bash
kubectl wait --for=jsonpath='{.status.state}'=detached \
  volume/campfire-restore-test -n longhorn-system --timeout=300s
```

`restoreRequired: false` on a `detached` volume is the finished state. It took
under a minute for 192MB.

### Step 3: Mount it

A static PV with `storageClassName: ""` bound to the Longhorn volume by
`volumeHandle`, a PVC naming it via `volumeName`, and any pod to look with.
`python:3.13-alpine` is a good choice: the image is already on the nodes and
Python ships `sqlite3`, so nothing has to be installed.

Full manifest in the PR that added this runbook.

### Step 4: Verify

**Why**: "the volume restored" is not the same claim as "the data is usable".

```bash
kubectl exec -n restore-test inspect -- python3 -c '
import sqlite3
db = sqlite3.connect("file:/restored/db/production.sqlite3?immutable=1", uri=True)
print(db.execute("PRAGMA integrity_check").fetchone()[0])
for t in ("users","rooms","messages","active_storage_blobs"):
    print(t, db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])'
```

Check all four:

- `integrity_check` returns `ok`
- row counts are plausible for the backup's timestamp
- `active_storage_blobs` matches `find /restored/files -type f | wc -l` — this
  is what proves the database and the attachments agree, and it is the check
  most likely to catch a partial restore
- the newest row is close to the backup time. Ours was 4 minutes before it

### Step 5: Clean up

```bash
kubectl delete namespace restore-test
kubectl delete pv campfire-restore-test
kubectl delete volume campfire-restore-test -n longhorn-system
```

Delete the namespace first: it removes the pod and PVC, so nothing is still
attached when the volume goes.

## Using it for real

To restore *into* the app rather than a scratch volume, the difference is that
the app must be stopped first — one replica, `strategy: Recreate`, and SQLite
does not tolerate two writers:

1. `kubectl scale deploy/campfire -n campfire --replicas=0`
2. Restore to a new volume as above, with `numberOfReplicas: 3`
3. Repoint the app's PVC at the restored volume, or copy `db/` and `files/`
   across from a pod that mounts both
4. Scale back to 1 and check the log for a clean boot

**Copy the whole `db/` directory** if you go the copy route. See finding 1.

## Prevention

- The recurring job labels on the PVC are the entire backup story. Confirm they
  are still there after any PVC change:
  `kubectl get pvc campfire-data -n campfire -o jsonpath='{.metadata.labels}'`
- The Campfire morning briefing reports a volume whose newest backup is older
  than 26 hours, so a silently stopped backup job surfaces on its own.
- Re-run this drill after changing the storage class, the Longhorn version or
  the backup target. One successful restore is evidence about one backup.
