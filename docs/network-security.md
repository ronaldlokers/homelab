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

## Implemented NetworkPolicies

### 1. Default Deny (`default-deny-all.yaml`)

**Purpose**: Establish zero-trust baseline by denying all ingress and egress traffic in every namespace.

**Applied to**:
- linkding
- homepage
- nightscout
- pgadmin
- commafeed
- speedtest
- immich
- ntfy
- database
- monitoring

**Impact**: Without this, all pods can communicate freely. With this, all traffic must be explicitly allowed.

### 2. Allow DNS (`allow-dns.yaml`)

**Purpose**: Enable DNS resolution for all pods (required for service discovery).

**Traffic allowed**:
- All pods → CoreDNS (kube-system) on port 53 (UDP/TCP)

**Why needed**: Without DNS, pods can't resolve service names like `postgresql-cluster.database.svc.cluster.local`.

### 3. Allow Ingress to Apps (`allow-ingress-to-apps.yaml`)

**Purpose**: Enable external access to applications via Traefik ingress controller.

**Traffic allowed**:
- Traefik (kube-system) → Application pods in:
  - linkding
  - homepage
  - nightscout
  - pgadmin
  - commafeed
  - speedtest
  - immich
  - ntfy
  - monitoring (Grafana only)

**Why needed**: User requests come through Traefik, which must reach app pods to serve content.

### 4. Allow Apps to Database (`allow-apps-to-database.yaml`)

**Purpose**: Enable database connectivity for applications.

**Traffic allowed**:
- linkding → database:5432 (PostgreSQL)
- nightscout → database:5432, database:27017 (PostgreSQL + FerretDB)
- pgadmin → database:5432 (PostgreSQL)
- commafeed → database:5432 (PostgreSQL)
- speedtest → database:5432 (PostgreSQL)
- immich → database:5432 (PostgreSQL)

**Ingress to database namespace**:
- From all app namespaces listed above

**Why needed**: Applications persist data in PostgreSQL databases.

### 5. Allow Monitoring (`allow-monitoring.yaml`)

**Purpose**: Enable Prometheus to scrape metrics from all services.

**Traffic allowed**:
- Prometheus (monitoring) → All application pods (metrics endpoints)
- Prometheus (monitoring) → Database pods (PostgreSQL metrics)
- Promtail/Fluentd → Loki (log ingestion)
- Internal monitoring stack communication (Grafana ↔ Prometheus ↔ Alertmanager ↔ Loki)

**Why needed**: Without this, Prometheus can't collect metrics and monitoring is blind.

### 6. Allow Egress to Internet (`allow-egress-internet.yaml`)

**Purpose**: Enable applications to reach external services (APIs, webhooks, RSS feeds, etc.).

**Traffic allowed**:
- All application pods → Internet (0.0.0.0/0) on ports 80, 443
- **Excludes** private networks: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
- Database namespace → Internet on port 443 (Backblaze B2 backups)
- Monitoring namespace → Internet on ports 80, 443 (webhooks, external queries)

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
- ❌ App pod → kube-system (except Traefik ingress response)
- ❌ App pod → Internal infrastructure (private networks 10.x, 172.16.x, 192.168.x)
- ❌ Database pod → App pod (only app → database is allowed)
- ❌ Database pod → Internet (except HTTPS for backups)

## Adding a New Application

When deploying a new application, you need to update NetworkPolicies:

### 1. Add namespace to default-deny

Edit `infrastructure/configs/base/network-policies/default-deny-all.yaml`:

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
```

### 2. Add DNS allow rule

Edit `infrastructure/configs/base/network-policies/allow-dns.yaml`:

```yaml
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
```

### 3. Allow ingress from Traefik (if app has ingress)

Edit `infrastructure/configs/base/network-policies/allow-ingress-to-apps.yaml`:

```yaml
---
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
```

### 4. Allow database access (if app uses PostgreSQL)

Edit `infrastructure/configs/base/network-policies/allow-apps-to-database.yaml`:

```yaml
---
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
```

And update the database namespace ingress rule to include your new app.

### 5. Allow Prometheus scraping (if app has metrics)

Edit `infrastructure/configs/base/network-policies/allow-monitoring.yaml`:

```yaml
---
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
```

### 6. Allow internet egress (if app needs external APIs)

Edit `infrastructure/configs/base/network-policies/allow-egress-internet.yaml`:

```yaml
---
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
- ✅ **Production-ready** applied to both staging and production

This significantly hardens the cluster against network-based attacks while maintaining full functionality of all applications.

---

**Last Updated**: 2026-02-07
**Related Documents**: `docs/security.md`, `docs/architecture.md`
