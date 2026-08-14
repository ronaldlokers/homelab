# Granting the bots' read-only role

Run once per database the beats read, and again for any new one.

CloudNativePG's `managed.roles` **creates a role and sets its password**. It
does not grant privileges — there is no declarative field for `GRANT SELECT`,
because grants are per-database and the operator manages the cluster. So the
role arrives able to log in and able to see nothing, which is the safe
direction to be incomplete in.

The alternative was to let the beats use each app's own role. Those exist, are
already encrypted in `role-secrets.yaml`, and can also `DROP TABLE`. A digest
that counts unread articles has no business holding a credential that could
delete them.

## The grants

Once per database, as the superuser. `commafeed`, `authentik` and `gatus` today:

```bash
for db in commafeed authentik gatus; do
  kubectl --context=production exec -n database postgres-cluster-4 -c postgres -- \
    psql -U postgres -d "$db" -c "
      GRANT CONNECT ON DATABASE \"$db\" TO bots;
      GRANT USAGE ON SCHEMA public TO bots;
      GRANT SELECT ON ALL TABLES IN SCHEMA public TO bots;
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bots;"
done
```

`postgres-cluster-4` is whichever pod is primary at the time:

```bash
kubectl --context=production get pods -n database \
  -l cnpg.io/cluster=postgres-cluster,cnpg.io/instanceRole=primary
```

The `ALTER DEFAULT PRIVILEGES` line is the one that matters six months from
now. Without it, a table created by a later migration is invisible to the
bots, and the beat fails with "permission denied for table" on a Sunday — long
after anyone remembers granting anything.

Note that default privileges apply to tables created by **the role that ran the
statement**. Migrations run as the application's own role, so run the `ALTER`
as that role too when a database has one:

```bash
kubectl --context=production exec -n database postgres-cluster-4 -c postgres -- \
  psql -U postgres -d commafeed -c \
    "ALTER DEFAULT PRIVILEGES FOR ROLE commafeed IN SCHEMA public GRANT SELECT ON TABLES TO bots;"
```

## Checking it

The role authenticates by password, so this has to go over TCP — `psql -U bots`
on the pod's own socket is peer authentication and fails as `FATAL: Peer
authentication failed`, which looks like a missing grant and is not one:

```bash
PW=$(kubectl --context=production get secret pg-role-bots -n database \
  -o jsonpath='{.data.password}' | base64 -d)
kubectl --context=production exec -n database postgres-cluster-4 -c postgres -- \
  env PGPASSWORD="$PW" psql -U bots -h 127.0.0.1 -d commafeed \
  -tAc "SELECT count(*) FROM feedentries;"
```

A number is success. `permission denied` means the grants did not run against
that database — they are per-database, and running them twice against the same
one is harmless.

And confirm it cannot write, which is the whole point:

```bash
kubectl --context=production exec -n database postgres-cluster-4 -c postgres -- \
  env PGPASSWORD="$PW" psql -U bots -h 127.0.0.1 -d commafeed \
  -tAc "DELETE FROM feedentries WHERE false;"
# ERROR:  permission denied for table feedentries
```
