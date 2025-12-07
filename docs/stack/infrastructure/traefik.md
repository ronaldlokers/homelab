# Traefik

[Traefik](https://traefik.io/) is the ingress controller and reverse proxy.

**Deployment**: Installed by K3s by default

**Features**:
- Automatic service discovery
- SNI-based routing
- TLS termination
- HTTP to HTTPS redirect middleware
- Integration with cert-manager for automatic TLS

**How it works**:
1. Ingress resource created with host and path rules
2. Traefik detects the ingress
3. Configures routing rules
4. cert-manager provisions TLS certificate
5. Traefik serves traffic with TLS termination

**Load Balancing**:
- **Staging**: k3d built-in load balancer forwards to Traefik
- **Production**: K3s ServiceLB exposes Traefik on all node IPs

**Access**:
- Traefik runs in `kube-system` namespace
- LoadBalancer service on ports 80 and 443
- Dashboard not exposed (security)
