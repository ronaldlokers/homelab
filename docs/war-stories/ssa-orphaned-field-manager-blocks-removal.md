# A Manager That Left Two Years Ago Still Owned Half the Field

Deleting a config block from git is supposed to delete it from the cluster. On
`staging/postgres-cluster` it deleted *most* of one, and what was left was
invalid.

The migration off CloudNativePG's in-tree `barmanObjectStore` (#377) is a
straight swap: remove `spec.backup.barmanObjectStore` from the Cluster, add a
`plugins:` entry pointing at an `ObjectStore`. It had already been done twice —
nightscout in staging (#386), and it was rehearsed against the same manifests in
CI. The third cluster failed the moment Flux applied it.

## The Problem

`infrastructure-configs` went unhealthy in staging with a dry-run failure:

```
Cluster/database/postgres-cluster dry-run failed (Invalid):
Cluster.postgresql.cnpg.io "postgres-cluster" is invalid:
spec.backup.barmanObjectStore.destinationPath: Invalid value: "":
  spec.backup.barmanObjectStore.destinationPath in body should be at least 1 chars long
```

Which reads like the manifest still contains a `barmanObjectStore` with an empty
`destinationPath`. It does not — the commit removed the block entirely.

The blast radius was not the backups. The cluster was untouched, still archiving
through the in-tree path exactly as before, because *nothing* from the commit
applied. But a Kustomization stuck on `Ready: False` blocks everything behind
it, and every Kustomization in this repo uses `wait: true`.

## The Investigation

The live object explained the error and nothing else:

```console
$ kubectl get cluster postgres-cluster -n database -o jsonpath='{.spec.backup}'
{"barmanObjectStore":{"data":{"compression":"gzip","jobs":2},
 "destinationPath":"s3://homelab-postgres-backups/staging-2026-07/", ...},
 "retentionPolicy":"14d","target":"prefer-standby"}
```

Still fully populated — because the failing apply was a *dry run*, so the object
was as it had always been. The question was what the apply would have produced,
and that is answered by `managedFields`, not by the spec:

```console
$ kubectl get cluster postgres-cluster -n database --show-managed-fields -o json | jq ...
flux-client-side-apply  Apply  {"f:barmanObjectStore": {"f:data": ..., "f:endpointURL": {},
                                "f:s3Credentials": ..., "f:wal": ...}, "f:retentionPolicy": {}}
kustomize-controller    Apply  {"f:barmanObjectStore": {"f:data": ..., "f:destinationPath": {},
                                "f:endpointURL": {}, "f:s3Credentials": ..., "f:wal": ...}, ...}
```

Two managers own the same block. Note what the first one does **not** own:
`destinationPath`.

## The Root Cause

`flux-client-side-apply` is the field manager Flux used before this repo moved
to server-side apply. It never went away — a field manager persists on the
object until something releases it, and nothing had rewritten this Cluster's
`backup` block since the switch.

Server-side apply removes a field when the manager that owns it stops sending
it, and only then. So the apply did exactly what it was asked:

- `kustomize-controller` stopped sending the block, releasing everything it
  owned, **including `destinationPath`**
- `flux-client-side-apply` still owned `data`, `endpointURL`, `s3Credentials`
  and `wal`, so those stayed

Leaving a `barmanObjectStore` with credentials, an endpoint and compression
settings, and no destination. The CRD requires one. Invalid.

The reason this was survivable is the reason it was confusing: it failed at
admission, so the half-deleted object never existed. Had `destinationPath` been
co-owned, the apply would have succeeded and the cluster would have kept
archiving to the old path while the manifest said it was on the plugin.

## The Solution

Remove the orphaned subtree directly, then reconcile so the plugin config lands
in the same minute — between the two commands the cluster has no archive
destination:

```bash
kubectl patch cluster postgres-cluster -n database \
  --type=json -p '[{"op":"remove","path":"/spec/backup/barmanObjectStore"}]'

flux reconcile kustomization infrastructure-configs --context=staging
```

CNPG announced the leftover on the way out:

```
Warning: Retention policies specified in .spec.backup.retentionPolicy are only used
by the in-tree barman-cloud support, which is not being used in this cluster.
```

That `retentionPolicy: 14d` is still there, owned solely by the dead manager and
therefore unreachable from git. It is inert — real retention lives on the
`ObjectStore` — but it is the same shape as #382: a setting that outlived the
feature that needed it.

## Prevention

**Check ownership before deleting a block, not after.** One command, and it is
the only reliable way to know whether removing a field from git will remove it
from the cluster:

```bash
kubectl get <kind> <name> -n <ns> --show-managed-fields -o json |
  jq '[.metadata.managedFields[] | {manager, operation}]'
```

More than one `Apply` manager on an object means a wholesale block deletion is
not a safe assumption.

Doing that across all five clusters afterwards showed the problem was confined
to exactly one:

```
staging/postgres-cluster:     flux-client-side-apply, kustomize-controller
staging/nightscout-cluster:   kustomize-controller
production/postgres-cluster:  kustomize-controller
production/immich-cluster:    kustomize-controller
production/nightscout-cluster: kustomize-controller
```

Which is why #386 was smooth and why the production migrations were expected to
be — and were. The one affected object is the oldest, created before the
server-side-apply switch and never rewritten since.

**The general shape:** a field manager is state that lives on the object, not in
git, and it survives long after the tool that created it stops running. Any
long-lived resource that predates a tooling change can have an invisible
co-owner, and you only discover it when you try to take something away.
