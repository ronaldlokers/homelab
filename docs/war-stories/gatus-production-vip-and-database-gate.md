# Deploying an In-Cluster Monitor to Production: The VIP It Couldn't Reach and the Database Gate

Gatus worked perfectly in staging, then failed on production in two ways staging
could never have surfaced. Both are structural properties of the production
cluster (MetalLB VIP + a label-gated database namespace) that the k3d staging
environment simply doesn't have.

## The Problem

Freshly promoted to production, Gatus came up `1/1 Running` but:

1. Its pod **restarted once** on first boot with a Postgres `connection refused`
   panic.
2. **Every `*.ronaldlokers.nl` check failed** — linkding, immich, mealie, ntfy,
   proxmox — while the one genuinely-external check (`lokilabs.nl`) stayed green.
3. Because ntfy was among the failing internal targets, **alerting was broken too**.

Staging (k3d) had none of these symptoms with the same base manifests.

## Trap 1 — The database namespace only admits labelled namespaces

Production Gatus uses Postgres storage (staging uses in-memory). The `database`
namespace runs default-deny with an ingress gate:

```yaml
# infrastructure/configs/base/network-policies/allow-apps-to-database.yaml
- from:
    - namespaceSelector:
        matchLabels:
          homelab.io/needs-database: "true"   # <-- only these namespaces reach 5432
```

The gatus namespace was labelled `homelab.io/app: "true"` but **not**
`homelab.io/needs-database`, so its Postgres connection was refused at the DB
side. Fix: add the label to the base namespace (harmless in staging, which never
connects), plus a `gatus → database:5432` egress policy in the production overlay.

Gatus compounds this by **panicking** if storage is unreachable at startup rather
than retrying, so the ordering of "netpol programmed" vs "app first dials DB"
produced a one-restart crash loop that self-resolved once both existed.

## Trap 2 — A pod cannot reach its own cluster's MetalLB VIP

The bigger one. Every failing check reported:

```
dial tcp 10.0.40.100:443: connect: connection refused
```

`10.0.40.100` is the MetalLB VIP that all `*.ronaldlokers.nl` names resolve to.
From inside the cluster, the Gatus pod could not open a connection to it.

What made this genuinely confusing — a controlled test contradicted the obvious
hypotheses:

- An ephemeral `curl` pod in the **same namespace, pinned to the same node** as
  Gatus reached the VIP fine (`http_code=200`). So it was **not** node placement,
  not `externalTrafficPolicy` (which was `Cluster` anyway), not the NetworkPolicy
  (same namespace = same policy), and not a Gatus bug.
- A pod restart did **not** fix it, ruling out the stale-dataplane-sync pattern
  seen elsewhere in these war stories.

The reproducible takeaway, regardless of the exact kube-proxy/MetalLB internal:
**sustained pod → own-VIP traffic is not reliable from inside this cluster, even
though one-shot curls may succeed.** Routing in-cluster health checks out through
the external load-balancer IP and back in is the wrong path anyway — it tests the
LB hairpin, not the service.

## The Fix — monitor in-cluster services from inside

Point the production checks at **internal ClusterIP DNS**, bypassing the VIP
entirely:

| Target | Was (via VIP) | Now (internal) |
|--------|---------------|----------------|
| linkding | `https://linkding.ronaldlokers.nl/health` | `http://linkding.linkding.svc.cluster.local:9090/health` |
| immich | `https://immich.ronaldlokers.nl/...` | `http://immich-server.immich.svc.cluster.local:2283/...` |
| mealie | `https://mealie.ronaldlokers.nl/...` | `http://mealie.mealie.svc.cluster.local:9000/...` |
| ntfy (check + alerts) | `https://ntfy.ronaldlokers.nl` | `http://ntfy.ntfy.svc.cluster.local` |
| proxmox | `https://proxmox.ronaldlokers.nl` (VIP → Traefik → backend) | `https://10.0.40.20:8006` (backend directly, self-signed → insecure) |
| lokilabs | unchanged — genuinely external | unchanged |

Each monitored app is default-deny, so each gained a small `allow-gatus-ingress`
NetworkPolicy on its service port; Gatus gained a matching egress policy for those
ports plus the Proxmox backend host.

Result: all six checks green, Postgres history persisting, and an authenticated
test publish to the `homelab-gatus` topic returned `200` — alerting confirmed
end-to-end.

**Tradeoff accepted:** internal checks confirm "the app is up" but no longer
exercise the external TLS/ingress path. That path is covered separately by
cert-manager (certificate issuance/expiry) and by the external `lokilabs.nl`
check, which does traverse a real public ingress.

## Lessons Learned

1. **Staging (k3d) cannot surface MetalLB or LB-hairpin behaviour** — a monitor
   that passes in staging can still fail every check in production because the
   load-balancer implementation is fundamentally different.
2. **Don't monitor in-cluster services through your own external VIP** — resolve
   them by internal ClusterIP DNS. The VIP round-trip tests the load balancer's
   hairpin, not the service, and may not work pod → own-VIP at all.
3. **A one-shot curl succeeding doesn't prove sustained connectivity works** — the
   decisive test here was same-node/same-namespace curl reaching the VIP while the
   long-lived pod couldn't; that contradiction is what ruled out the easy answers.
4. **Label-gated namespaces bite promotions, not first deploys** — production's
   `database` namespace admits only `homelab.io/needs-database` namespaces;
   Postgres storage silently fails without the label even though the app is
   otherwise correct.
5. **Apps that panic on missing storage make netpol ordering visible** — Gatus
   crash-restarts if Postgres isn't reachable at boot; a single self-resolving
   restart on first deploy is the NetworkPolicy/DB becoming ready a moment after
   the app first dialed.
6. **Verify an alert channel by actually sending through it** — an authenticated
   test publish (token from the real secret, via the exact URL the app uses)
   returning `200` is proof; a green "config validated" log is not.

## Related Documentation

- [A PostgreSQL Replica That Wouldn't Come Back](postgres-replica-networkpolicy-dataplane-sync.md) — the dataplane-sync pattern this was checked against and ruled out
- [NetworkPolicy Connectivity Debugging](networkpolicy-connectivity-debugging.md) — systematic zero-trust connectivity troubleshooting
- [Repository Architecture](/docs/architecture.md) — MetalLB VIP and the shared database namespace

## Commands Reference

**Read a monitor's per-endpoint error strings (the ground truth) via its API**:
```bash
kubectl --context=production port-forward -n gatus deploy/gatus 18080:8080 &
curl -sS http://localhost:18080/api/v1/endpoints/statuses | \
  python3 -c "import sys,json; [print(e['key'], e['results'][-1].get('errors')) for e in json.load(sys.stdin) if e.get('results')]"
```

**Isolate whether a failure is node/namespace-specific — curl the VIP pinned to the app's node**:
```bash
NODE=$(kubectl --context=production -n gatus get pod -l app=gatus -o jsonpath='{.items[0].spec.nodeName}')
kubectl --context=production -n gatus run nodetest --rm -i --restart=Never \
  --overrides="{\"spec\":{\"nodeName\":\"$NODE\"}}" --image=curlimages/curl:latest --command -- \
  curl -sS -o /dev/null -w "%{http_code}\n" https://linkding.ronaldlokers.nl/health
```

**Test an alert channel end-to-end without exposing the token (inject it from the secret)**:
```bash
kubectl --context=production -n gatus run alerttest --rm -i --restart=Never \
  --image=curlimages/curl:latest \
  --overrides='{"spec":{"containers":[{"name":"alerttest","image":"curlimages/curl:latest","command":["sh","-c","curl -sS -o /dev/null -w \"%{http_code}\\n\" -H \"Authorization: Bearer $NTFY_TOKEN\" -d test http://ntfy.ntfy.svc.cluster.local/homelab-gatus"],"envFrom":[{"secretRef":{"name":"gatus-alerting"}}]}]}}'
```
