# Cloudflare

Tunnel ingress, DNS records and `always_use_https` for `ronaldlokers.nl` (#308).

## Running it

```
export CLOUDFLARE_API_TOKEN=…     # DNS edit, zone settings edit, tunnel edit
export AWS_ACCESS_KEY_ID=…        # B2 key id, restricted to the state bucket
export AWS_SECRET_ACCESS_KEY=…

cd terraform
terraform plan
terraform apply
```

Both credentials live in Proton Pass, not this repo.

## State

Backblaze B2 over its S3 API. The `skip_*` flags in `main.tf` are required —
`skip_s3_checksum` in particular, or every write fails.

**No locking**: B2 has no DynamoDB equivalent. Acceptable with one operator.

## Scope

TXT records are excluded; other services issue them.
