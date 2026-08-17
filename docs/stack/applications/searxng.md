# SearXNG

[SearXNG](https://github.com/searxng/searxng) is a metasearch engine: it forwards
a query to other engines — Google, DuckDuckGo, Brave, Mojeek, Wikipedia and
roughly two hundred more — strips the tracking, and merges the results. It runs
here as the replacement for a paid Kagi subscription.

**Why SearXNG and not something else**

The self-hosted search field is thinner than it looks. Checked before deploying
(2026-08-17):

| Candidate | State |
|-----------|-------|
| **SearXNG** | Actively developed, commits daily, multi-arch images including arm64 |
| Whoogle | Archived upstream; Google's blocking made a pure Google proxy untenable |
| Stract | Archived; last commit March 2025 |
| LibreY | Alive but small — a few hundred stars and one maintainer |
| YaCy | Alive, but builds its own P2P index; good for an intranet, poor for the open web |

**What it does not replace**: Kagi ranks results with its own index and lets you
promote or demote domains permanently. SearXNG has no index of its own, so
result quality is whatever the upstream engines return, ranked by SearXNG's own
scoring. The closest equivalents are per-engine weights and the `hostnames`
plugin, both configured in `settings.yml`.

**Deployment**:
- Single replica, no database and no persistent volume — the instance holds no
  state beyond a favicon cache in an `emptyDir`
- Runs as UID 977, the `searxng` user baked into the image
- Config lives in a ConfigMap mounted over `/etc/searxng/settings.yml`, using
  `use_default_settings: true` so the upstream engine catalogue is inherited
  rather than copied
- The cookie signing key comes from a SOPS-encrypted secret as
  `$SEARXNG_SECRET`; without it the container generates a fresh one per restart
  and saved preferences reset
- Reloader restarts the pod when the ConfigMap or Secret changes, which a
  `subPath` mount would otherwise never pick up

**Access**:
- **Staging**: https://searxng.staging.ronaldlokers.nl
- **Production**: https://searxng.ronaldlokers.nl

Both hostnames resolve to private addresses, so the instance is reachable from
the LAN only. That is deliberate and it is also why the rate limiter is off: the
limiter exists to keep scrapers off public instances, and enabling it would mean
running Valkey to rate-limit an audience of one. Exposing this publicly would
mean turning the limiter on and accepting that open SearXNG instances attract
scraper traffic — which is what gets an instance CAPTCHA'd by upstream engines.

**Using it as the browser's search engine**

Visit the instance once and use the browser's "add search engine" affordance;
SearXNG serves `/opensearch.xml` for exactly this. Firefox offers it from the
address bar menu, Chromium picks it up automatically after a visit. Homepage's
search box and quicklaunch are already pointed at the production instance.

**When results get worse**

The expected failure mode is an engine starting to answer with CAPTCHAs instead
of results, usually Google, because the requests all come from one home IP.
`/stats` shows per-engine error counts and is the first place to look. The fix
is to lean on the engines that do not fight scrapers — DuckDuckGo, Brave,
Mojeek, Startpage — by disabling the offender in Preferences, or permanently in
`settings.yml`. SearXNG also supports API-keyed engines (Brave Search API among
them) if a paid but cheap backend ever beats fighting the scrapers.
