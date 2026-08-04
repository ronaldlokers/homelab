# Migrate Nightscout's Data to Its Own PostgreSQL Cluster

**Status:** executed 2026-08-04 on both environments. Staging was structure-only (zero documents); production moved 22,293 documents across nine collections, verified count-for-count before cutover.
**Access:** `kubectl --context=<env>`, plus temporary pods in the `nightscout` namespace.
**Time:** ~30 minutes, most of it waiting on a backup.
**Rule:** the old data is never deleted. Every step up to the cutover is reversible by leaving it alone.

Context: [#249](https://github.com/ronaldlokers/homelab/issues/249). DocumentDB can only live in the one database named by `cron.database_name`, a cluster-wide postmaster setting, so Nightscout could not get a per-app database on the shared cluster. It gets a dedicated cluster instead ([#250](https://github.com/ronaldlokers/homelab/pull/250)).

## Three things that do not work

Establish these first, or a lot of time goes into rediscovering them.

**`pg_dump` cannot migrate the collections.** DocumentDB's catalog tables are extension members, so `pg_dump` silently omits them:

```sql
SELECT c.relname, e.extname FROM pg_depend d
JOIN pg_class c ON c.oid=d.objid JOIN pg_extension e ON e.oid=d.refobjid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE d.deptype='e' AND n.nspname='documentdb_api_catalog';
-- collections | documentdb
-- collection_indexes | documentdb
```

A `pg_dump` migration moves the `documents_N` tables and leaves the target with **no registered collections** — the documents exist, nothing references them, and the database reads as empty. It reports success. Do not use it.

**`mongodump` and `mongoexport` cannot talk to FerretDB here.**

```
Failed: error counting postgres.activity: (InternalError) fe_sendauth: no password supplied
```

Any DocumentDB operation that opens an outbound libpq connection as the *calling* role fails, because `pg_hba` has required scram for `app` since #166 and that internal connection supplies no password. `documentdb_api.drop_collection` fails the same way. `mongosh` is unaffected — use it.

**A hand-rolled FerretDB pod needs `FERRETDB_AUTH=false`.** The managed deployment sets it. Without it, FerretDB demands client credentials and forwards them to PostgreSQL, which fails as above. This costs three confusing attempts if missed.

## Pre-flight

```bash
# 1. target cluster healthy, extension present -> expect 3/3 and documentdb 0.107-0
kubectl --context=<env> get cluster nightscout-cluster -n database
kubectl --context=<env> exec -n database nightscout-cluster-1 -c postgres -- \
  psql -d postgres -tAc "SELECT extname, extversion FROM pg_extension WHERE extname LIKE 'documentdb%';"

# 2. fresh backup of the SOURCE cluster, and wait for it to complete
kubectl --context=<env> -n database create -f - <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  generateName: pre-nightscout-migration-
  namespace: database
spec:
  cluster:
    name: postgres-cluster
EOF
# NOTE: `backup` is ambiguous — it resolves to backups.longhorn.io. Qualify it.
kubectl --context=<env> get backups.postgresql.cnpg.io -n database \
  --sort-by=.metadata.creationTimestamp | tail -3
# -> phase must reach "completed" before continuing

# 3. record the source counts. THESE ARE THE ACCEPTANCE CRITERIA.
kubectl --context=<env> exec -n database <source-primary> -c postgres -- psql -d postgres -tAc "
DO \$\$ DECLARE r record; n bigint; BEGIN
 FOR r IN SELECT collection_id, collection_name FROM documentdb_api_catalog.collections ORDER BY 2 LOOP
   EXECUTE format('SELECT count(*) FROM documentdb_data.documents_%s', r.collection_id) INTO n;
   RAISE NOTICE '%: %', r.collection_name, n;
 END LOOP; END \$\$;"
```

## Procedure

### 1. Temporary FerretDB against the new cluster

```bash
NEWPW=$(kubectl --context=<env> get secret nightscout-cluster-app -n database \
          -o jsonpath='{.data.password}' | base64 -d)

kubectl --context=<env> -n nightscout run ferretdb-migrate \
  --image=ghcr.io/ferretdb/ferretdb:2.7.0 --restart=Never \
  --env="FERRETDB_POSTGRESQL_URL=postgres://app:${NEWPW}@nightscout-cluster-rw.database.svc.cluster.local:5432/postgres" \
  --env="FERRETDB_AUTH=false" \
  --env="FERRETDB_TELEMETRY=disable" \
  --labels="app=ferretdb"
```

`FERRETDB_AUTH=false` is not optional — see above. The `app=ferretdb` label matters: the namespace's NetworkPolicy only admits port 27017 to pods carrying it.

### 2. mongosh pod

```bash
kubectl --context=<env> -n nightscout run mongotools \
  --image=mongodb/mongodb-community-server:7.0-ubi8 --restart=Never \
  --command -- sleep 3600
```

### 3. Copy, paginated by OFFSET

Two things about this loop are counter-intuitive and both were learned the expensive way on production.

**Do not iterate a cursor.** `find().batchSize(n)` works for the first batch and then fails:

```
MongoServerError: FATAL:  password authentication failed for user "app"
connection to server at "127.0.0.1", port 5432
```

Cursor *continuation* opens an internal libpq connection that cannot authenticate. Any single-batch read (`find().limit(n).toArray()`) is fine. Empty collections never hit this, which is why staging — where every collection was empty — validated nothing about this path.

**Do not paginate by `_id`.** Nightscout's `_id` values are mixed BSON types — strings *and* ObjectIds:

```
min _id: "00Q8HVEndz0oL1Q23iQ22vye"     <- string
max _id: "6a723fe52c62841e3b027610"     <- ObjectId
```

MongoDB orders across types; DocumentDB's `$gt` does not compare across them. Keyset pagination silently stops at the type boundary — it copied 12,194 of 22,292 and reported success, because the "no more rows" signal and "no more rows *of this type*" are indistinguishable.

**And do not break on a short page.** Pages come back short when the reply hits 16MB, not only at the end. Only an empty page means done.

Both endpoints are addressed by pod IP; neither has a Service.

```bash
OLDIP=$(kubectl --context=<env> -n nightscout get pod -l app=ferretdb \
          -o jsonpath='{range .items[*]}{.metadata.name} {.status.podIP}{"\n"}{end}' \
          | grep -v migrate | awk '{print $2}')
NEWIP=$(kubectl --context=<env> -n nightscout get pod ferretdb-migrate -o jsonpath='{.status.podIP}')

kubectl --context=<env> -n nightscout exec mongotools -- \
  mongosh "mongodb://$OLDIP:27017/postgres" --quiet --eval "
const dst = new Mongo('mongodb://$NEWIP:27017').getDB('postgres');
const src = db;
const PAGE = 500;
src.getCollectionNames().sort().forEach(function(c) {
  if (c.startsWith('system.')) return;
  const n = src.getCollection(c).countDocuments({});
  if (n === 0) { dst.createCollection(c); print('empty  ' + c); return; }
  let copied = 0, skip = 0, dupes = 0;
  while (true) {
    const page = src.getCollection(c).find().sort({_id:1}).skip(skip).limit(PAGE).toArray();
    if (page.length === 0) break;                     // ONLY empty means done
    try { dst.getCollection(c).insertMany(page, {ordered:false}); copied += page.length; }
    catch (e) {                                        // duplicates are fine: re-runnable
      const w = e.writeErrors || (e.result && e.result.writeErrors) || [];
      dupes += w.length; copied += (page.length - w.length);
    }
    skip += page.length;
  }
  print('copied ' + c + ': ' + copied + '/' + n + (dupes ? '  (skipped ' + dupes + ' dupes)' : ''));
});
"
```

No credentials on either URI: both FerretDBs run with `FERRETDB_AUTH=false` and use their own configured PostgreSQL credentials.

### 4. Verify counts BEFORE cutting over

Re-run the pre-flight count query against the **new** cluster and compare, collection by collection. A mismatch means stop — the source is still live and still authoritative.

### 5. Cut over

Re-encrypt `apps/<env>/nightscout/ferretdb-url-secret.yaml` with the new cluster's `app` password and `nightscout-cluster-rw` as the host. `MONGODB_URI` on the Nightscout deployment does **not** change: DocumentDB lives in the `postgres` database on the new cluster too, so the Mongo-side database name is identical.

Commit, let Flux reconcile, and confirm FerretDB reconnects.

### 6. Verify, then clean up the temporary pods

```bash
kubectl --context=<env> exec -n database nightscout-cluster-1 -c postgres -- psql -tAc \
  "SELECT datname, usename, application_name, count(*) FROM pg_stat_activity
   WHERE usename IS NOT NULL GROUP BY 1,2,3;"          # -> FerretDB connections present

kubectl --context=<env> -n nightscout logs deploy/nightscout --since=10m | grep -icE "error|fail"
kubectl --context=<env> -n nightscout delete pod ferretdb-migrate mongotools
```

Nightscout cannot be curled from a scratch pod — the namespace NetworkPolicy admits ingress only from Traefik. Verify through its own logs and the connection list, or over the public URL.

## Rollback

Before step 5, there is nothing to roll back: the source cluster is untouched and still serving.

After step 5, revert the Secret commit. The old data is still in the shared cluster's `postgres` database and nothing has deleted it. Any glucose readings written after the cutover would exist only on the new cluster, so a rollback loses that window — which is the reason to verify counts at step 4 rather than after.

## Afterwards

The old `documentdb_data` in the shared cluster becomes dead weight. Leave it until the new cluster has a verified restore, then drop it — and only then can the shared cluster's `postgres` database have PUBLIC's `CONNECT` revoked, which is what finally closes #219 for every database.

## Related

- [Migrate an App from the Shared PostgreSQL `app` Role](postgres-per-app-role-migration.md)
- [PostgreSQL Cluster Disaster Recovery](postgresql-cluster-disaster-recovery.md)
