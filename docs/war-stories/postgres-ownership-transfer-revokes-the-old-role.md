# The Role I Was Migrating *From* Was the One That Broke

Splitting one shared PostgreSQL role into seven per-app roles took two hours of careful sequencing, a written runbook, and a clean staging rehearsal across four databases. Three hours after the runbook was written, the production run took Tandoor down for eleven minutes.

The runbook was wrong. It protected the wrong role.

## The Problem

### What the migration was for

Every application authenticated as one shared `app` role, which owned every database in the cluster. Any app that could read its own `postgres-app-credentials` Secret held a credential valid against all of them, including Nightscout's glucose store.

Fixing that means giving each app its own role, moving ownership of the existing objects, and revoking `PUBLIC`'s default `CONNECT`.

### The sequencing everyone worries about

The obvious hazard is the role being migrated **to**. It starts with nothing: no grants, no ownership. If the application's credentials are switched before that role can read anything, the app breaks.

So the runbook opened with grants:

```sql
GRANT USAGE, CREATE ON SCHEMA public TO <role>;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO <role>;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO <role>;
-- then ALTER ... OWNER TO <role> for every object
```

Grant first, transfer second. Staging ran clean: four databases, zero downtime, zero restarts.

### Production

Six databases at once. The transfer reported exactly what it should:

```
### tandoor
 tandoor    |    99
(1 row)
```

Ninety-nine tables, all owned by the new role, nothing left behind. All six the same. Every pod still `Running`.

Eleven minutes later Tandoor was serving 500s.

```
django.db.utils.ProgrammingError: permission denied for table auth_user
10.42.1.1 - - [04/Aug/2026:14:16:13 +0000] "GET / HTTP/1.1" 500 145 "kube-probe/1.33"
```

## The Root Cause

`app` held **no grants anywhere**. Its access to every table in every database came from *owning* them, and nothing else.

Transferring ownership therefore did not just give the new role access. It took the old role's access away — completely, at the instant of the `ALTER`, while the running pod was still connected with `app` credentials and the deployment carrying the new ones had not yet been merged.

Confirmed afterwards:

```sql
SELECT has_table_privilege('app', 'public.auth_user', 'SELECT');
-- f

SELECT grantee, count(*) FROM information_schema.role_table_grants
 WHERE table_schema = 'public' GROUP BY 1;
-- tandoor | 693        <- app does not appear at all
```

The timeline:

```
14:15  ALTER ... OWNER TO tandoor     app loses everything
14:16  pod restarts, reconnects as app, permission denied  (x234)
14:26  new credentials roll out       recovered
```

The gap between step 1 and step 2 is not a race to be won by ordering the statements correctly. It is a window whose width is however long it takes a Git commit to pass CI, merge, reconcile, and roll a pod — around eleven minutes here, and unbounded if a check fails.

## Why Five Other Applications Looked Fine

Speedtest, Linkding, Commafeed, Mealie and Gatus went through exactly the same transfer at exactly the same moment, with exactly the same loss of privilege. All five reported `Running`, `1/1`, restarts unchanged.

They were equally broken. They just had nothing checking.

Their readiness and liveness probes hit an HTTP endpoint that does not query the database, and no user happened to load a page in those eleven minutes. Tandoor's probe requests `/`, Django renders it by reading `auth_user`, the query failed, the probe failed, and the container restarted — which is the only reason the breakage surfaced at all.

**Tandoor was not the unlucky one. It was the only one instrumented well enough to notice.** A probe that never touches the dependency it is monitoring reports on the web server, not the application.

## The Solution

One statement, first in the transaction:

```sql
BEGIN;

-- Keeps the CURRENTLY CONNECTED role working after ownership moves.
GRANT <role> TO app;

GRANT USAGE, CREATE ON SCHEMA public TO <role>;
-- ... grants for the new role, then the ownership transfer
COMMIT;
```

Role membership means `app` inherits everything `<role>` owns, so its access survives the transfer regardless of how long the credential rollout takes. Once the app is verified running on its new credentials, the bridge comes down with the `PUBLIC` revoke:

```sql
REVOKE CONNECT ON DATABASE <db> FROM PUBLIC;
GRANT  CONNECT ON DATABASE <db> TO <role>;
REVOKE <role> FROM app;
```

And the verification step after the transfer now asserts the thing that was never asserted:

```sql
-- expect t. If this is f, the running pod is already broken.
SELECT has_table_privilege('app', 'public.<any-table>', 'SELECT');
```

That query takes a second and would have caught this before a single pod noticed.

## Lessons

**In a privilege migration, check the role you are migrating away from.** All the attention goes to the new role, because it starts with nothing and obviously needs grants. The old role is assumed safe because it is currently working — but if its access came from ownership, transferring ownership *is* the revoke. It is the same statement seen from the other side.

**Ownership is a privilege, not a label.** `ALTER TABLE ... OWNER TO` reads like bookkeeping — updating who is responsible for an object. It is a simultaneous grant and revoke, and only the grant half is visible in the command.

**"Every pod is Running" is not "every pod is working".** Six applications lost database access at the same instant. One reported it. The difference was entirely in whether the health check exercised the dependency that had just been removed — which is worth knowing about a fleet *before* an incident, not after.

**A clean rehearsal is evidence about the rehearsal.** Staging ran four databases through the same broken sequence without a visible failure, because staging apps are idle and nothing queried during the window. The rehearsal did not prove the procedure was correct; it proved the procedure was survivable at zero traffic.

**Write the runbook, then distrust it on first contact.** The document was three hours old, written immediately after a successful staging run, with the two traps it *had* hit documented in detail. It was still missing the most expensive one — because that one had never fired.

## Related

- [Runbook: Migrate an App from the Shared PostgreSQL `app` Role](../runbooks/postgres-per-app-role-migration.md)
- [47 Restarts in 19 Hours, Every One Exiting Zero](liveness-without-startup-probe.md)
- [The App That Started Perfectly and Served Nothing](tandoor-service-env-var-collision.md)
