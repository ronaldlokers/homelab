# cert-manager

[cert-manager](https://cert-manager.io/) provides automated TLS certificate management.

**Version**: v1.16.3

**Components**:
- **cert-manager-controller**: Main controller for certificate lifecycle
- **cert-manager-webhook**: Validating webhook for cert-manager resources
- **cert-manager-cainjector**: Injects CA bundles into webhooks and API services

**Certificate Issuers**:
- **letsencrypt-production**: Production Let's Encrypt issuer (rate-limited)
- **letsencrypt-staging**: Staging issuer for testing

**DNS-01 Challenge**:
Uses Cloudflare DNS API for DNS-01 challenges:
1. cert-manager receives certificate request
2. Creates TXT record in Cloudflare: `_acme-challenge.domain.com`
3. Let's Encrypt verifies the TXT record
4. Certificate issued and stored in Secret
5. TXT record cleaned up

**Benefits of DNS-01**:
- Works for services not publicly accessible
- Can issue wildcard certificates
- No port 80/443 requirements

**Configuration**:
- Cloudflare API token stored in SOPS-encrypted secret
- Automatic certificate renewal at 30 days before expiry
- Certificates valid for 90 days
