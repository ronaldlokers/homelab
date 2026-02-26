# Runbook: NetworkPolicy Connectivity Troubleshooting

**Purpose**: Quick reference for diagnosing and fixing NetworkPolicy-related connectivity issues
**Severity**: High (service outages)
**Estimated Time**: 15-30 minutes

---

## Symptoms Checklist

Check if you're experiencing NetworkPolicy issues:

- [ ] Service returns "connection refused" or "timeout"
- [ ] Logs show DNS resolution failures
- [ ] Recently applied or modified NetworkPolicies
- [ ] Pod restart didn't fix the issue
- [ ] Works from outside cluster but not inside
- [ ] Worked before NetworkPolicy implementation

---

## Quick Diagnosis (5 minutes)

### Step 1: Identify the Connection Pattern

**What is trying to connect to what?**

```
Source: <pod-name> in <namespace-a>
Destination: <service-name> in <namespace-b>:<port>
```

### Step 2: Check if Policies Exist

```bash
# Check source namespace egress
kubectl get networkpolicy -n <source-namespace>

# Check destination namespace ingress
kubectl get networkpolicy -n <destination-namespace>
```

**Expected**: Both namespaces should have policies (unless intentionally open)

### Step 3: Test Connectivity

```bash
# DNS test
kubectl exec -n <source-ns> <pod> -- nslookup <service>.<dest-ns>.svc.cluster.local

# Port connectivity test
kubectl exec -n <source-ns> <pod> -- nc -zv <service>.<dest-ns> <port>

# HTTP test (if applicable)
kubectl exec -n <source-ns> <pod> -- wget -O- --timeout=5 http://<service>.<dest-ns>:<port>
```

**If DNS fails**: Check DNS NetworkPolicy (see DNS Issues section)
**If port fails**: Check egress/ingress policies (see Port Blocked section)

---

## Common Issues & Fixes

### Issue 1: DNS Resolution Failing

**Symptom**: `nslookup` returns "server can't find" or timeout

**Diagnosis**:
```bash
kubectl describe networkpolicy allow-dns -n <namespace>
```

**Expected output**:
```yaml
Allowing egress traffic:
  To Port: 53/UDP
  To Port: 53/TCP
  To:
    NamespaceSelector: kubernetes.io/metadata.name=kube-system
```

**Fix**: Add DNS policy to namespace
```bash
# Apply the allow-dns policy
kubectl apply -f infrastructure/configs/base/network-policies/allow-dns.yaml

# Or manually create:
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
  namespace: <namespace>
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
EOF
```

**Verify**:
```bash
kubectl exec -n <namespace> <pod> -- nslookup kubernetes.default
```

---

### Issue 2: Port Blocked by Egress Policy

**Symptom**: DNS works, but connection to service fails

**Diagnosis**:
```bash
kubectl describe networkpolicy -n <source-namespace> | grep -A 20 "Allowing egress"
```

**Check**:
1. Does destination namespace appear in `To:` selectors?
2. Is the required port listed in `To Port:`?

**Fix**: Add namespace and port to egress policy

```bash
# Edit the policy file
vim infrastructure/configs/base/network-policies/allow-<source>-to-<dest>.yaml

# Or patch inline:
kubectl patch networkpolicy <policy-name> -n <namespace> --type='json' -p='[
  {
    "op": "add",
    "path": "/spec/egress/0/to/-",
    "value": {
      "namespaceSelector": {
        "matchLabels": {
          "kubernetes.io/metadata.name": "<dest-namespace>"
        }
      }
    }
  }
]'
```

**Verify**:
```bash
kubectl exec -n <source-ns> <pod> -- nc -zv <service>.<dest-ns> <port>
```

---

### Issue 3: Ingress Blocked at Destination

**Symptom**: Egress allowed, but connection still refused

**Diagnosis**:
```bash
kubectl describe networkpolicy -n <dest-namespace> | grep -A 20 "Allowing ingress"
```

**Check**:
1. Does source namespace appear in `From:` selectors?
2. Is the service port listed in `To Port:`?

**Fix**: Add namespace to ingress policy

```bash
# Edit destination namespace policy
vim infrastructure/configs/base/network-policies/allow-ingress-to-<dest>.yaml

# Ensure ingress allows from source namespace
```

**Common mistake**: Multiple `from` clauses instead of combined list

**Wrong**:
```yaml
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: namespace-a
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: namespace-b
```

**Correct**:
```yaml
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: namespace-a
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: namespace-b
    ports:
      - protocol: TCP
        port: 5432
```

**Verify**:
```bash
kubectl exec -n <source-ns> <pod> -- nc -zv <service>.<dest-ns> <port>
```

---

### Issue 4: Pod Labels Don't Match Policy Selectors

**Symptom**: Policies look correct, but still blocked

**Diagnosis**:
```bash
# Check actual pod labels
kubectl get pods -n <namespace> --show-labels

# Check what policy selects
kubectl get networkpolicy <policy> -n <namespace> -o yaml | grep -A 5 podSelector
```

**Common mismatch**: Policy uses `app=myapp` but pod has `app.kubernetes.io/name=myapp`

**Fix**: Update policy podSelector to match actual labels

```yaml
podSelector:
  matchLabels:
    app.kubernetes.io/name: myapp  # Use actual label
```

---

### Issue 5: Policy Not Applied (Pod Needs Restart)

**Symptom**: Policy looks correct, but established connections still blocked

**Root cause**: NetworkPolicies don't affect existing connections

**Fix**: Restart the pod

```bash
# For deployment
kubectl rollout restart -n <namespace> deployment/<name>

# For statefulset
kubectl rollout restart -n <namespace> statefulset/<name>

# Watch rollout
kubectl rollout status -n <namespace> deployment/<name>
```

**Verify**: Test connectivity after new pod is running

---

### Issue 6: Kubernetes API Access Blocked

**Symptom**: Operator logs show "connection refused" to 10.43.0.1:443 or control plane IPs

**Diagnosis**:
```bash
kubectl logs -n <namespace> <pod> | grep "connection refused"
kubectl logs -n <namespace> <pod> | grep "10.43.0.1"
```

**Fix**: Add API egress policy (critical for operators)

```bash
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-k8s-api-egress
  namespace: <namespace>
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 10.43.0.1/32  # Kubernetes service IP
      ports:
        - protocol: TCP
          port: 443
    - to:
        - ipBlock:
            cidr: 10.0.40.101/32  # Control plane 1
        - ipBlock:
            cidr: 10.0.40.102/32  # Control plane 2
        - ipBlock:
            cidr: 10.0.40.103/32  # Control plane 3
      ports:
        - protocol: TCP
          port: 6443
EOF
```

**Note**: Required for CloudNative-PG, Longhorn, and other operators

---

### Issue 7: External Service Access Blocked

**Symptom**: Can't reach internet or external services

**Diagnosis**:
```bash
kubectl exec -n <namespace> <pod> -- curl -v https://google.com
```

**Fix**: Add internet egress policy

```bash
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-internet-egress
  namespace: <namespace>
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
EOF
```

**Note**: Excludes private networks to prevent accessing internal infrastructure

---

## Diagnostic Commands

### View All Policies in Namespace
```bash
kubectl get networkpolicy -n <namespace>
```

### Describe Specific Policy
```bash
kubectl describe networkpolicy <policy-name> -n <namespace>
```

### View Policy YAML
```bash
kubectl get networkpolicy <policy-name> -n <namespace> -o yaml
```

### Test DNS
```bash
kubectl exec -n <namespace> <pod> -- nslookup <service>.<namespace>.svc.cluster.local
```

### Test Port Connectivity
```bash
kubectl exec -n <namespace> <pod> -- nc -zv <host> <port>
```

### Test HTTP/HTTPS
```bash
kubectl exec -n <namespace> <pod> -- wget -O- --timeout=5 http://<url>
kubectl exec -n <namespace> <pod> -- curl -v https://<url>
```

### Check Pod Labels
```bash
kubectl get pods -n <namespace> --show-labels
kubectl get pods -n <namespace> <pod> -o jsonpath='{.metadata.labels}'
```

### Check Service Endpoints
```bash
kubectl get endpoints -n <namespace> <service>
```

### View Full Pod Logs
```bash
kubectl logs -n <namespace> <pod> --tail=200
```

---

## Verification Steps

After applying a fix:

1. **Verify policy applied**:
   ```bash
   kubectl get networkpolicy -n <namespace> <policy-name>
   ```

2. **Restart pod** (if needed):
   ```bash
   kubectl rollout restart -n <namespace> deployment/<name>
   ```

3. **Test connectivity**:
   ```bash
   kubectl exec -n <namespace> <pod> -- nc -zv <service>.<dest-ns> <port>
   ```

4. **Check application logs**:
   ```bash
   kubectl logs -n <namespace> <pod> --tail=50
   ```

5. **Verify service health**:
   ```bash
   kubectl get pods -n <namespace>
   ```

---

## Rollback Procedure

If NetworkPolicy causes service outage:

### Emergency: Remove All Policies from Namespace

```bash
# List policies
kubectl get networkpolicy -n <namespace>

# Delete all
kubectl delete networkpolicy --all -n <namespace>

# Pods can now connect freely (insecure but functional)
```

### Targeted: Remove Specific Policy

```bash
kubectl delete networkpolicy <policy-name> -n <namespace>
```

### Revert to Previous Version

```bash
# If using GitOps
git revert <commit-hash>
git push

# Then reconcile
flux reconcile kustomization infrastructure-configs
```

---

## Prevention Checklist

Before implementing NetworkPolicies:

- [ ] Map all required traffic flows
- [ ] Test in staging environment first
- [ ] Create all allow policies before default-deny
- [ ] Verify pod labels match policy selectors
- [ ] Document why each rule exists
- [ ] Plan for operator requirements (API access)
- [ ] Have rollback plan ready
- [ ] Schedule during maintenance window

---

## Related Documentation

- [Network Security Architecture](../network-security.md) - Full policy documentation
- [NetworkPolicy War Story](../war-stories/networkpolicy-connectivity-debugging.md) - Detailed debugging examples
- [Kubernetes NetworkPolicy Docs](https://kubernetes.io/docs/concepts/services-networking/network-policies/) - Official documentation

---

## Escalation

If this runbook doesn't resolve the issue:

1. Check war story for similar scenarios: `docs/war-stories/networkpolicy-connectivity-debugging.md`
2. Review full network security documentation: `docs/network-security.md`
3. Search cluster logs for related errors: `kubectl logs -n flux-system <kustomize-controller>`
4. Review recent Git commits for policy changes: `git log --oneline infrastructure/configs/base/network-policies/`

---

**Last Updated**: 2026-02-26
**Maintainer**: Infrastructure Team
**Related Runbooks**: PostgreSQL Cluster Disaster Recovery, Loki Ring Unhealthy Instances
