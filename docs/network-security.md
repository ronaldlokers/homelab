# Network Security Architecture

## Overview

This document describes the network security implementation in the homelab Kubernetes clusters using **NetworkPolicies** to enforce zero-trust networking principles.

## What are NetworkPolicies?

NetworkPolicies are Kubernetes resources that control traffic flow between pods and network endpoints. They work like firewalls inside your Kubernetes cluster, allowing you to:

- **Deny all traffic by default** (default-deny posture)
- **Explicitly allow specific traffic** (allowlist approach)
- **Segment namespaces** (prevent lateral movement)
- **Control egress** (limit which external services pods can reach)

## Security Philosophy: Zero-Trust Networking

Our implementation follows **zero-trust principles**:

1. **Default deny**: All traffic is blocked unless explicitly allowed
2. **Least privilege**: Pods can only communicate with services they need
3. **Defense in depth**: Network policies complement other security measures (RBAC, SOPS encryption, etc.)
4. **Namespace isolation**: Applications in different namespaces can't communicate unless permitted

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         Internet                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                    ┌────▼─────┐
                    │ Traefik  │ (kube-system)
                    │ Ingress  │
                    └────┬─────┘
                         │
        ┌────────────────┼────────────────────────────┐
        │                │                             │
   ┌────▼─────┐    ┌────▼──────┐              ┌──────▼──────┐
   │ Homepage │    │  Linkding │              │   Grafana   │
   │          │    │           │              │ (monitoring)│
   └────┬─────┘    └────┬──────┘              └──────┬──────┘
        │               │                             │
        │               │        ┌────────────────────┼──────┐
        │               │        │                    │      │
        │          ┌────▼────────▼─────┐         ┌───▼───┐  │
        │          │   PostgreSQL      │◄────────┤Prometh│  │
        │          │   (database ns)   │         │ eus   │  │
        │          └───────────────────┘         └───────┘  │
        │                                                    │
        └────────────────────────────────────────────────────┘
                         ▲
                         │
                  ┌──────┴──────┐
                  │   CoreDNS   │
                  │ (kube-system)│
                  └─────────────┘
```

## Where Policies Live

NetworkPolicies are split across two layers, matching the repo's base/overlay convention:

- **`apps/base/<app>/network-policies.yaml`** — everything owned by a single application: its default-deny pair, DNS egress, Traefik ingress, homepage ingress, Prometheus scrape ingress, database egress, internet egress, and any app-internal rules (e.g. Immich's Valkey/ML sidecars). This file is a `kustomization.yaml` resource alongside the app's `namespace.yaml`, `deployment.yaml`, etc., so the policy's lifecycle is tied to the app: it only renders in an environment if the app itself is deployed there (e.g. ntfy and speedtest are production-only, so their policies never render on staging).
- **`infrastructure/configs/base/network-policies/`** — cross-cutting policies that don't belong to one app: the `database` and `monitoring` namespaces' own default-deny/DNS/ingress/egress rules, the database's `allow-apps-ingress` rule, and Prometheus's bulk scrape-egress rule.

Two shared labels on each app's `Namespace` (set in `apps/base/<app>/namespace.yaml`) let the cross-cutting policies reference "all apps" without enumerating names:

- `homelab.io/app: "true"` — every app namespace. Used by homepage's egress-to-apps rule and Prometheus's scrape-egress rule.
- `homelab.io/needs-database: "true"` — only the apps that talk to PostgreSQL (all apps except homepage and ntfy). Used by the database namespace's `allow-apps-ingress` rule.

## Implemented NetworkPolicies

### 1. Default Deny

**Purpose**: Establish zero-trust baseline by denying all ingress and egress traffic in every namespace.

**Applied to**: every app namespace (own copy in `apps/base/<app>/network-policies.yaml`) plus `database` and `monitoring` (in `infrastructure/configs/base/network-policies/default-deny-all.yaml`).

**Note**: `external-services` namespace contains only Service resources (no pods), so NetworkPolicies are not needed there.

**Impact**: Without this, all pods can communicate freely. With this, all traffic must be explicitly allowed.

### 2. Allow DNS

**Purpose**: Enable DNS resolution for all pods (required for service discovery).

**Traffic allowed**:
- All pods → CoreDNS (kube-system) on port 53 (UDP/TCP)

**Location**: each app's own `allow-dns` rule lives in `apps/base/<app>/network-policies.yaml`; `database` and `monitoring`'s copies stay in `infrastructure/configs/base/network-policies/allow-dns.yaml`.

**Why needed**: Without DNS, pods can't resolve service names like `postgresql-cluster.database.svc.cluster.local`.

### 3. Allow Ingress to Apps

**Purpose**: Enable external access to applications via Traefik ingress controller.

**Traffic allowed**:
- Traefik (kube-system) → each app's pods (rule lives in that app's `apps/base/<app>/network-policies.yaml`)
- Traefik (kube-system) → monitoring (Grafana only; stays in `infrastructure/configs/base/network-policies/allow-ingress-to-apps.yaml`)

**Why needed**: User requests come through Traefik, which must reach app pods to serve content.

### 4. Allow Apps to Database

**Purpose**: Enable database connectivity for applications.

**Traffic allowed** (each app's `allow-database-egress` rule lives in its own `apps/base/<app>/network-policies.yaml`):
- linkding → database:5432 (PostgreSQL)
- nightscout → database:5432, database:27017 (PostgreSQL + FerretDB)
- pgadmin → database:5432 (PostgreSQL)
- commafeed → database:5432 (PostgreSQL)
- speedtest → database:5432 (PostgreSQL)
- immich → database:5432 (PostgreSQL)

**Ingress to database namespace** (`infrastructure/configs/base/network-policies/allow-apps-to-database.yaml`):
- From any namespace labeled `homelab.io/needs-database: "true"` — currently the six apps above. homepage and ntfy don't carry this label and have no path to the database.

**Why needed**: Applications persist data in PostgreSQL databases.

### 5. Allow Monitoring

**Purpose**: Enable Prometheus to scrape metrics from all services.

**Traffic allowed**:
- Prometheus (monitoring) → any namespace labeled `homelab.io/app: "true"`, plus the `database` namespace explicitly (`infrastructure/configs/base/network-policies/allow-prometheus-to-app-namespaces.yaml`)
- Each app also carries a matching `allow-prometheus-scraping` ingress rule in its own `apps/base/<app>/network-policies.yaml` (NetworkPolicy ingress/egress must be permitted on both sides)
- Promtail/Fluentd → Loki (log ingestion)
- Internal monitoring stack communication (Grafana ↔ Prometheus ↔ Alertmanager ↔ Loki)

(The last three stay in `infrastructure/configs/base/network-policies/allow-monitoring.yaml`, along with the database namespace's own `allow-prometheus-scraping` ingress rule.)

**Why needed**: Without this, Prometheus can't collect metrics and monitoring is blind.

### 6. Allow Egress to Internet

**Purpose**: Enable applications to reach external services (APIs, webhooks, RSS feeds, etc.).

**Traffic allowed** (each app's `allow-internet-egress` rule lives in its own `apps/base/<app>/network-policies.yaml`; pgadmin has none since it needs no internet access):
- All application pods → Internet (0.0.0.0/0) on ports 80, 443
- **Excludes** private networks: 172.16.0.0/12, 192.168.0.0/16 (10.0.0.0/8 varies per namespace)
- **Homepage** → Internet including 10.0.0.0/8 on ports 80, 443, 8006 (for Proxmox at 10.0.1.10)
- Database namespace → Internet on port 443 (Backblaze B2 backups; stays in `infrastructure/configs/base/network-policies/allow-egress-internet.yaml`)
- Monitoring namespace → Internet on ports 80, 443 (webhooks, external queries; same base file)

**Why needed**:
- Immich: Download ML models, fetch metadata
- ntfy: Send push notifications
- Speedtest: Reach test servers
- Nightscout: Fetch CGM data
- Commafeed: Fetch RSS feeds
- Linkding: Fetch bookmark metadata
- Homepage: Check external service status
- Alertmanager: Send webhook notifications
- PostgreSQL: Backup to Backblaze B2

**Security note**: Private networks are excluded to prevent pods from reaching internal infrastructure directly.

### 7. Allow Homepage to Apps

**Purpose**: Enable Homepage dashboard to connect to internal services for status widgets.

**Location**: the egress side (`allow-homepage-to-apps-egress`) lives in `apps/base/homepage/network-policies.yaml`; the ingress side (`allow-homepage-ingress`) lives in each destination app's own `apps/base/<app>/network-policies.yaml`.

**Traffic allowed**:
- Homepage → any namespace labeled `homelab.io/app: "true"` (ports 80, 443, 2283, 3000, 3003, 8006, 8080) plus kube-system explicitly (Traefik)
- Per-app ingress rules further restrict which ports each app actually accepts from homepage:
  - immich (ports 2283, 3003)
  - speedtest (port 80)
  - linkding (port 80)
  - nightscout (ports 80, 1337)
  - commafeed (port 80)
  - ntfy (port 80)
  - pgadmin (port 80)
  - monitoring/Grafana (ports 80, 3000; stays in `infrastructure/configs/base/network-policies/allow-monitoring.yaml`)

**Why needed**: Homepage widgets query service APIs to show status, metrics, and health information on the dashboard.

**Security note**: Homepage has broader access than typical apps because it's a monitoring dashboard that needs to query many services.

**Special case - Proxmox**: Homepage's Proxmox widget connects via the `allow-internet-egress` policy (port 8006 to 10.0.1.10), not through namespace selectors, because Proxmox is an external service outside the cluster.

### 8. Allow Database to Kubernetes API (`allow-database-to-k8s-api.yaml`)

**Purpose**: Enable CloudNative-PG operator to manage PostgreSQL clusters.

**Traffic allowed**:
- Database pods → Kubernetes API server (10.43.0.1:443)
- Database pods → Control plane nodes (10.0.40.101-103:6443)

**Why needed**: CloudNative-PG operator requires API access for:
- Cluster coordination and leader election
- Health checks and status updates
- Backup and recovery operations
- Pod lifecycle management

**Security note**: Limited to database namespace only. Required for operator functionality.

### 9. Allow Immich Internal Communication

**Purpose**: Enable Immich microservices to communicate within the same namespace.

**Location**: `apps/base/immich/network-policies.yaml`, alongside Immich's other app-owned rules.

**Traffic allowed**:
- Immich server → Valkey (Redis) on port 6379 (within immich namespace)
- Immich server → Machine learning service on port 3003 (within immich namespace)
- Immich server → Database namespace on port 5432

**Ingress to Immich pods**:
- Valkey accepts connections from Immich server

**Why needed**: Immich is a microservices architecture:
- **Valkey**: Job queue and session storage
- **Machine learning**: AI-powered photo recognition
- **PostgreSQL**: Metadata and user data storage

**Security note**: Pod selectors ensure only specific components can communicate (e.g., server can access valkey, but valkey cannot initiate connections to server).

### 10. Allow Loki to S3 Storage (`allow-loki-to-s3.yaml`)

**Purpose**: Enable Loki write components to store logs in S3-compatible object storage.

**Traffic allowed**:
- Loki write pods → MinIO/S3 storage (10.0.40.10:9000)

**Ingress within monitoring namespace**:
- Loki pods ↔ Loki pods (ring membership and replication)

**Why needed**: Loki Simple Scalable Deployment mode requires:
- **S3 storage**: Persistent log storage beyond cluster lifetime
- **Ring communication**: Distributed hash ring for pod coordination
- **Replication**: Multiple write replicas share workload

**Security note**: Only Loki write components need S3 access. Read components fetch from S3 through write components.

## Traffic Flows

### Application Request Flow

1. **User → Application**:
   ```
   User → DNS (10.0.40.100) → Traefik (kube-system) → App pod → Response
   ```

2. **Application → Database**:
   ```
   App pod → CoreDNS (resolve postgresql-cluster.database) → PostgreSQL pod
   ```

3. **Application → External API**:
   ```
   App pod → CoreDNS (resolve external domain) → Internet (via node egress)
   ```

4. **Prometheus Scraping**:
   ```
   Prometheus → App pod metrics endpoint (e.g., :8080/metrics) → Metrics data
   ```

### What Traffic is Blocked?

- ❌ App pod → Another app pod in different namespace (lateral movement)
- ❌ App pod → kube-system (except Traefik ingress response and CoreDNS)
- ❌ App pod → Internal infrastructure without explicit allow (private networks 10.x, 172.16.x, 192.168.x)
- ❌ Database pod → App pod (only app → database is allowed, not bidirectional)
- ❌ Database pod → Internet (except HTTPS port 443 for Backblaze B2 backups and Kubernetes API)
- ❌ Homepage → Services not in allowed namespaces
- ❌ Immich components → Other namespaces (except database)

## Adding a New Application

Everything a new app needs lives in one file next to it — you generally do **not** touch `infrastructure/configs/base/network-policies/` at all. This keeps the policy's lifecycle tied to the app: it only renders in environments where the app itself is deployed (see `apps/staging/kustomization.yaml` vs `apps/production/kustomization.yaml`).

### 1. Label the namespace

In `apps/base/your-new-app/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: your-new-app
  labels:
    homelab.io/app: "true"
    # Only add this if the app talks to PostgreSQL:
    homelab.io/needs-database: "true"
```

`homelab.io/app` makes the namespace a valid destination for homepage's status checks and a valid Prometheus scrape target automatically — no other file needs editing for those two things. `homelab.io/needs-database` opens the database namespace's ingress rule to it.

### 2. Create `apps/base/your-new-app/network-policies.yaml`

Combine the pieces the app actually needs:

```yaml
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Ingress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
---
# Only if the app is exposed via Ingress:
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-from-traefik
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: traefik
---
# Only if the app should show up on the homepage dashboard:
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-homepage-ingress
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: homepage
      ports:
        - protocol: TCP
          port: 80  # match your app's actual port
---
# Only if the app exposes Prometheus metrics:
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-prometheus-scraping
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
          podSelector:
            matchLabels:
              app.kubernetes.io/name: prometheus
---
# Only if the app uses PostgreSQL (pair with homelab.io/needs-database label above):
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-database-egress
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: database
      ports:
        - protocol: TCP
          port: 5432
---
# Only if the app calls out to the internet:
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-internet-egress
  namespace: your-new-app
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 80
        - protocol: TCP
          port: 443
```

### 3. Register the file

Add `network-policies.yaml` to `apps/base/your-new-app/kustomization.yaml`'s `resources` list, next to `namespace.yaml`, `deployment.yaml`, etc.

That's it — no edits to `infrastructure/configs/base/network-policies/` or `infrastructure/configs/production/` are needed unless the new namespace is `database`- or `monitoring`-adjacent infrastructure rather than a user-facing app.

## Troubleshooting

### Pod Can't Resolve DNS

**Symptom**: Pod logs show DNS resolution failures like `cannot resolve postgresql-cluster.database.svc.cluster.local`.

**Fix**: Check allow-dns NetworkPolicy exists for the namespace:

```bash
kubectl get networkpolicy -n <namespace>
kubectl describe networkpolicy allow-dns -n <namespace>
```

Verify it allows egress to kube-system on port 53.

### App Returns 503 or Connection Refused

**Symptom**: Accessing application via ingress returns 503 or connection refused.

**Possible causes**:
1. **Missing ingress allow rule**: Traefik can't reach app pods
2. **App not listening**: Pod isn't actually serving on expected port
3. **Wrong pod selector**: NetworkPolicy targets wrong pods

**Debug**:

```bash
# Check if Traefik can reach app
kubectl get networkpolicy -n <namespace>
kubectl describe networkpolicy allow-ingress-from-traefik -n <namespace>

# Verify Traefik labels (should match policy)
kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik --show-labels

# Test from Traefik pod directly
kubectl exec -n kube-system <traefik-pod> -- wget -O- http://<app-service>.<namespace>:80
```

### App Can't Connect to Database

**Symptom**: App logs show `connection refused` or `timeout` connecting to PostgreSQL.

**Debug**:

```bash
# Check egress from app namespace
kubectl get networkpolicy allow-database-egress -n <app-namespace>

# Check ingress to database namespace
kubectl get networkpolicy allow-apps-ingress -n database

# Test connectivity from app pod
kubectl exec -n <app-namespace> <pod-name> -- nc -zv postgresql-cluster.database.svc.cluster.local 5432
```

### Prometheus Missing Metrics

**Symptom**: Grafana shows "No data" or Prometheus targets show as down.

**Debug**:

```bash
# Check monitoring NetworkPolicy
kubectl get networkpolicy allow-prometheus-scraping -n <namespace>

# Verify Prometheus pod labels
kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus --show-labels

# Test from Prometheus pod
kubectl exec -n monitoring <prometheus-pod> -- wget -O- http://<service>.<namespace>:8080/metrics
```

### App Can't Reach External API

**Symptom**: App logs show connection timeout to external services (e.g., api.github.com).

**Debug**:

```bash
# Check egress policy
kubectl get networkpolicy allow-internet-egress -n <namespace>
kubectl describe networkpolicy allow-internet-egress -n <namespace>

# Test from app pod
kubectl exec -n <namespace> <pod-name> -- curl -v https://api.github.com
```

If it fails, verify:
1. The namespace has allow-internet-egress NetworkPolicy
2. Port 443 is allowed
3. The target IP isn't in excluded private ranges

## Testing NetworkPolicies

### Verify Policies Are Applied

```bash
# List all NetworkPolicies across namespaces
kubectl get networkpolicies --all-namespaces

# Check specific namespace
kubectl get networkpolicies -n linkding
```

### Test Connectivity

Use `kubectl exec` to test from within pods:

```bash
# Test DNS (should work)
kubectl exec -n linkding <pod-name> -- nslookup kubernetes.default

# Test database (should work for apps that need it)
kubectl exec -n linkding <pod-name> -- nc -zv postgresql-cluster.database 5432

# Test lateral movement (should FAIL)
kubectl exec -n linkding <pod-name> -- nc -zv some-service.homepage 80

# Test internet (should work)
kubectl exec -n linkding <pod-name> -- curl -I https://google.com
```

### Validate with Policy Dry-Run

Before applying changes, validate with dry-run:

```bash
kubectl apply -f infrastructure/configs/base/network-policies/ --dry-run=client
```

## Maintenance

### Updating Policies

1. Edit policy files in `infrastructure/configs/base/network-policies/`
2. Test changes in staging first
3. Commit and push to trigger Flux reconciliation
4. Verify policies applied: `flux reconcile kustomization infrastructure-configs`
5. Test application connectivity

### Monitoring Policy Effectiveness

There's no built-in way to see "blocked" traffic in Kubernetes NetworkPolicies. To monitor effectiveness:

1. **Centralized logging**: Review application error logs for connection failures
2. **Metrics**: Monitor connection errors in Prometheus
3. **Testing**: Periodically test that prohibited connections are blocked

### Performance Impact

NetworkPolicies are enforced by the CNI plugin (in K3s, this is Flannel + iptables). Performance impact is minimal:

- **Latency**: Negligible (<1ms per connection)
- **Throughput**: No impact on established connections
- **CPU**: Minimal overhead from iptables rule evaluation

## Security Benefits

### Attack Surface Reduction

- **Lateral movement prevention**: Compromised pod can't pivot to other services
- **Data exfiltration limitation**: Egress controls prevent unauthorized data leaks
- **Namespace isolation**: Blast radius is contained per namespace

### Compliance

NetworkPolicies help meet security requirements:

- **CIS Kubernetes Benchmark**: Section 5.3 (Network Policies)
- **PCI-DSS**: Network segmentation requirements
- **NIST**: Defense-in-depth principle

### Real-World Scenarios

**Scenario 1**: Immich pod is compromised via CVE

- ❌ **Without NetworkPolicies**: Attacker can access database directly, scrape secrets from other namespaces, pivot to monitoring infrastructure
- ✅ **With NetworkPolicies**: Attacker is limited to Immich's allowed connections (database, internet egress). Can't reach other apps or infrastructure.

**Scenario 2**: Malicious container image in linkding

- ❌ **Without NetworkPolicies**: Malware can scan internal network, attempt to compromise other services
- ✅ **With NetworkPolicies**: Malware can only reach DNS, database, and internet (but not private networks).

## Further Reading

- [Kubernetes NetworkPolicy Documentation](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [NetworkPolicy Recipes](https://github.com/ahmetb/kubernetes-network-policy-recipes)
- [Calico NetworkPolicy Tutorial](https://docs.tigera.io/calico/latest/network-policy/get-started/kubernetes-policy/kubernetes-network-policy)
- [CIS Kubernetes Benchmark - Network Policies](https://www.cisecurity.org/benchmark/kubernetes)

## Summary

We've implemented a **zero-trust network architecture** using Kubernetes NetworkPolicies:

- ✅ **Default deny** in all namespaces
- ✅ **Explicit allow** for required traffic flows
- ✅ **Namespace isolation** to prevent lateral movement
- ✅ **Egress control** to limit external communication
- ✅ **Monitoring enabled** via Prometheus scraping
- ✅ **Production-ready**, with app-owned policies deployed only where each app actually runs (e.g. ntfy and speedtest are production-only and never render on staging)

This significantly hardens the cluster against network-based attacks while maintaining full functionality of all applications.

---

**Last Updated**: 2026-07-02
**Related Documents**: `docs/security.md`, `docs/architecture.md`
