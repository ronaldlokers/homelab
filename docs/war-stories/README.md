# War Stories

Real-world challenges encountered while building and operating this homelab Kubernetes cluster.

These stories document actual problems, debugging processes, and solutions. They serve as:
- Learning resources for troubleshooting similar issues
- Documentation of non-obvious pitfalls
- Context for design decisions made in the infrastructure

## Index

### Infrastructure

- [NFS Mounts Failing on Debian Trixie](nfs-debian-trixie.md) - The mysterious "mount program didn't pass remote address" error
- [Longhorn ReadWriteMany Multi-Attach Errors](longhorn-rwx-multi-attach.md) - PVC access mode changes and the recreation challenge
- [Longhorn S3 Backups Failing with MinIO](longhorn-s3-virtual-hosted-style.md) - Virtual-hosted vs path-style S3 URLs
- [PostgreSQL Cluster Disaster Recovery Bootstrap Mode](postgres-bootstrap-recovery.md) - Ensuring clusters restore from backup after deletion
- **[PostgreSQL WAL Archiving Failure Causing PVC Fill-Up](postgres-wal-archiving-failure-pvc-fillup.md)** - 68 days of silent WAL accumulation, root cause analysis, and preventive monitoring
- [Loki Ring Errors: Too Many Unhealthy Instances](loki-replication-factor-ring.md) - Replication factor mismatch in staging environment
- [Kustomize ConfigMap Hash Suffix Breaking Alloy](kustomize-configmap-hash-suffix.md) - When Kustomize and Helm ConfigMap names don't match
- [inotify Limits Exhausted in k3d](inotify-limits-k3d.md) - "Too many open files" errors in Docker-based clusters
- [NetworkPolicy Connectivity Debugging](networkpolicy-connectivity-debugging.md) - Systematic troubleshooting of zero-trust network segmentation issues
- **[Staging Cluster Frozen by Unbounded Docker Disk Growth](staging-disk-pressure-docker-bloat.md)** - Six weeks of silent GitOps failure from missing Docker log rotation and unpruned container images
- **[Switching to Recreate Strategy Couldn't Fix Itself Through Git Alone](deployment-recreate-strategy-ssa-conflict.md)** - A server-side-apply edge case where a stale, API-server-defaulted field blocked a Deployment strategy change until a one-time imperative patch
- **[A HelmRelease Stuck "Failed" for Seven Months While Its Pods Ran Fine](flux-helmrelease-terminal-failure.md)** - Flux's terminal-error circuit breaker silently blocking all future reconciliation attempts
- **[A PostgreSQL Replica That Wouldn't Come Back](postgres-replica-networkpolicy-dataplane-sync.md)** - Ruling out a wrong node-flakiness hypothesis with a controlled A/B test, then finding a stale NetworkPolicy dataplane sync as the real cause
- **[The Photo Database That Had Backups — Except It Didn't](cnpg-invisible-backup-gap.md)** - Four months of zero recoverability behind green dashboards: WAL archiving without a base backup, an hourly "daily" cron, an exact-match PodMonitor, and alerts selecting a label that never existed
- **[All Three Control-Plane Nodes Running etcd on SD Card Instead of NVMe](etcd-emmc-storage-latency.md)** - A NetworkPolicy fix that wouldn't converge led to 30x-over etcd read latency, explaining a 4-day-old propagation mystery

### Applications

- [Immich Ingress and Version Management](immich-helm-migration.md) - Moving from separate Ingress resources to Helm-managed configuration
- [HPA Not Working Without Resource Requests](hpa-resource-requests.md) - Why HPAs show `<unknown>` CPU metrics

## Lessons Learned

1. **Read error messages carefully, but don't trust them completely** - "Mount program didn't pass remote address" actually meant "missing nfs-common package"
2. **PVC access modes are immutable** - You can't change ReadWriteOnce to ReadWriteMany without recreating the PVC
3. **HPAs need resource requests** - Percentage-based CPU targets require requests to calculate percentages
4. **Debian Trixie is minimal by design** - Don't assume packages like nfs-common are installed
5. **Test version management strategies early** - Following Flux best practices (versions in overlays) prevents headaches later
6. **Helm charts have their own ingress management** - Don't create separate Ingress resources when the chart provides it
7. **S3-compatible storage isn't always compatible** - MinIO uses path-style URLs by default, AWS uses virtual-hosted-style
8. **Bootstrap mode determines disaster recovery behavior** - PostgreSQL clusters should use recovery mode in production to restore from backups after deletion
9. **Test your disaster recovery process** - Don't wait for an accident to find out if your backups actually restore
10. **Replication factor ≠ replica count** - Loki's replication_factor controls data copies, not component instances
11. **Kustomize and Helm need coordination** - ConfigMap hash suffixes break Helm's static name references
12. **k3d shares inotify limits across all containers** - Default Linux limits are too low for Docker-based Kubernetes
13. **"Too many open files" is often about inotify watches** - Not file descriptors, check `/proc/sys/fs/inotify/`
14. **Staging doesn't need production HA** - Simpler configurations (replication_factor: 1) make debugging easier
15. **NetworkPolicy structure matters** - Multiple `from` clauses create AND logic, not OR - combine selectors in one list
16. **Default-deny blocks everything** - Including same-namespace traffic, DNS, and Kubernetes API access
17. **Verify pod labels before writing NetworkPolicies** - Helm chart labels may differ from documentation
18. **NetworkPolicies don't apply retroactively** - Restart pods after policy changes to apply new rules
19. **Operators need Kubernetes API access** - CloudNative-PG and similar operators require egress to 10.43.0.1:443
20. **Error messages can be misleading** - "Not enough disk space" in PostgreSQL actually meant "can't reach API server"
21. **WAL archiving failures are silent** - PostgreSQL can report `failed_count: 0` while files accumulate for months
22. **Disabled archiving is a ticking time bomb** - Without continuous WAL archiving, files accumulate until PVCs fill up
23. **wal_keep_size is a critical safety valve** - Prevents unlimited WAL growth at the cost of some PITR capability
24. **Monitor what you can't see** - 68 days of WAL accumulation went unnoticed without proper alerts
25. **Archiving "success" can be misleading** - Archive command running doesn't mean files are reaching remote storage
26. **Don't rush to quick fixes during incidents** - Understanding the full timeline is crucial for proper prevention
27. **Configuration changes need monitoring** - Critical features (like backups) being disabled should trigger alerts
28. **A system that fails can't self-heal its own root cause** - If Flux can't reconcile because the disk is full, it can't deploy the fix for the disk being full either
29. **Docker's default logging has no rotation** - Easy to forget on any long-lived host, not just k3d nodes
30. **A single-replica Deployment with an exclusive-attach PVC can never complete a RollingUpdate** - It's a structural deadlock, not a flaky rollout; use `Recreate` proactively
31. **`null` in server-side-apply is not a guaranteed field-clear** - Verify with `--dry-run=server` before relying on documented SSA behavior, especially for API-server-defaulted fields
32. **When two fields must change together, change them in one atomic operation** - A `replace` avoids a window where defaulting logic can reassert an old value mid-change
33. **A `Ready: False` controller status can be completely disconnected from actual resource health** - Always cross-check against the resources it's supposed to manage
34. **Flux's terminal-error state is a circuit breaker, not a bug** - It exists to stop broken releases from retrying forever; use `flux reconcile --reset` to clear it once the underlying cause has resolved
35. **"Connection refused rules out NetworkPolicy" is CNI-implementation-specific, not universal** - Some policy engines use REJECT semantics, producing exactly that symptom
36. **A/B tests with throwaway resources are cheap and decisive** - Two pods on the same node, one variable changed, settles a hypothesis in under a minute
37. **CNPG doesn't reuse instance ordinals by design** - A "missing" `postgres-cluster-1` after recreation isn't data loss, it's intentional identity separation
38. **Never hardcode a specific database instance ordinal in scripts or docs** - Discover the current primary dynamically via its role label instead

## Contributing Your Own War Stories

When adding a new war story:

1. Create a new markdown file in this directory
2. Use the template structure:
   - **The Problem** (symptoms)
   - **The Investigation** (how you debugged it)
   - **The Root Cause** (what actually caused it)
   - **The Solution** (how to fix it)
   - **Prevention** (how to avoid it in the future)
3. Update this README index
4. Include actual error messages and command outputs
5. Be honest about dead-ends and wrong assumptions
