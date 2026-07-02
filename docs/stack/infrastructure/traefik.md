# Traefik

[Traefik](https://traefik.io/) is the ingress controller and reverse proxy.

**Deployment**: Installed by K3s by default

**Features**:
- Automatic service discovery
- SNI-based routing
- TLS termination
- HTTP to HTTPS redirect middleware
- IP allowlist middleware for restricting admin tools to the local network
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

**Middlewares** (production, in `infrastructure/configs/production/`):
- `kube-system-https-redirect` - forces HTTP → HTTPS
- `kube-system-local-network-only` - `ipAllowList` restricting a resource to `10.0.0.0/16` (the local network). Chained onto Prometheus, pgAdmin, and Longhorn UI's ingresses since none of the three have their own authentication layer. Grafana is intentionally excluded - it has its own login and is meant to be reachable from outside the LAN.
