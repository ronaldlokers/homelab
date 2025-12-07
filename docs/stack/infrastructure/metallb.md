# MetalLB (Production Only)

[MetalLB](https://metallb.universe.tf/) provides network load balancing for bare-metal Kubernetes clusters.

**Version**: 0.14.9

**Architecture**:
- **metallb-controller**: Manages IP address assignment for LoadBalancer services
- **metallb-speaker**: Announces IPs on the local network (runs as DaemonSet)

**Configuration**:
- **IPAddressPool**: Defines the pool of IP addresses MetalLB can assign (10.0.40.100/32)
- **L2Advertisement**: Configures Layer 2 mode for IP announcement

**Load Balancer IP**: 10.0.40.100

**How it works**:
1. LoadBalancer service created (e.g., Traefik)
2. MetalLB controller assigns an IP from the pool
3. Speaker pods announce the IP via ARP on the local network
4. Traffic to the VIP is routed to the appropriate service
5. Automatic failover if a node goes down

**Benefits**:
- Single stable IP address for ingress instead of round-robin DNS
- Automatic failover between nodes
- Standard Kubernetes LoadBalancer interface
- No external load balancer hardware required

**Layer 2 Mode**:
- Uses ARP to announce IP addresses on the local network
- Simple configuration, no BGP required
- IP moves between nodes automatically on failure
- Suitable for homelab environments
