# k3s Node Certificates Approaching Expiry

## Quick Reference

- **Severity**: Medium — the warning fires 120 days out, so there is time
- **Estimated Time to Resolve**: 15 minutes for all three nodes
- **Symptoms**: `CertificateExpirationWarning` events against one or more nodes
- **Affected Components**: k3s control plane (apiserver, etcd, controller-manager, scheduler, admin, supervisor, auth-proxy)
- **Environment**: Production
- **Prerequisites**: SSH to the node, sudo, kubectl against a node you are **not** restarting

## Symptoms & Detection

### Error Messages

```
Warning  CertificateExpirationWarning  node/kube-srv-2
Node certificates require attention - restart k3s on this node to trigger
automatic rotation: admin/client-admin.crt: certificate CN=system:admin,
O=system:masters will expire within 120 days at 2026-12-02T17:11:23Z, ...
```

### Observable Behaviour

- Events only. Nothing is broken, and nothing will be until the expiry date.
- Typically 13 certificates per node, all sharing one expiry timestamp — the
  date the node was first bootstrapped, plus one year.
- Nodes may disagree. In 2026-08 kube-srv-1 was clean while kube-srv-2 and
  kube-srv-3 were stale, because only kube-srv-1 had been through a rotation.

### Monitoring Indicators

**There is no alert for this.** The events exist; nothing routes them. It was
found by accident, when the Campfire status bot's `why` verb read the warning
event stream while triaging something unrelated. Worth adding a PrometheusRule.

```bash
kubectl get events -A --field-selector reason=CertificateExpirationWarning
```

## Diagnosis Steps

### 1. Ask k3s directly, per node

```bash
ssh <node> 'sudo k3s certificate check'
```

`is ok, expires at …` is fine. `will expire within 120 days at …` is the
finding. Count them:

```bash
ssh <node> 'sudo k3s certificate check 2>&1 | grep -c "will expire within"'
```

### 2. Work out how far off expiry is

```bash
python3 -c "
from datetime import datetime, timezone
exp = datetime(2026,12,2,tzinfo=timezone.utc)
print((exp - datetime.now(timezone.utc)).days, 'days')"
```

**This number decides the procedure.** See the trap below.

### 3. Confirm diagnosis

**This is the right runbook if:**
- ✅ `CertificateExpirationWarning` events name specific nodes
- ✅ `k3s certificate check` reports `will expire within` on those nodes
- ✅ The cluster is otherwise healthy

**This is NOT the right runbook if:**
- ❌ The certificate is served by cert-manager (an Ingress, `*.ronaldlokers.nl`) —
  that is a Certificate resource, nothing to do with k3s
- ❌ Certificates have **already** expired — the API server will be refusing
  connections and this becomes a recovery, not a rotation

## The trap: a plain restart may do nothing

The event text says *"restart k3s on this node to trigger automatic rotation"*.
That is only true **within 90 days of expiry**.

Observed 2026-08-11: kube-srv-2 had been rebooted ten days earlier and its
certificates were still stale, because expiry was 113 days out. Following the
event's own advice would have disrupted the control plane and changed nothing.

| Days to expiry | What a restart does |
|---|---|
| Under 90 | Rotates automatically |
| Over 90 | Nothing — you need `k3s certificate rotate` |

`k3s certificate rotate` forces it regardless of the window. It writes new
certificates to disk and requires k3s to be **stopped** first; they are picked
up on the next start.

## Resolution Steps

**One node at a time.** Three control plane nodes with embedded etcd survive
losing one. They do not survive losing two.

### Step 1: Pre-flight

**Why**: you need to know the cluster was healthy *before*, or you cannot tell
what you caused.

```bash
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'
kubectl get nodes
kubectl get pods -A | grep -Ev 'Running|Completed'
flux get kustomizations
```

**The kubeconfig server matters.** If it points at the node you are about to
stop, you lose kubectl for the duration. Point it elsewhere first.

### Step 2: Rotate

**Why**: stop, rewrite certificates, start. All three parts are required.

```bash
ssh <node> 'sudo systemctl stop k3s && sudo k3s certificate rotate && sudo systemctl start k3s'
```

**Paste this as a single line.** If it splits across newlines, `systemctl` runs
with no arguments and later words are read as separate commands. That happened
on 2026-08-11: `stop` and `rotate` ran, `start` did not, and the node sat
NotReady with freshly rotated certificates it had not loaded.

**If this fails**: check whether k3s is stopped —

```bash
ssh <node> 'systemctl is-active k3s'
```

`inactive` means the node is down and needs starting. Nothing is lost; the
rotation, if it ran, is already on disk:

```bash
ssh <node> 'sudo systemctl start k3s'
```

### Step 3: Verify before the next node

**Why**: a second node taken down while the first is still rejoining costs
quorum, and that is a different, much worse runbook.

```bash
kubectl get nodes                       # all three Ready
ssh <node> 'sudo k3s certificate check 2>&1 | grep -c "will expire within"'   # 0
ssh <node> 'sudo k3s certificate check 2>&1 | grep -oE "expires at [0-9-]{10}" | sort -u'
```

Expect the new expiry to be one year out.

### Step 4: Repeat for the remaining nodes

Then confirm the cluster as a whole:

```bash
flux get kustomizations
kubectl get volumes.longhorn.io -n longhorn-system -o json |
  python3 -c "import sys,json,collections; print(collections.Counter(v['status'].get('robustness') for v in json.load(sys.stdin)['items']))"
```

## Expected noise, not damage

**Flux goes red for a minute or two.** The API server on the restarted node
disappears and comes back, and Longhorn's admission webhook times out while its
side of that settles:

```
Node/longhorn-system/kube-srv-1 dry-run failed (InternalError): failed calling
webhook "mutator.longhorn.io": context deadline exceeded
```

`infrastructure-controllers` goes `False`, and everything that depends on it
follows. It self-heals; `flux reconcile kustomization infrastructure-controllers`
hurries it along. Volumes stay `healthy` throughout — check that rather than the
Kustomization status if you want to know whether anything is genuinely wrong.

**Warning events linger.** Kubernetes events survive roughly an hour, so
`CertificateExpirationWarning` entries older than the rotation remain visible
afterwards. Compare each event's timestamp against the k3s restart time before
concluding the rotation failed:

```bash
ssh <node> 'systemctl show k3s -p ActiveEnterTimestamp --value'
```

## Prevention

Rotation buys one year. The warning fires at 120 days, which is ample, but only
if someone sees it — and today nothing routes these events anywhere.

Worth doing: a PrometheusRule on the event, or a `certs`-style assertion in the
Campfire status bot. The bot already reads cert-manager Certificates; k3s node
certificates are a different source and are not covered by it.
