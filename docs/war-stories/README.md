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
- [Loki Ring Errors: Too Many Unhealthy Instances](loki-replication-factor-ring.md) - Replication factor mismatch in staging environment
- [Kustomize ConfigMap Hash Suffix Breaking Alloy](kustomize-configmap-hash-suffix.md) - When Kustomize and Helm ConfigMap names don't match
- [inotify Limits Exhausted in k3d](inotify-limits-k3d.md) - "Too many open files" errors in Docker-based clusters

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
