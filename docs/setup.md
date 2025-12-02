# Cluster Setup Guide

This guide walks through setting up both the staging and production Kubernetes clusters.

## Staging Cluster (k3d)

The staging cluster runs in k3d on an Ubuntu Server VM in Proxmox on the MS-01 mini PC.

### Prerequisites

- Ubuntu Server VM with Docker installed
- kubectl installed
- k3d installed

### Create the Cluster

```bash
k3d cluster create staging \
  --servers 1 \
  --agents 3 \
  --k3s-arg "--tls-san=10.0.40.52@server:0" \
  --port "80:80@loadbalancer" \
  --port "443:443@loadbalancer"
```

This creates a 4-node cluster (1 server + 3 agents) with:
- TLS certificate valid for the VM's IP address (10.0.40.52)
- HTTP and HTTPS ports exposed for ingress

### Configure kubectl Access

```bash
# Merge the kubeconfig
k3d kubeconfig merge staging --kubeconfig-merge-default

# Rename context for easier use
kubectl config rename-context k3d-staging staging
```

### Verify the Cluster

```bash
kubectl --context=staging get nodes
```

You should see 4 nodes in Ready state.

## Production Cluster (K3s HA)

The production cluster is a 3-node high-availability cluster with embedded etcd, running on Raspberry Pi CM5 modules in a Sipeed NanoCluster.

### Prerequisites

Install required packages on all nodes (kube-srv-1, kube-srv-2, kube-srv-3):

```bash
# Install open-iscsi (required for Longhorn storage)
sudo apt update
sudo apt install -y open-iscsi
sudo systemctl enable --now iscsid
```

### Install K3s on First Node

On **kube-srv-1 (10.0.40.101)**, install K3s with the `--cluster-init` flag to initialize the etcd cluster:

```bash
curl -sfL https://get.k3s.io | sh -s - server \
  --cluster-init \
  --disable helm-controller \
  --tls-san 10.0.40.101 \
  --tls-san 10.0.40.102 \
  --tls-san 10.0.40.103
```

This command:
- Initializes an embedded etcd cluster
- Disables the Helm controller (Flux will manage Helm releases)
- Adds TLS SANs for all three node IPs

**Get the join token**:

```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

Save this token - you'll need it for the other nodes.

### Install K3s on Additional Nodes

On **kube-srv-2 (10.0.40.102)** and **kube-srv-3 (10.0.40.103)**, join the cluster as additional control plane nodes:

```bash
# Replace <token> with the token from kube-srv-1
curl -sfL https://get.k3s.io | K3S_TOKEN="<token>" sh -s - server \
  --server https://10.0.40.101:6443 \
  --disable helm-controller \
  --tls-san 10.0.40.101 \
  --tls-san 10.0.40.102 \
  --tls-san 10.0.40.103
```

**Important notes**:
- Use `server` (not `agent`) to join as a control plane node
- The token must be on a single line with no line breaks
- All nodes must have the same TLS SANs

### Configure kubectl Access

Copy the kubeconfig from kube-srv-1 and merge it:

```bash
# Copy kubeconfig from kube-srv-1
scp kube-srv-1:/etc/rancher/k3s/k3s.yaml production-config.yaml

# Edit to replace 127.0.0.1 with the actual node IP
sed -i 's/127.0.0.1/10.0.40.101/g' production-config.yaml

# Merge with your main kubeconfig
KUBECONFIG=~kubeconfig:production-config.yaml kubectl config view --flatten > kubeconfig-merged
mv kubeconfig-merged kubeconfig

# Rename context for easier use
kubectl config rename-context default production
```

### Verify the Cluster

```bash
kubectl --context=production get nodes
```

All three nodes should show as Ready with roles "control-plane,etcd,master".

Verify etcd is running:

```bash
# On any node
sudo k3s kubectl get nodes
sudo systemctl status k3s

# Check etcd endpoints
sudo k3s etcd-snapshot save --etcd-s3=false
```

## Bootstrap Flux

After setting up either cluster, bootstrap Flux to enable GitOps deployment:

```bash
# Export GitHub credentials
export GITHUB_USER=ronaldlokers
export GITHUB_TOKEN=<personal-access-token>

# Bootstrap staging cluster
flux bootstrap github \
  --context=staging \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/staging \
  --personal

# Bootstrap production cluster
flux bootstrap github \
  --context=production \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/production \
  --personal
```

This will:
- Install Flux controllers in the cluster
- Create a deploy key for the repository
- Commit Flux manifests to the repository
- Configure Flux to watch the appropriate path

## Create SOPS Age Secret

Each environment uses its own age encryption key for SOPS. Create the secret in each cluster:

```bash
# For staging (staging-age.key stored in Proton Pass)
cat staging-age.key | kubectl --context=staging create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin

# For production (production-age.key stored in Proton Pass)
cat production-age.key | kubectl --context=production create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin
```

## Verify Deployment

After bootstrapping Flux and creating the SOPS secret, Flux will automatically deploy all infrastructure and applications.

Check Flux reconciliation status:

```bash
# For staging
flux get kustomizations --context=staging

# For production
flux get kustomizations --context=production
```

All kustomizations should show as "Applied" and "Ready".

Check application pods:

```bash
# For staging
kubectl --context=staging get pods -A

# For production
kubectl --context=production get pods -A
```

All pods should reach Running state within a few minutes.
