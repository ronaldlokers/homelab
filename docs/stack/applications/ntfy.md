# ntfy

[ntfy](https://ntfy.sh/) is a simple HTTP-based pub-sub notification service for sending notifications to phones and desktops via scripts.

**Features**:
- HTTP-based pub/sub for sending notifications
- Web app for receiving notifications
- Mobile apps (iOS and Android)
- Web push notifications
- User authentication
- Topic-based subscriptions
- Attachment support
- REST API

**Deployment**:
- Single replica
- Persistent storage for cache and web push database
- Version: v2.15
- Production only (not deployed in staging)

**Storage**:
- PVC for cache database: `/var/cache/ntfy/cache.db`
- Web push database: `/var/cache/ntfy/webpush.db`
- Cache duration: 12 hours

**Access**:
- **Production**: https://ntfy.ronaldlokers.nl

**Authentication**:
- Login enabled for authenticated topics
- User credentials managed via ntfy CLI

**Configuration**:
- Base URL: https://ntfy.ronaldlokers.nl
- Upstream relay: https://ntfy.sh (for iOS notifications)
- Web push enabled with VAPID keys
- Web push email: ronaldlokers@me.com
- Web push expiry: 60 days (warning at 55 days)
- Behind proxy mode enabled (for Traefik integration)
- Prometheus metrics enabled at `/metrics` endpoint

**Secrets**:
- Web push VAPID keys (public and private)
- Upstream access token for iOS relay
- Stored in SOPS-encrypted `ntfy-secret.yaml`

**Ingress**:
- TLS with wildcard certificate from cert-manager
- HTTPS redirect middleware
- WebSocket support for real-time notifications

**Monitoring**:
- Prometheus metrics exposed at `/metrics`
- Liveness probe: `GET /v1/health`
- Readiness probe: `GET /v1/health`

**Use Cases**:
- Server monitoring alerts
- Deployment notifications
- IoT device notifications
- Home automation alerts
- Personal reminders and notifications
