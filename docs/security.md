# Security & Secrets Management

This document describes how secrets are managed in the homelab using SOPS encryption with age keys.

## Overview

Secrets are encrypted using [SOPS](https://github.com/getsops/sops) with [age](https://github.com/FiloSottile/age) encryption. This allows secrets to be stored securely in Git while remaining readable only by those with the decryption key.

Flux automatically decrypts secrets during deployment using the cluster's age key stored in the `flux-system/sops-age` secret.

## Encryption Keys

Each environment has its own SOPS configuration and age encryption key for security isolation:

| Environment | Key File | Public Key | Storage |
|------------|----------|------------|---------|
| Staging | `staging-age.key` | `age1uq9nturwsx36q045qtrm85lkg8qmzpgk9srduqesxs2ahjurw53sp9rhm6` | Proton Pass |
| Production | `production-age.key` | `age1hh6cdyljk2ks5mkmxqx6g65c7a8rgndy5p2s2d7w2gvqx4h53ggqtwr7rh` | Proton Pass |

**Important**:
- Private keys are stored in Proton Pass (not in Git)
- Public keys are used in `.sops.yaml` configuration files
- Each environment can only decrypt its own secrets

## SOPS Configuration

Each cluster has its own `.sops.yaml` configuration file:

### Staging: `clusters/staging/.sops.yaml`

```yaml
creation_rules:
  - path_regex: .*.yaml
    encrypted_regex: ^(data|stringData)$
    age: age1uq9nturwsx36q045qtrm85lkg8qmzpgk9srduqesxs2ahjurw53sp9rhm6
```

### Production: `clusters/production/.sops.yaml`

```yaml
creation_rules:
  - path_regex: .*.yaml
    encrypted_regex: ^(data|stringData)$
    age: age1hh6cdyljk2ks5mkmxqx6g65c7a8rgndy5p2s2d7w2gvqx4h53ggqtwr7rh
```

These configurations:
- Apply to all `.yaml` files in the directory
- Only encrypt the `data` and `stringData` fields (Secret content)
- Leave metadata unencrypted for readability
- Use environment-specific age public keys

## Required Secrets

These secrets must be manually created in each cluster before deployment.

### flux-system/flux-system

Created automatically during Flux bootstrap:

```bash
export GITHUB_USER=ronaldlokers
export GITHUB_TOKEN=<personal-access-token>

# For staging
flux bootstrap github \
  --context=staging \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/staging \
  --personal

# For production
flux bootstrap github \
  --context=production \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/production \
  --personal
```

This secret contains:
- GitHub deploy key for repository access
- Known hosts for Git over SSH

### flux-system/sops-age

Age private key for decrypting SOPS-encrypted secrets.

**Create for staging**:
```bash
cat staging-age.key | kubectl --context=staging create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin
```

**Create for production**:
```bash
cat production-age.key | kubectl --context=production create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin
```

The private keys are retrieved from Proton Pass.

## Managed Secrets

These secrets are encrypted with SOPS and stored in this repository. Flux decrypts them automatically during deployment.

### cert-manager/cloudflare-api-token

Cloudflare DNS API token for Let's Encrypt DNS-01 challenges.

**Location**:
- Staging: `infrastructure/configs/staging/cert-manager/cloudflare-api-token-secret.yaml`
- Production: `infrastructure/configs/production/cert-manager/cloudflare-api-token-secret.yaml`

**Contents**:
- `api-token`: Cloudflare API token with DNS edit permissions

### linkding/linkding-container-env

Linkding application environment variables.

**Location**:
- Staging: `apps/staging/linkding/linkding-container-env-secret.yaml`
- Production: `apps/production/linkding/linkding-container-env-secret.yaml`

**Contents**:
- `LD_SUPERUSER_NAME`: Admin username
- `LD_SUPERUSER_PASSWORD`: Admin password

### renovate/renovate-container-env

Renovate GitHub token for automated dependency updates.

**Location**:
- Staging: `infrastructure/controllers/staging/renovate/renovate-container-env.yaml`
- Production: `infrastructure/controllers/production/renovate/renovate-container-env.yaml`

**Contents**:
- `RENOVATE_TOKEN`: GitHub personal access token with repository write permissions

## Auto-Generated Secrets

These secrets are automatically created by controllers and should not be manually managed.

### TLS Certificates

Created by cert-manager:
- `*-tls`: TLS certificates for ingresses
- `letsencrypt-production`: Let's Encrypt ACME account key
- `letsencrypt-staging`: Let's Encrypt staging ACME account key

### Grafana Credentials

Created by kube-prometheus-stack:
- `kube-prometheus-stack-grafana`: Grafana admin username and password

Retrieve Grafana credentials:
```bash
# For staging
kubectl --context=staging get secret -n monitoring kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d

# For production
kubectl --context=production get secret -n monitoring kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d
```

### Prometheus and Alertmanager

Created by kube-prometheus-stack:
- Configuration secrets for Prometheus
- Configuration secrets for Alertmanager

## Working with SOPS Secrets

### Encrypting a New Secret

1. Create the secret YAML file:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: my-secret
  namespace: my-namespace
data:
  key: dmFsdWU=  # base64-encoded value
```

2. Encrypt with SOPS (from the appropriate cluster directory):
```bash
# For staging secrets
cd clusters/staging
sops --encrypt --in-place ../../path/to/secret.yaml

# For production secrets
cd clusters/production
sops --encrypt --in-place ../../path/to/secret.yaml
```

The `.sops.yaml` file in the cluster directory determines which age key to use.

### Editing an Encrypted Secret

```bash
# For staging
cd clusters/staging
sops ../../path/to/secret.yaml

# For production
cd clusters/production
sops ../../path/to/secret.yaml
```

SOPS will:
1. Decrypt the file using your age key
2. Open it in your default editor
3. Re-encrypt it when you save and close

### Viewing an Encrypted Secret

```bash
# For staging
cd clusters/staging
sops --decrypt ../../path/to/secret.yaml

# For production
cd clusters/production
sops --decrypt ../../path/to/secret.yaml
```

### Re-encrypting Secrets with a New Key

If you need to change the encryption key:

1. Update the `.sops.yaml` file with the new public key
2. Re-encrypt all secrets:

```bash
# For staging
cd clusters/staging
find ../../ -name "*.yaml" -path "*/staging/*" -exec grep -l "sops:" {} \; | \
  xargs -I {} sops updatekeys --yes {}

# For production
cd clusters/production
find ../../ -name "*.yaml" -path "*/production/*" -exec grep -l "sops:" {} \; | \
  xargs -I {} sops updatekeys --yes {}
```

3. Update the `sops-age` secret in the cluster with the new private key

## Security Best Practices

1. **Never commit private keys**: Age private keys should never be committed to Git
2. **Use separate keys per environment**: Staging and production have separate keys
3. **Store private keys securely**: Use Proton Pass or another password manager
4. **Rotate keys periodically**: Re-encrypt secrets with new age keys regularly
5. **Limit key access**: Only share private keys with trusted team members
6. **Verify encryption**: Always check that secrets are encrypted before committing
7. **Use strong tokens**: Generate strong passwords and API tokens
8. **Rotate credentials**: Periodically rotate API tokens and passwords

## Troubleshooting

### "failed to decrypt" Error

If Flux reports decryption errors:

1. Verify the `sops-age` secret exists:
```bash
kubectl --context=production get secret -n flux-system sops-age
```

2. Check the secret contains the correct key:
```bash
kubectl --context=production get secret -n flux-system sops-age \
  -o jsonpath='{.data.age\.agekey}' | base64 -d
```

3. Verify the secret was encrypted with the correct public key:
```bash
sops --decrypt path/to/secret.yaml
```

If decryption fails locally, the secret was encrypted with a different key.

### Secret Not Decrypted

If a secret exists but isn't being decrypted by Flux:

1. Check Kustomization for SOPS configuration:
```bash
kubectl --context=production get kustomization -n flux-system infrastructure-controllers -o yaml
```

Look for:
```yaml
spec:
  decryption:
    provider: sops
    secretRef:
      name: sops-age
```

2. Check Flux logs:
```bash
kubectl --context=production logs -n flux-system -l app=kustomize-controller
```

### Wrong Environment Key Used

If you accidentally encrypted a staging secret with the production key (or vice versa):

1. Decrypt with the correct key:
```bash
cd clusters/production  # or staging
sops --decrypt ../../path/to/secret.yaml > /tmp/secret-decrypted.yaml
```

2. Re-encrypt with the correct key:
```bash
cd clusters/staging  # or production
sops --encrypt /tmp/secret-decrypted.yaml > ../../path/to/secret.yaml
rm /tmp/secret-decrypted.yaml
```
