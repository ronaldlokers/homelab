# Cloudflare

What of this estate is reachable from the internet is decided here: the tunnel's
ingress rules, the zone's DNS, and whether Cloudflare forces HTTPS. Until this
existed those lived only in a dashboard, which is how removing Immich's network
path in-cluster left it serving anyway — the hostname was routed somewhere
nothing in this repository could see (#308).

## Running it

Two credentials, neither in this repository:

```
export CLOUDFLARE_API_TOKEN=…     # account token: DNS edit, zone settings edit, tunnel edit
export AWS_ACCESS_KEY_ID=…        # B2 application key id, restricted to the state bucket
export AWS_SECRET_ACCESS_KEY=…    # its applicationKey

cd terraform
terraform plan     # what would change
terraform apply    # change it
```

Both live in Proton Pass. The B2 key is scoped to
`lokilabs-homelab-terraform-state` and can reach nothing else — notably not the
postgres backup bucket, which has its own credential.

## State

Backblaze B2 via its S3-compatible API. The `skip_*` flags in `main.tf` are
required, not cosmetic: the AWS SDK asserts things only true of AWS, and
`skip_s3_checksum` in particular is the one that makes every write fail if
omitted.

**There is no locking.** B2 has no DynamoDB equivalent. With one operator the
practical risk is running `apply` twice concurrently; if this ever grows a
second, that needs revisiting.

## What is managed

- the `homelab-prod` tunnel and its ingress rules
- the zone's A and CNAME records
- `always_use_https`

TXT records are deliberately excluded: they are verification and mail records
issued by other services, and this repo does not generate their values.

## What was deleted rather than imported

Two tunnels with zero connectors: `linkdingkube`, which still carried a route to
`http://linkding:9090`, and `Familie C. Lokers`. A dormant tunnel with a live
route and a valid credential is a standing exposure — anyone running a connector
with that token publishes the service, and nothing here would have said so.
