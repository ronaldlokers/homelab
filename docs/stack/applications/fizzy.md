# Fizzy

37signals' kanban board — Rails + Hotwire, SQLite, no external database.
Production only, at https://fizzy.ronaldlokers.nl.

Structurally the same workload as [Campfire](./campfire.md): same upstream
lineage, uid/gid 1000, port 80 behind Thruster, everything under
`/rails/storage`. Most of what is true there is true here, so this documents
only what differs.

## Image pinning

Upstream publishes **no semver tags** — only a floating `main` and a
`sha-<short>` per build. Campfire's `newTag: v1.4.9` has no equivalent.

So the overlay pins the **digest** and keeps `main` as documentation:

```yaml
images:
  - name: ghcr.io/basecamp/fizzy
    newTag: main
    digest: sha256:e2f8c5ac…
```

Renovate raises digest bumps as reviewable PRs, the same pattern the Renovate
CronJob already uses on its own image. Without the digest the image could
change under the tag with no commit here to show it — which is exactly what
pinning exists to prevent.

Note the ghcr tag listing shows only `-amd64` suffixed tags, which looks like
there is no arm64 build. There is: `main` is a multi-arch OCI index containing
both, and the suffixed tags are build-stage artefacts. Check the index, not the
tag list.

## Signing in — no SMTP here

Fizzy authenticates by emailing a **6-character verification code**. This
homelab has no SMTP relay and no mail credentials anywhere, so that email
cannot be sent.

Unset, `SMTP_ADDRESS` does not disable mail — it falls back to a local MTA, so
delivery fails rather than no-op'ing:

```
Error performing ActionMailer::MailDeliveryJob …
Errno::ECONNREFUSED (Connection refused - connect(2) for "localhost" port 25)
```

It is not a blocker, because the code is only needed to establish a session.
**Read it from the database, not the log.** Upstream's Docker guide says the
code appears in the container output; it does not here. The mail body renders
and is then handed to the failing delivery job, so the only place the code
exists is the `MagicLink` row:

```bash
kubectl exec -n fizzy --context=production deploy/fizzy -- bin/rails runner '
  m = MagicLink.order(:created_at).last
  puts m ? { code: m.code, purpose: m.purpose, expires_at: m.expires_at }.inspect
         : "no pending code — request one from the sign-in page first"'
```

Codes expire **15 minutes** after they are requested, and the row is deleted
the moment one is used, so request from the sign-in page first and run this
second. `purpose` is `sign_up` for the very first one and `sign_in` afterwards.
The nil branch matters: without it the command dies on `nil.code` and buries
the reason in a Ruby backtrace.

Then **register a passkey immediately.** Fizzy supports WebAuthn
(`ActionPack::Passkey`), and sessions are database-backed rather than
cookie-expiry-bound, so after this the email path is not needed again on that
device.

Every attempt leaves a failed `ActionMailer::MailDeliveryJob` behind in
`production_queue.sqlite3`. Harmless at one sign-in per device; worth knowing
before wondering why SolidQueue has a failed-job backlog.

If that becomes annoying — a new device, a cleared browser — the fix is an SMTP
relay: `SMTP_ADDRESS`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` and
`MAILER_FROM_ADDRESS` into the `fizzy-secrets` SOPS secret, plus a
NetworkPolicy egress rule for the submission port. `allow-internet-egress`
opens **443 only**; 587 is deliberately not open.

There is no OIDC support, so unlike linkding, mealie and immich this does not
sit behind authentik.

## Not multi-tenant

`MULTI_TENANT=false` is set explicitly. Upstream defaults to it, but the
setting is the difference between a personal board and an open signup page on a
publicly resolvable host, so it is stated rather than assumed.

## Egress

Narrower than Campfire's. Fizzy has **no link unfurler** — no feature that
fetches an attacker-chosen URL — so the RFC1918 exclusions are defence in depth
rather than containment of a known behaviour. Port 443 only; Campfire also
needs 80 for unfurling.

Web Push works the same way: VAPID keys in the SOPS secret, deliveries to
Apple's and Google's endpoints, and on iOS only from an installed PWA.

## Storage and backups

One 5Gi Longhorn PVC carrying the SQLite database *and* every attachment. The
`recurring-job.longhorn.io` labels are the whole backup story — there is no
Postgres cluster behind this and so no WAL archiving.

Same caveat as Campfire: a volume snapshot of a live SQLite database can
capture a torn write. Acceptable for a kanban board; it would not be for
anything transactional.

## Licence

The **O'Saasy License** — permissive like MIT, with one added condition:
you may not offer the software to third parties as a competing hosted or SaaS
product. Self-hosting for your own use is explicitly permitted.
