# War Story: NetworkPolicy Connectivity Debugging

**Date**: February 26, 2026
**Status**: Resolved
**Impact**: Multiple services unable to communicate after implementing NetworkPolicies
**Duration**: ~8 hours
**Affected Services**: Homepage, Immich, Loki, PostgreSQL, Proxmox widget

---

## Context

After implementing zero-trust NetworkPolicies with default-deny across all namespaces, several services experienced connectivity issues. This war story documents the systematic debugging process and lessons learned.

## Initial Symptoms

1. **Nightscout couldn't connect to database** - Connection refused to PostgreSQL
2. **Homepage widgets showing errors** - Multiple API connection failures
3. **PostgreSQL cluster crashing** - "Not enough disk space" errors (misleading)
4. **Immich server flapping** - Couldn't connect to Valkey (Redis)
5. **Loki write pods showing "empty ring"** - Replication failure
6. **Proxmox widget failing** - HTTP 500 errors on Homepage

## Investigation & Resolution

### Issue 1: Nightscout Database Connectivity

**Symptom**: Nightscout logs showed "connection refused" to PostgreSQL.

**Initial hypothesis**: Missing egress policy from nightscout namespace.

**Investigation**:
```bash
kubectl get networkpolicy -n nightscout
kubectl describe networkpolicy allow-database-egress -n nightscout
kubectl exec -n nightscout <pod> -- nc -zv postgresql-cluster.database 5432
```

**Root cause**: NetworkPolicy ingress rule in database namespace had **incorrect structure**. Multiple separate `from` clauses instead of a combined list:

```yaml
# WRONG - creates separate rules that don't work together
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: linkding
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: nightscout
```

```yaml
# CORRECT - combined list in one rule
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: linkding
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: nightscout
    ports:
      - protocol: TCP
        port: 5432
```

**Fix**: Restructured ingress rules to use a single rule with multiple namespace selectors.

**Lesson**: NetworkPolicy YAML structure matters significantly. Multiple `from` clauses create separate rules with AND logic, not OR.

---

### Issue 2: Nightscout to FerretDB

**Symptom**: After fixing database connectivity, Nightscout still failed with MongoDB connection errors.

**Investigation**: Nightscout uses FerretDB (MongoDB compatibility layer) which runs in the same namespace.

**Root cause**: No network policy allowing Nightscout pods → FerretDB pods within the same namespace.

**Fix**: Added namespace-internal policy:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-nightscout-to-ferretdb
  namespace: nightscout
spec:
  podSelector:
    matchLabels:
      app: nightscout
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: ferretdb
      ports:
        - protocol: TCP
          port: 27017
```

**Lesson**: Default-deny blocks **all traffic**, including within the same namespace. Microservices architectures need explicit intra-namespace policies.

---

### Issue 3: Homepage Widget Failures

**Symptom**: Homepage couldn't reach services for status checks. Connection refused to Traefik and various apps.

**Investigation**:
```bash
kubectl logs -n homepage <pod> | grep error
kubectl exec -n homepage <pod> -- wget http://traefik.kube-system
```

**Root cause**: Homepage egress policy didn't include:
1. `kube-system` namespace (for Traefik)
2. Correct ports for various services (e.g., Immich on 2283, not 80)

**Fix**: Added comprehensive egress policy for homepage:
```yaml
egress:
  - to:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: immich
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: kube-system
      # ... other namespaces
    ports:
      - protocol: TCP
        port: 80
      - protocol: TCP
        port: 2283  # Immich
      - protocol: TCP
        port: 3003  # Immich ML
      - protocol: TCP
        port: 8006  # Proxmox
```

**Note**: Required pod restart after applying policy: `kubectl rollout restart -n homepage deployment/homepage`

**Lesson**: NetworkPolicies don't apply retroactively to existing connections. Restart pods after policy changes.

---

### Issue 4: PostgreSQL Cluster Crashes

**Symptom**: CloudNative-PG pods in CrashLoopBackOff with misleading "Detected low-disk space condition" errors.

**Investigation**:
```bash
kubectl logs -n database postgresql-cluster-1
# Error: connection refused to 10.43.0.1:443 (Kubernetes API)
```

**Root cause 1**: PostgreSQL operator couldn't reach Kubernetes API server for cluster coordination.

**Root cause 2**: Actual disk usage was 12-15GB but PVCs were only 10Gi.

**Fix**:
1. Added network policy for database → Kubernetes API:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-k8s-api-egress
  namespace: database
spec:
  egress:
    - to:
        - ipBlock:
            cidr: 10.43.0.1/32  # Kubernetes service IP
      ports:
        - protocol: TCP
          port: 443
    - to:
        - ipBlock:
            cidr: 10.0.40.101/32  # Control plane node 1
        - ipBlock:
            cidr: 10.0.40.102/32  # Control plane node 2
        - ipBlock:
            cidr: 10.0.40.103/32  # Control plane node 3
      ports:
        - protocol: TCP
          port: 6443
```

2. Increased storage from 10Gi to 20Gi in postgres-cluster.yaml.

**Lesson**:
- Operators need Kubernetes API access - add before enabling default-deny
- The kubernetes.default.svc.cluster.local service (10.43.0.1:443) forwards to actual control plane nodes (10.0.40.x:6443), so you need both IPs
- Error messages can be misleading - always check full logs

---

### Issue 5: Immich Internal Communication

**Symptom**: Immich server couldn't connect to Valkey (Redis) at 10.43.180.27:6379.

**Investigation**:
```bash
kubectl get pods -n immich -l app.kubernetes.io/name=server --show-labels
kubectl get pods -n immich -l app.kubernetes.io/name=valkey --show-labels
```

**Root cause**: Network policy pod selectors used incorrect labels. Policy specified `immich-server` but actual label was `server`.

**Fix**: Updated pod selectors to match actual labels:
```yaml
podSelector:
  matchLabels:
    app.kubernetes.io/name: server  # NOT immich-server
```

**Lesson**: Always verify pod labels before writing NetworkPolicies. Use `--show-labels` to confirm.

---

### Issue 6: Loki "Empty Ring" Errors

**Symptom**: Loki write pods showing "error getting ingester clients: empty ring".

**Investigation**:
```bash
kubectl logs -n monitoring loki-write-0
# Error: cannot connect to S3 at 10.0.40.10:9000
# Error: cannot communicate with other Loki pods
```

**Root cause**:
1. No egress to S3/MinIO storage
2. No Loki pod-to-pod communication for ring membership

**Fix**:
1. Created `allow-loki-to-s3.yaml`:
```yaml
egress:
  - to:
      - ipBlock:
          cidr: 10.0.40.10/32
    ports:
      - protocol: TCP
        port: 9000
```

2. Added Loki-to-Loki ingress in `allow-monitoring.yaml`:
```yaml
ingress:
  - from:
      - podSelector:
          matchLabels:
            app.kubernetes.io/name: loki
```

**Lesson**: Distributed systems need pod-to-pod communication. Check architecture diagrams for all required connections.

---

### Issue 7: Proxmox Widget Connection Refused

**Symptom**: Homepage Proxmox widget showing HTTP 500 errors.

**Investigation**:
```bash
kubectl exec -n homepage <pod> -- wget https://proxmox.ronaldlokers.nl
# Connection refused to 10.0.40.100:443

kubectl get svc -n external-services proxmox -o yaml
# Service routes to external IP: 10.0.1.10:8006
```

**Root cause**:
1. Proxmox URL resolved to MetalLB VIP (hairpin NAT issue)
2. Homepage config used external URL instead of internal service
3. Homepage internet egress policy missing port 8006

**Fix**:
1. Updated homepage config to use internal service URL:
   `https://proxmox.external-services.svc.cluster.local:8006`

2. Added port 8006 to homepage internet egress policy:
```yaml
egress:
  - to:
      - ipBlock:
          cidr: 0.0.0.0/0
    ports:
      - protocol: TCP
        port: 8006
```

**Lesson**:
- Avoid hairpin NAT by using internal service URLs
- External services (via Endpoints) need specific egress ports
- Test connectivity from within pods, not just from outside cluster

---

## Debugging Methodology

This systematic approach proved effective:

### 1. Identify the Connection Pattern
```bash
# What is trying to connect to what?
# Source pod → Destination service:port
```

### 2. Check Egress from Source Namespace
```bash
kubectl get networkpolicy -n <source-namespace>
kubectl describe networkpolicy <policy-name> -n <source-namespace>
```

### 3. Check Ingress to Destination Namespace
```bash
kubectl get networkpolicy -n <dest-namespace>
kubectl describe networkpolicy <policy-name> -n <dest-namespace>
```

### 4. Verify Pod Labels
```bash
kubectl get pods -n <namespace> --show-labels
# Ensure podSelector labels match actual pod labels
```

### 5. Test from Source Pod
```bash
kubectl exec -n <namespace> <pod> -- nc -zv <service>.<namespace> <port>
kubectl exec -n <namespace> <pod> -- wget -O- http://<service>
kubectl exec -n <namespace> <pod> -- nslookup <service>.<namespace>.svc.cluster.local
```

### 6. Check Service Endpoints
```bash
kubectl get endpoints -n <namespace> <service>
# Verify endpoint IPs match pod IPs
```

### 7. Verify Policy Application
```bash
# Policies don't apply retroactively - restart pods
kubectl rollout restart -n <namespace> deployment/<name>
```

---

## Key Lessons Learned

### 1. NetworkPolicy Structure Matters
- Multiple `from` clauses create separate rules (AND logic)
- Combine selectors in a single `from` list for OR logic
- YAML structure directly impacts functionality

### 2. Default-Deny Blocks Everything
- Including same-namespace communication
- Including DNS (must explicitly allow)
- Including Kubernetes API access for operators

### 3. Test Incrementally
- Apply policies one namespace at a time
- Verify connectivity after each change
- Don't apply all policies at once in production

### 4. Operators Need Special Treatment
- CloudNative-PG needs Kubernetes API access
- Operators often need broader permissions than apps
- Check operator documentation for requirements

### 5. Verify Pod Labels
- `kubectl get pods --show-labels` is essential
- Labels in Helm charts may differ from documentation
- Test with actual deployed labels, not assumptions

### 6. Restart Pods After Policy Changes
- NetworkPolicies don't affect existing connections
- `kubectl rollout restart` applies new policies
- Watch for CrashLoopBackOff after restart

### 7. Check Full Logs
- Error messages can be misleading
- "Disk space" might mean "can't reach API"
- Always scroll through full logs, not just last line

### 8. Service Discovery Complexity
- Internal services: Use `.svc.cluster.local` names
- External services: Be careful with hairpin NAT
- Kubernetes service IP forwards to actual API server IPs

### 9. Distributed Systems Need Mesh Communication
- Loki ring requires pod-to-pod communication
- Check architecture for all required connections
- Not just client → server, but server ↔ server

### 10. Documentation is Critical
- Document each policy's purpose
- Explain why specific ports are allowed
- Note any non-obvious requirements

---

## Tools Used

### Essential Commands
```bash
# View policies
kubectl get networkpolicy -n <namespace>
kubectl describe networkpolicy <name> -n <namespace>

# Test connectivity
kubectl exec -n <namespace> <pod> -- nc -zv <host> <port>
kubectl exec -n <namespace> <pod> -- wget -O- http://<url>
kubectl exec -n <namespace> <pod> -- nslookup <domain>

# Check labels
kubectl get pods -n <namespace> --show-labels

# View logs
kubectl logs -n <namespace> <pod> --tail=100

# Restart deployments
kubectl rollout restart -n <namespace> deployment/<name>

# Check endpoints
kubectl get endpoints -n <namespace>

# Describe services
kubectl get svc -n <namespace> <name> -o yaml
```

---

## Prevention Strategies

### For Future NetworkPolicy Implementations

1. **Plan before implementing**
   - Map all traffic flows
   - Identify operator requirements
   - Document hairpin NAT scenarios

2. **Test in staging first**
   - Apply policies to staging
   - Verify all functionality
   - Only then promote to production

3. **Apply incrementally**
   - One namespace at a time
   - Verify between each step
   - Don't batch all changes

4. **Create policies before default-deny**
   - Write all allow policies first
   - Apply default-deny last
   - Reduces downtime

5. **Document as you go**
   - Note why each rule exists
   - Record troubleshooting steps
   - Update war stories

6. **Monitor after deployment**
   - Watch logs for connection errors
   - Check Grafana for service health
   - Be ready to rollback

---

## Final State

All services now running with zero-trust NetworkPolicies:
- ✅ Default-deny in all namespaces
- ✅ Explicit allow rules for all required traffic
- ✅ Namespace isolation working correctly
- ✅ No lateral movement possible
- ✅ All widgets and services functional

**Total policies**: 10 files, ~40 individual NetworkPolicy resources

---

## Related Documentation

- [Network Security Architecture](../network-security.md)
- [PostgreSQL Disaster Recovery Runbook](../runbooks/postgresql-cluster-disaster-recovery.md)
- [Loki Ring Unhealthy Instances](loki-replication-factor-ring.md)

---

**Author**: Claude Code (with human debugging)
**Reviewed**: 2026-02-26
**Tags**: networkpolicy, security, connectivity, debugging, zero-trust
