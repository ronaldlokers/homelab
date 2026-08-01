# Reloader

[Reloader](https://github.com/stakater/Reloader) is a Kubernetes controller that watches ConfigMaps and Secrets and performs a rolling restart of the workloads that consume them.

**Version**: chart 2.2.14 (appVersion v1.4.19)

**Environment scope**: staging and production. Staging came first and was validated there before promotion — see *Validation on staging* below.

## Why it exists

Kubernetes never updates `env` / `envFrom` values in a running pod — they are injected once, at pod creation. A SOPS secret rotation therefore applies cleanly, Flux reports success, and the pod keeps serving the old value with no signal that anything is stale.

An audit of the repo found 18 such references across 14 workload instances (8 distinct apps). Two mitigations exist, and they cover different ground:

| Mechanism | Covers | Why |
|---|---|---|
| `configMapGenerator` content hash | ConfigMaps | Kustomize hashes the content into the resource name, so the Deployment spec changes and Flux rolls the pod. Used by `gatus` and `ntfy`. |
| Reloader | Secrets **and** ConfigMaps | SOPS secrets are committed as encrypted `Secret` manifests and decrypted by Flux at apply time. `secretGenerator` needs plaintext input, so the hash trick cannot reach them. |

Reloader also covers a case the hash cannot: `pgadmin-servers` is mounted with `subPath: servers.json`, and **subPath mounts are never updated by kubelet** — not after the usual sync interval, not ever. Only pod recreation refreshes them.

## The Flux interaction

`reloader.reloadStrategy: annotations` is set deliberately. The default strategy injects a hash into an environment variable in the workload's pod spec — a field Flux manages, so Flux reconciles it straight back to what git says and the restart is undone.

This is not theoretical: a manual `kubectl rollout restart` of ntfy was reverted by Flux for exactly this reason, dropping the pod back to its original ReplicaSet. The `annotations` strategy writes to a pod-template annotation that Flux does not manage, so the two do not fight.

## Opting a workload in

Reloader only acts on workloads carrying the annotation:

```yaml
metadata:
  annotations:
    reloader.stakater.com/auto: "true"
```

It is applied via a targeted patch in each app's overlay kustomization.

| Environment | Annotated workloads |
|---|---|
| staging | `homepage`, `linkding`, `nightscout`, `ferretdb`, `pgadmin4` |
| production | `cloudflared`, `gatus`, `homepage`, `linkding`, `ferretdb`, `nightscout`, `ntfy`, `pgadmin4`, `speedtest` |

Not annotated, deliberately: `commafeed` and `mealie` reference no ConfigMap or Secret at all, and `gatus` on staging consumes only its hashed ConfigMap (production `gatus` additionally reads the `gatus-alerting` secret via `envFrom`, so it is annotated there).

## Validation on staging

Before promotion, a throwaway comment was appended to the staging `homepage` ConfigMap:

- Reloader rolled the pod within ~10s onto a new ReplicaSet
- Flux reverted the probe, and Reloader rolled again — correct behaviour
- The final ReplicaSet stayed stable for 3 minutes across several Flux reconciles, 0 restarts

The pod template carries Reloader's own annotation, which Flux leaves alone:

```
reloader.stakater.com/last-reloaded-from: {"type":"CONFIGMAP","name":"homepage",...}
```

This is the direct contrast with a manual `kubectl rollout restart`, which Flux reverts because the `restartedAt` annotation is not in git.

## Verifying

```bash
# controller healthy
flux get helmrelease reloader -n reloader --context=production

# which workloads are opted in
kubectl get deploy -A --context=production \
  -o jsonpath='{range .items[?(@.metadata.annotations.reloader\.stakater\.com/auto=="true")]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'

# confirm the strategy actually in use
kubectl logs -n reloader deploy/reloader --context=production | head
```

After editing a watched Secret, the consuming pod should roll within a few seconds and the new ReplicaSet should persist across the next Flux reconcile — if it does not, the strategy has regressed to the default.
