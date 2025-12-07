# Homepage

[Homepage](https://gethomepage.dev/) is a modern, fully static, fast application dashboard with integrations.

**Features**:
- Service dashboard with icons and links
- Kubernetes cluster monitoring widgets
- Resource usage displays (CPU, memory)
- Service widgets with live data
- Automatic service discovery via Ingress annotations
- Customizable layouts and themes
- Over 100 service integrations

**Deployment**:
- Single replica
- RBAC-enabled ServiceAccount for cluster access
- ConfigMap-based configuration
- No persistent storage required (stateless)

**Access**:
- **Staging**: https://homepage.staging.ronaldlokers.nl
- **Production**: https://homepage.ronaldlokers.nl

**Configuration**:
- Kubernetes cluster mode enabled for monitoring
- Pre-configured service widgets for Linkding and Grafana
- Dark theme with clean header style
- Custom bookmarks and search integration

**Kubernetes Integration**:
- ClusterRole for reading namespaces, pods, nodes, and ingresses
- Automatic service discovery from Ingress annotations
- Real-time metrics from Kubernetes metrics API
- Resource usage tracking per service
