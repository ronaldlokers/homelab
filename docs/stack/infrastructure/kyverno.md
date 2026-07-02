# Kyverno (Staging Only)

[Kyverno](https://kyverno.io/) is a Kubernetes-native policy engine used for admission control - validating, mutating, and generating resources based on declarative policies.

**Version**: 3.7.1

**Status**: Controller deployed and healthy on staging. **No `ClusterPolicy` resources exist yet** - the controller isn't currently enforcing or auditing anything. This is infrastructure staged ahead of policy rollout, not an active security control.

**Architecture**:
- **admissionController**: Intercepts and evaluates API requests against configured policies
- **backgroundController**: Applies policies to existing resources (not just new ones)
- **cleanupController**: Handles policy-driven resource cleanup
- **reportsController**: Generates `PolicyReport`/`ClusterPolicyReport` resources

**Configuration**:
- `config.webhooks` excludes `kube-system`, `kube-public`, `kube-node-lease`, and `flux-system` from admission webhook coverage, so core cluster components are never subject to policy evaluation
- All four controllers run at 1 replica with modest resource limits (256-512Mi memory, 50-100m CPU requests), appropriate for a homelab-scale cluster
- `policyReports.enabled: true` and `grafana.enabled: true` are set, so once policies exist their violations will surface in both `kubectl get policyreport` and a Grafana dashboard

**Environment scope**: staging only (`infrastructure/controllers/staging/kyverno/`). Not yet deployed to production - the plan is to validate policies on staging first, then promote.

**Planned policies** (not yet written):
- Require resource requests/limits on all containers
- Disallow privileged containers
- Require non-root containers
- Disallow `:latest` image tags

**Deployment note**: getting the controller installed required fixing a Helm values-schema mismatch - chart 3.7.1 expects `config.webhooks` as a map (`namespaceSelector.matchExpressions`), not the list-of-namespaceSelector-objects shape used in some older chart versions/examples. See [`infrastructure/controllers/base/kyverno/release.yaml`](/infrastructure/controllers/base/kyverno/release.yaml) for the current, correct shape.
