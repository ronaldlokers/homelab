# Stand Up a New App Database

For an app that has never had a database here. To move an existing app off the
shared `app` role, use
[postgres-per-app-role-migration.md](postgres-per-app-role-migration.md)
instead — that one has an ownership transfer this one does not need.

Seven steps. Six are declarative and Flux applies them. **The seventh is not,
and nothing in the first six hints that it is outstanding** — that is the whole
reason this page exists.

## What CloudNativePG does and does not do

It creates the role, sets its password from a Secret, creates the database, and
sets the database's owner. That is genuinely most of the job, and it is easy to
conclude it is all of it.

It does **not** revoke `PUBLIC`'s implicit `CONNECT`, and there is no
declarative field for it — the same reason the `bots` role's SELECT grants are
applied by hand. So a new database arrives on PostgreSQL's defaults, where
every role in the cluster can connect to it. That is precisely the state #219
removed everywhere else, and the `Database` CR reports `applied: true` while it
is true.

`PostgreSQLDatabaseNotIsolated` fires within a few minutes and is how this gets
caught. It has caught it at least once. Treat the alert as the backstop it is,
not as the control.

## Procedure

Paths below are `production`; staging is the same with `staging` substituted.
An app that runs in only one cluster only needs it there — Squirrel is
production-only because Campfire is.

### 1. Create the role credential

`role-secrets.yaml` is one SOPS file holding every `pg-role-*` Secret. Append a
document to it:

```bash
sops infrastructure/configs/production/cloudnative-pg/role-secrets.yaml
```

```yaml
---
apiVersion: v1
kind: Secret
metadata:
    name: pg-role-<app>
    namespace: database
type: kubernetes.io/basic-auth
stringData:
    username: <app>
    password: <32 random chars>
```

Generate the password rather than choosing one:

```bash
LC_ALL=C tr -dc 'A-Za-z0-9_-' </dev/urandom | head -c 32; echo
```

### 2. Mirror it into the app's namespace

The app reads the same object CloudNativePG reconciles the role against, rather
than a SOPS copy that can drift (#268). In
`infrastructure/configs/production/cloudnative-pg/kustomization.yaml`, as a
patch — the Secret's MAC covers unencrypted values too, so editing its metadata
in place breaks decryption:

```yaml
  - target:
      kind: Secret
      name: pg-role-<app>
    patch: |-
      apiVersion: v1
      kind: Secret
      metadata:
        name: pg-role-<app>
        annotations:
          reflector.v1.k8s.emberstack.com/reflection-allowed: "true"
          reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces: "<namespace>"
          reflector.v1.k8s.emberstack.com/reflection-auto-enabled: "true"
          reflector.v1.k8s.emberstack.com/reflection-auto-namespaces: "<namespace>"
```

The namespace is the app's, which is not always the app's name — Squirrel's is
`campfire`, because it lives with the other bots.

### 3. Declare the managed role

In `postgres-cluster.yaml`, under `managed.roles`:

```yaml
      - name: <app>
        ensure: present
        login: true
        passwordSecret:
          name: pg-role-<app>
```

### 4. Declare the database

`infrastructure/configs/production/cloudnative-pg/<app>-database.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: <app>
  namespace: database
spec:
  cluster:
    name: postgres-cluster
  name: <app>
  owner: <app>
```

Add it to that directory's `kustomization.yaml` `resources`.

**Expect one transient failure.** The role and the database land in the same
reconcile, and CloudNativePG may attempt the database first:

```
while creating database "<app>": ERROR: role "<app>" does not exist (SQLSTATE 42704)
```

It retries and settles within a minute. Wait for `applied: true` before moving
on:

```bash
kubectl --context=production get database.postgresql.cnpg.io -n database <app>
```

### 5. Wire the app to the credential

Read the username from the Secret, not as a literal, so the role name and its
password cannot drift apart (#219):

```yaml
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: pg-role-<app>
                  key: username
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: pg-role-<app>
                  key: password
```

Add `reloader.stakater.com/auto: "true"` to the Deployment. Without it a
rotation applies cleanly and the pod keeps using the old password, because env
is injected only at pod creation.

### 6. Open the network path

Two options, and the second is easy to miss:

- **The app has its own namespace:** label it
  `homelab.io/needs-database: "true"` in `namespace.yaml`. The shared
  `allow-apps-ingress` policy in `database` admits it. You still need an egress
  rule on the app's side — the namespace denies egress too.
- **The app shares a namespace that is not labelled** — anything in `campfire`,
  for instance — then `allow-apps-ingress` does not cover it and you write both
  halves explicitly, one policy in the app's namespace for egress and one in
  `database` for ingress. See `apps/base/squirrel/network-policies.yaml`.

### 7. Revoke PUBLIC's CONNECT

**The step that is not declarative.**

```bash
P=$(kubectl --context=production get pod -n database \
      -l cnpg.io/cluster=postgres-cluster,role=primary \
      -o jsonpath='{.items[0].metadata.name}')

kubectl --context=production exec -n database "$P" -c postgres -- \
  psql -U postgres -c "REVOKE CONNECT ON DATABASE <app> FROM PUBLIC;"
```

No matching `GRANT` is needed: the owner holds `CONNECT` implicitly, which is
also why this does not lock the app out of its own database.

> **Only ever run this against an app's own database.** On `postgres` the same
> command removes `streaming_replica`'s only grant and breaks replica rejoin,
> and every signal stays green for hours because existing replicas keep
> streaming on connections already open. See
> [revoke-that-only-broke-things-four-hours-later.md](../war-stories/revoke-that-only-broke-things-four-hours-later.md).

## Verify

Both halves of the invariant, for the whole cluster rather than the database
you just touched — this is the query the alert is built on:

```bash
kubectl --context=production exec -n database "$P" -c postgres -- psql -U postgres -c "
  SELECT d.datname,
         has_database_privilege('public', d.datname, 'CONNECT')            AS public_connect,
         has_database_privilege('streaming_replica', d.datname, 'CONNECT') AS replica_connect
  FROM pg_database d WHERE d.datistemplate = false ORDER BY d.datname;"
```

Expect `public_connect = f` everywhere, and `replica_connect = t` on
`postgres` **only**. A `t` in the first column is the alert's condition; an `f`
on `postgres` in the second is the four-hour outage.

Then confirm the shape matches a database that was already correct:

```bash
kubectl --context=production exec -n database "$P" -c postgres -- psql -U postgres -t -c \
  "select datname, array_to_string(datacl,' | ') from pg_database
   where datname in ('<app>','linkding');"
```

Both should read `=T/<owner> | <owner>=CTc/<owner>` — PUBLIC keeping TEMP and
losing CONNECT. A database showing `(default)` has a NULL ACL and has never
been revoked.

## Known drift in staging

Production bootstraps by physical restore and carries `pg_database.datacl` with
it. **Staging bootstraps with `initdb` and does not** — so every rebuild returns
it to PostgreSQL's defaults, and the revokes have to be reapplied by hand.

As of 2026-08-15, staging has `public_connect = t` on `app`, `authentik` and
`nightscout`. Run the verification query above against `--context=staging`
after any cluster rebuild, and expect to redo step 7 for each database.

This is the standing argument for making the revoke declarative — a Job, or a
Kyverno policy, or an `initdb.postInitApplicationSQL` hook. Nobody has, and
until someone does the alert is the only thing between a rebuild and a cluster
where every role can reach every database.
