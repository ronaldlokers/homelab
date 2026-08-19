# Bringing Squirrel up

Squirrel is an external memory bot: you send it a direct message in Campfire,
it stores the raw text, and answers with a 🐿️. The code lives in
[ronaldlokers/squirrel](https://github.com/ronaldlokers/squirrel); this
repository owns its manifests.

Three of the steps below cannot be done from Git, one of them is a setting no
code can verify, and one is a credential that must never be written down
outside SOPS. That is why this page exists.

## What it needs that nothing else here needs

Squirrel is the first **stateful** workload in the `campfire` namespace. Every
other bot is a stateless report-filer that can be rescheduled freely. Squirrel
writes every incoming message to a spool volume and fsyncs it — file, then the
directory — *before* the room is told it landed. A background loop drains the
spool into Postgres afterwards.

That ordering is the whole design, and it exists because **Campfire does not
retry a failed webhook delivery**. A message dropped at the door is gone. So:

- The Deployment is `replicas: 1` with `strategy: Recreate`. The volume is
  ReadWriteOnce, and two processes draining one directory would race.
- `/healthz` checks that the spool is writable and deliberately does **not**
  check Postgres. A readiness probe that failed during a database outage would
  pull the pod from its Service, Campfire's delivery would fail, and the
  messages sent in that window would be lost — converting a survivable outage
  into permanent data loss.
- The pod runs as uid 65532 with `fsGroup: 65532`. Longhorn hands over a
  root-owned filesystem; without the fsGroup the spool is unwritable,
  `/healthz` answers 503 forever, and the pod never goes ready. It never
  receives a webhook and nothing in the room says so.

## Step 1 — create the bot in Campfire

In Campfire, as an administrator, create a bot user named `squirrel` and set
its webhook URL to:

```
http://squirrel.campfire.svc.cluster.local:8080/transports/campfire
```

Keep the bot key — you need it in [step 4](#step-4--the-bot-key). Until phase 2
this cluster deliberately held no Campfire credential, because every reply
travelled back inside the webhook's own HTTP response. The daily digest is the
first thing that has to *start* a conversation rather than answer one, so the
key arrived with it.

## Step 2 — open a direct message and find the two ids

Start a direct message with `@squirrel`, then read the ids out of Campfire's
database. It lives in SQLite at `/rails/storage/db/production.sqlite3`, and the
image ships no `sqlite3` binary — so copy it out and query it locally. Take the
`-wal` and `-shm` files too, or a room created in the last few minutes will be
missing:

```bash
POD=$(kubectl --context=production get pod -n campfire -l app=campfire \
        -o jsonpath='{.items[0].metadata.name}')
for f in production.sqlite3 production.sqlite3-shm production.sqlite3-wal; do
  kubectl --context=production cp -c campfire \
    "campfire/$POD:/rails/storage/db/$f" "/tmp/$f"
done

sqlite3 -header /tmp/production.sqlite3 "
  select r.id as conversation_id, r.type, u.id as sender_id, u.name
  from rooms r
  join memberships m on m.room_id = r.id
  join users u on u.id = m.user_id
  where r.type = 'Rooms::Direct'
    and r.id in (select room_id from memberships m2
                 join users u2 on u2.id = m2.user_id
                 where u2.role = 'bot' and trim(lower(u2.name)) = 'squirrel')
  order by r.id, u.id;"

shred -u /tmp/production.sqlite3*
```

**Match on `rooms.type`, not on the room's name.** There is a `Rooms::Closed`
room also called "Squirrel" with the same two members; it is indistinguishable
from the direct one by name, and choosing it is the mistake this page exists to
prevent.

Also note the bot's name is `Squirrel ` — capital S, trailing space — which is
why the query trims and lowercases. An exact match on `"squirrel"` finds
nothing and looks like "no direct room exists yet".

Put the non-bot user's id in `CAMPFIRE_SENDER_ID` and the room's id in
`CAMPFIRE_CONVERSATION_ID` in
`apps/production/squirrel/deployment-postgres-patch.yaml`, and commit. Flux
applies within about a minute.

### The obligation no code can check

`CAMPFIRE_CONVERSATION_ID` **must name a direct room.**

The webhook payload carries no room-type field. Campfire models direct rooms as
a distinct subclass with a `direct?` predicate, but never serialises it, so
Squirrel cannot verify what kind of room a message came from and does not
pretend to. Point this at a group room and it will capture from it happily.

What actually holds the line is this setting, plus `CAMPFIRE_SENDER_ID`, plus
the fact that outside direct rooms Campfire only fires the webhook on an
explicit `@squirrel` mention. Anything from another conversation is answered
with total silence and logged at warn with its conversation id.

## Step 3 — revoke PUBLIC's CONNECT

**This does not happen by itself, and it was missed the first time.**

CloudNativePG's `Database` CR creates the database and sets its owner. It does
not revoke `PUBLIC`'s implicit `CONNECT`, and there is no declarative field for
it — the same reason the `bots` role's SELECT grants are applied by hand. A new
database therefore arrives on PostgreSQL's defaults, with every role in the
cluster able to connect to it. That is the state #219 removed everywhere else.

```bash
P=$(kubectl --context=production get pod -n database \
      -l cnpg.io/cluster=postgres-cluster,role=primary \
      -o jsonpath='{.items[0].metadata.name}')

kubectl --context=production exec -n database "$P" -c postgres -- \
  psql -U postgres -c "REVOKE CONNECT ON DATABASE squirrel FROM PUBLIC;"
```

No matching `GRANT` is needed: the owner holds `CONNECT` implicitly, which is
also why revoking from `PUBLIC` does not lock Squirrel out of its own database.

Verify against a database that already has it — the ACLs should be identical in
shape, `=T/<owner>` meaning PUBLIC keeps TEMP and loses CONNECT:

```bash
kubectl --context=production exec -n database "$P" -c postgres -- psql -U postgres -t -c \
  "select datname, array_to_string(datacl,' | ') from pg_database
   where datname in ('squirrel','linkding');"
```

`PostgreSQLDatabaseNotIsolated` fires within a few minutes if this is skipped,
which is how it was caught. Read
[revoke-that-only-broke-things-four-hours-later.md](../war-stories/revoke-that-only-broke-things-four-hours-later.md)
before revoking on any database other than an app's own — on `postgres` this
same command once removed `streaming_replica`'s only grant and broke replica
rejoin for four hours with every signal green.

## Step 4 — the bot key

**The one credential in this namespace.** It can post as `@squirrel` into any
room the bot can reach, and it is the same value that appears in the webhook
URL Campfire calls — which is why an inbound payload's `room.path` is treated
as secret too, and why the code strips URLs out of every outbound error before
logging it.

It lives in one SOPS file. Put the real key in:

```bash
sops apps/production/squirrel/squirrel-campfire-secret.yaml
```

Replace `REPLACE_ME_WITH_THE_REAL_BOT_KEY` under `stringData` and save. Never
write it anywhere else — not a plaintext manifest, not a commit message, not a
shell command that lands in history.

It pairs with `CAMPFIRE_BASE_URL` in `apps/base/squirrel/deployment.yaml`, and
the two fail asymmetrically. **A key with no URL is rejected at boot, loudly. A
URL with no key is not:** `Send` stays nil, the pod goes ready, captures keep
landing, and every reply, every digest and every 🐿️ receipt silently stops —
the receipt too, because the boost is built from that same base URL. If the
room goes quiet but `items` is still filling, check this first.

`reloader.stakater.com/auto` is set on the Deployment, so a rotation rolls the
pod. Without it the rotation would apply cleanly and the pod would keep using
the old key, which is the same quiet failure.

To rotate: change it in Campfire's bot admin, run the `sops` command above,
commit, and let Flux apply it. The webhook URL does not change.

## The forked Campfire

**Buttons need a fork; everything else does not.** From v0.3.0 Squirrel puts a
button on each due chore in the digest, and a correction button on a chore it
just defined. Interactive bot actions and `PATCH` on a bot's own message are in
`ronaldlokers/once-campfire@feat/bot-message-actions` and not upstream.

**Squirrel runs fine without it, on purpose.** A message carrying buttons is
sent as JSON; an upstream Campfire rejects that with a 4xx and the transport
retries the same text as plain text, logging:

```
campfire: message with actions was rejected, retrying as plain text
```

Seeing that line every morning is the expected signal that Campfire is upstream.
It is not a fault to chase. What you lose is the buttons — every number still
works, so `done 2`, a bare `2`, `stop 3`, `?` and `nvm` are untouched, and both
halves of the receipt still land.

**The standing cost is rebasing.** Upstream ships security fixes and each one
now arrives through the fork. Check `ronaldlokers/once-campfire` against upstream
`main` whenever a `security/*` branch appears there.

**Rolling back** is repointing `apps/production/campfire/kustomization.yaml` at
`ghcr.io/basecamp/once-campfire` and its digest. Squirrel needs no change and no
rollback of its own — it degrades as described above. Taps simply stop arriving,
so nothing resolves against a prompt whose buttons no longer render.

## The presence webhook

**New in v0.4.0, and the only path on this pod reachable from outside the
namespace.** Home Assistant calls it when I get home. Squirrel answers 204
immediately, then waits a couple of minutes before nudging — you have a coat
on, and the same window debounces the several pings a phone flapping between
wifi and cellular will produce.

It is the one inbound route that **deliberately does not go through the
spool**. Everything else inbound is written to disk and fsynced before it is
acknowledged, because losing it means losing a thought. A presence ping is not
a thought: losing one costs a nudge, and 19:00 catches the same day. Spooling
it would also put "you came home" in the capture list.

**Home Assistant runs off-cluster, on the LAN.** That is why this needs an
Ingress at all, where the Campfire webhook needs none. Two things keep it
narrow, and both have to hold:

- `apps/production/squirrel/ingress.yaml` routes **exactly** `/hooks/home`,
  `pathType: Exact`. The Campfire webhook and `/healthz` stay unreachable from
  outside the namespace. Widening this to `/` would hand the Campfire webhook —
  which has no authentication whatsoever — to every host on the LAN.
- `kube-system-local-network-only@kubernetescrd`, the same ipAllowList
  middleware that guards Longhorn, pgAdmin and the campfire-alert-bridge. It
  works because `*.ronaldlokers.nl` resolves to the MetalLB VIP `10.0.40.100` —
  a private address — and Traefik runs `externalTrafficPolicy: Local`, so the
  client's real source IP survives the hop. Only `ntfy` is proxied through
  Cloudflare; nothing else here is reachable from the internet.

No NetworkPolicy was added for this. `allow-ingress-from-traefik` in
`apps/base/campfire/network-policies.yaml` already selects every pod in the
namespace with no port restriction, so it covers Squirrel today. Worth knowing
before going to look for a rule that is not there.

### The secret

`apps/production/squirrel/squirrel-presence-secret.yaml`, generated at random
and never typed anywhere. Read it back to configure Home Assistant:

```bash
sops -d apps/production/squirrel/squirrel-presence-secret.yaml
```

It is kept apart from `squirrel-campfire` on purpose. Different blast radii:
the bot key posts as `@squirrel` into any room it can reach, this one can make
the bot nudge me about a chore. Rotating one should not roll the other.

**A missing secret is safe; an empty one would not be.**
`subtle.ConstantTimeCompare("", "")` returns 1, so an unset secret would
authenticate every caller, including one sending no header at all — which is
why the binary refuses to mount the route rather than mounting it wide open,
and logs a warning saying so. The Deployment reads `PRESENCE_SECRET` with
`optional: true`, so losing the Secret costs the presence trigger and nothing
else. Captures, the nudge that rides back on a message, and 19:00 all carry on.

### The Home Assistant automation

A REST call on arrival, with the secret in the header:

```yaml
rest_command:
  squirrel_presence:
    url: https://squirrel.ronaldlokers.nl/hooks/home
    method: POST
    headers:
      X-Squirrel-Token: !secret squirrel_presence_token

automation:
  - alias: Tell Squirrel I am home
    trigger:
      - platform: state
        entity_id: person.ronald
        to: home
    action:
      - service: rest_command.squirrel_presence
```

**If this breaks, nothing tells you.** The trigger simply stops and everything
still works, because 19:00 is the floor. Good degradation, bad observability —
the answer is to surface "last presence ping" wherever liveness eventually
lives, not to try to detect it here. Until then, an occasional manual check:

```bash
# 204 with the right token, 403 without it, 404 if the secret never mounted
curl -si -X POST https://squirrel.ronaldlokers.nl/hooks/home \
  -H "X-Squirrel-Token: $(sops -d apps/production/squirrel/squirrel-presence-secret.yaml \
    | grep PRESENCE_SECRET | cut -d' ' -f2)" | head -1
```

That fires a real nudge if one is still owed today, so expect a message.

## When the message arrives

`EVENING_AT` is set explicitly to `19:00` in
`apps/base/squirrel/deployment.yaml`, and it is load-bearing twice: it is the
clock trigger that catches a day nothing else did, and it is the slot the
capture list is sent in. On a quiet day the two share **one** message rather
than arriving a second apart. Moving it moves both.

The timezone beside it is still called `DIGEST_TZ`, from the phase 2 digest
that no longer exists — v0.4.0 renamed `DIGEST_AT` to `EVENING_AT` and left its
neighbour alone. Nothing outside the binary reads it, so the rename is free
whenever that file is next opened.

**Rolling back to v0.3.1** is repointing `newTag` and letting Flux apply it.
The Ingress and the presence Secret can stay: v0.3.1 ignores `PRESENCE_*`
entirely, so the route 404s and Home Assistant's call fails silently — the same
failure mode as the automation being switched off. What you lose is the nudge
and the "what you did" section. The digest returns at whatever `DIGEST_AT`
says, which is **08:00 by default**: v0.3.1 does not know the name
`EVENING_AT`, so the 19:00 set here stops applying. Set `DIGEST_AT` explicitly
if a morning digest is not what you want.

## The pile

**New in v0.5.0.** Notes stopped being write-only: they have a lifecycle now,
and a way to be found. Typing a thought is unchanged and still the default.

```
!notes                    the pile, newest first
!find boiler              search everything, across every state
!chores                   what is due (same as ?)
!chore 1 every 2 weeks    turn note 1 into a recurring chore
!help                     the vocabulary

done 1 · keep 1 · drop 1  clear line 1
```

**The one thing that feels different in the room:** `done <n>` now means
whichever kind of thing line n was. List the pile and type `done 1` and you
clear a note, not a chore. That is the design — a numbered surface owns its own
numbering — but it is the change most likely to surprise you.

`keep` is the state worth knowing about. A serial number or a link is not a
task and will never be `done`; `keep` takes it out of the pile while leaving it
findable by `!find`. Without it, reference notes would sit in triage forever.

**Commands are not notes.** The drain stores every inbound message, so `!notes`
and `?` land in `items` like anything else — they are filtered out of both the
pile and the evening list by the same test, so neither surface shows them.

**Nothing counts the pile.** No badge, no total, no "N to review". A capped
list says there is more, never how much. That is deliberate and load-bearing:
a number growing beside an implied zero is the accumulating mechanism this
project bans everywhere else.

### Two migrations

v0.5.0 applies `0008_item_state.sql` and `0009_prompt_line_items.sql` at boot,
before serving. Both are additive:

- 0008 adds `items.state` (defaulting every existing row to `open`, which is
  true — nothing had ever been triaged) and `items.state_at`.
- 0009 makes `prompt_lines.chore_id` nullable and adds `item_id`, so a numbered
  line can name a note. A check constraint requires exactly one of the two.

**Rolling back to v0.4.1 is safe and needs no schema change.** The columns stay;
v0.4.1 does not read them. What you lose is the pile: `!notes` and the rest
become unknown text and are filed as notes, which is the correct failure — a
command it does not understand is still a thought it keeps. The one wrinkle is
that any `prompt_lines` row written by v0.5.0 pointing at an item has a null
`chore_id`, and v0.4.1's `ChoreAtPosition` joins `chores` on it — the join
simply finds nothing, so a stale `done 1` says it has no such line rather than
resolving to the wrong chore.

## The screen

**New in v0.6.0.** The pile has a web screen at
<https://squirrel.ronaldlokers.nl/pile>: one note at a time, the four
transitions, undo, and search across every state. It reads and triages, and it
will never capture — there is no box to type a thought into and no route that
creates one, permanently. Two capture surfaces means two places to look for a
thought, which is the problem this bot exists to solve.

Everything it does is an ordinary form POST answered with a 303, so it works
with JavaScript switched off. The stamp, the moment the card holds still, and
one key per action (`d` done, `k` keep, `x` drop, `c` chore, `/` search) are
layered on top of that.

### What authenticates it

Squirrel writes no authentication code and holds no session. Four things have
to line up:

1. `WEB_IDENTITY: ronald` in `apps/base/squirrel/deployment.yaml`. Empty leaves
   the screen **unmounted** — `/pile` is a plain 404, not an open route — and
   logs `no web identity configured; the pile screen is not mounted`.
2. The `authentik-forward-auth` Middleware in
   `infrastructure/configs/production/`, which calls authentik's *embedded*
   outpost and copies `X-Authentik-Username` onto the request.
3. `apps/production/squirrel/ingress-pile.yaml`, the screen's own router:
   `https-redirect → lan-or-tailnet → authentik-forward-auth`, in that order.
   The address check stays the outer layer.
4. `apps/production/squirrel/ingress-outpost.yaml`, which routes
   `/outpost.goauthentik.io/` **on squirrel's hostname** to `authentik-server`.
   Without it the login redirect has nowhere to come back to.

The provider itself is declared in the `squirrel.yaml` key of the
`authentik-blueprints` Secret: a proxy provider in `forward_single` mode, plus
the application, plus the embedded outpost being told to serve it. That last
entry replaces the outpost's provider list rather than appending to it — a
second proxy provider must be added to the same entry or it will remove this
one.

The identity comparison is exact: no trimming, no case folding. It matches the
authentik **username** — `ronaldlokers` — not the e-mail, not the display name,
and not `OWNER_HANDLE`, which is the Campfire handle and is a different word.

### Reaching it from the tailnet

`lan-or-tailnet` is why the screen works from a phone that is not at home, and
it is wider than the `local-network-only` everything else here uses. The reason
it allows the *pod* network rather than 100.64.0.0/10 is that the subnet router
SNATs: by the time Traefik sees a tailnet request its source is the router
pod's IP. Reproduce the failure without a phone by asking from inside that pod:

```
kubectl --context=production -n tailscale exec ts-homelab-subnet-router-<id>-0 \
  -- wget -q -S -O /dev/null https://squirrel.ronaldlokers.nl/pile
```

403 means the router carries `local-network-only`; a 302 to authentik is right.

Both the screen and its outpost callback must carry the same rule. One on each
would let a tailnet device reach the pile and then fail on the way back from
the login, which reads as authentik being broken.

### When the screen is wrong

- **404 on /pile** — `WEB_IDENTITY` is empty, or the pod predates v0.6.0. The
  routes are never registered, so there is nothing to authenticate.
- **A blank white page after logging in successfully** — this one. The request
  reached squirrel with a username that is not `WEB_IDENTITY`, and squirrel
  answers 403 with no body on purpose, so a browser renders nothing at all.
  `kubectl logs` shows `refused the pile` with the identity it saw. That log
  line is the fastest way to the right value.
- **403 before any login page appears** — the address check, not the identity.
  From the tailnet, see above; from anywhere else, that is the rule working.
- **503 saying it cannot reach its memory** — Postgres is down, or the pod has
  not finished its first connection. The routes go live at `Listen`; the owner
  is only known once the database answers, and the screen says so rather than
  pretending. Captures are unaffected — that is the whole point of the spool.
- **Redirect loop, or a 404 on /outpost.goauthentik.io/...** — the outpost
  Ingress is missing, or the blueprint did not apply. Check authentik's logs
  for a blueprint error, and that the embedded outpost lists the provider.
- **The login redirect points at `http://localhost/`** — the embedded outpost's
  `authentik_host` is empty. It has no way to learn its own public hostname
  (the chart sets no `AUTHENTIK_HOST`, and the request it answers is Traefik's,
  not the browser's), so the blueprint states it. Confirm with:

  ```
  curl -s -H "Authorization: Bearer $TOKEN" \
    https://authentik.ronaldlokers.nl/api/v3/outposts/instances/ | jq '.results[].config'
  ```
- **403 on a form submission that worked a moment ago** — squirrel refuses a
  write whose `Origin` does not match its own host. If a middleware ever
  rewrites `Host`, every button on the screen breaks this way and the log says
  `refused a cross-site write`.

### The chores screen

**New in v0.8.0.** `/pile/chores` shows what comes back: what each chore is,
how often, and when it was last done, with `DID IT`, `HOW OFTEN` and `STOP
ASKING`. Before this a chore was invisible unless it nudged you — the one
moment you are least able to decide you never want it again.

`STOP ASKING` and chat's `!retire` are the same write, and changing the
interval is the same upsert-by-name the chat command makes, so the two surfaces
cannot drift apart. `!undo` in chat reverses the last note triaged on either
surface.

Nothing here says how many chores there are, how many are due, or how late
anything is. `3 days ago` is a fact about a chore; `2 days overdue` would be a
fact about you.

### The keyboard, and the phone

**New in v0.7.0.** `d` `k` `x` are the three ways out, `c` then `1`-`4` makes a
chore (`ESC` withdraws the question), `/` is search and `ESC` clears it.

`space` and the arrows move past a note without doing anything to it. That is
skipping, and it is a cursor in the address bar (`?after=<id>`) rather than a
state: a skipped note is untouched, still open, and still first the next time
the pile is opened from the top. Reloading `/pile` is how you get back to the
top. Running out of notes below the cursor is its own page — what you skipped
is still in the pile, so it does not say the pile is empty.

On a phone there is no space bar, so the same action is a `LATER` link in the
card's titlebar. It is a plain link on purpose: it needs neither a key nor
JavaScript, and the key presses it rather than knowing where it points.

Search answers as you type, by fetching the same URL the form submits to. With
JavaScript off the identical page arrives by pressing Enter — one renderer,
one code path.

### The staging pile

`squirrel.staging.ronaldlokers.nl/pile` runs the same binary with **no
transports**: no Campfire webhook, no bot key, no presence route — only the
screen, over a database somebody seeded on purpose. It exists so a change to
the screen can be looked at somewhere that is not the place every thought you
have ever had is kept.

It has **no authentication**. Staging has no Authentik outpost in front of it,
and the pile refuses to render without an identity, so a Traefik middleware
puts one there — `staging-identity` overwrites `X-Authentik-Username` with the
owner's name. That is a deliberate lie and it is why nothing real should ever be
seeded there.

Seed it the way any other staging database is seeded:

```
kubectl --context=staging -n database exec -it postgres-cluster-1 -- \
  psql -d squirrel -c "insert into items (transport, external_id, conversation_id, sender_id, person_id, raw_text, payload, received_at) select 'seed', 'x1', '7', '1', id, 'a note to look at', '{}', now() from people limit 1;"
```

### After changing an asset

Nothing, since v0.7.1. Asset URLs carry `?v=<stamp>`, hashed from the embedded
files at startup, so a changed file is a changed URL and the year-long cache
repaints itself.

Before that they did not, and v0.7.0 arrived broken in a browser that had seen
v0.6.0: HTML is served `no-store`, so the new markup rendered against the old
stylesheet and script — a link with no styling and a button with no handler. If
a screen ever looks half-updated again, that is the shape of it, and a hard
reload (**Ctrl-Shift-R**) is the test that proves it.

### Rolling back to v0.5.0

Repoint `newTag` and let Flux apply it. `WEB_IDENTITY` is ignored by v0.5.0, so
the screen's routes simply stop existing and `/pile` 404s behind a working
forward-auth. Nothing else changes: the chat commands, the evening message and
the presence nudge are untouched, and there is no schema change to undo — the
screen reads the same tables `!notes` does.

The Ingresses, the middleware and the blueprint can all stay. If you want them
gone as well, remove `ingress-pile.yaml` and `ingress-outpost.yaml` first, then
the middleware — an Ingress naming a middleware that no longer exists fails the
router rather than the request, which takes the presence webhook down with it.

## Step 5 — confirm it is working

```bash
# Ready, and the spool is writable
kubectl --context=production get pod -n campfire -l app=squirrel

# The boot sequence: listening comes before the database, on purpose
kubectl --context=production logs -n campfire -l app=squirrel | head -20
```

Expect `http.listening`, then `transport.started`, then `db.ready`. If the
database is unreachable the first two still appear and captures still land —
that is the design working, not a failure.

Then send it a message. You should get a 🐿️ back within a second, and:

```bash
kubectl --context=production exec -n database postgres-cluster-1 -- \
  psql -U postgres -d squirrel -c 'select id, raw_text, received_at from items order by id desc limit 5;'
```

## When something is wrong

**No 🐿️, and the room says "Failed to respond within 7 seconds."** The webhook
never arrived or never returned. Check the NetworkPolicy pair — the hop needs
saying twice, ingress on Squirrel and egress on Campfire, because the namespace
default-denies both directions. `allow-campfire-to-squirrel` and
`allow-campfire-egress-to-squirrel`.

**The room gets "⚠️ couldn't save that — please resend."** The spool write
failed. Almost always the volume: check `fsGroup`, check the PVC is bound, check
it is not full. This is the one message that means a thought was genuinely lost,
so it is worth chasing.

**A file appears in the room instead of a message.** A non-200 response carrying
a `Content-Type` is uploaded by Campfire as an attachment. Squirrel answers 200
for everything including failures precisely to avoid this, so a file means
something upstream of the handler answered — usually the wrong URL configured on
the bot, hitting a route that does not exist.

**Everything looks healthy but nothing reaches Postgres.** The drain defers
rather than errors, so captures keep landing on disk and the room keeps getting
its squirrel. Check `allow-squirrel-egress` and `allow-squirrel-ingress` (the
latter lives in the `database` namespace), and look for `db.unavailable` in the
logs. The spool directory is your backlog; nothing is lost, but it will not
drain itself if the policy is missing.

**Captures land, but the room has gone quiet — no receipts, no digest.**
Nothing is lost; `items` is still filling. This is the outbound half failing on
its own, and it has three causes worth checking in order. The bot key, missing
or wrong — see [step 4](#step-4--the-bot-key), and note that a URL with no key
boots perfectly happily. The NetworkPolicy pair for the hop *back* to Campfire,
`allow-squirrel-egress` and `allow-squirrel-to-campfire`, which is newer than
the inbound pair and easier to forget. Or Campfire itself being down, in which
case the log carries `campfire: send failed` with the URL stripped out.

**A digest never arrived, and the log says nothing.** If nothing was due and
nothing was captured, that is correct — a daily "nothing to report" is exactly
what the design refuses to send. Otherwise check the pod's clock and
`DIGEST_TZ`: the send is a wall-clock time in Europe/Amsterdam, and a day the
process slept through is skipped rather than sent late.

**The pod will not start after a node change.** ReadWriteOnce plus `Recreate`
means the old pod must fully terminate before the new one attaches. If it is
stuck on multi-attach, see
[deployment-recreate-strategy-stuck-rollout.md](deployment-recreate-strategy-stuck-rollout.md).

## What is in the spool

In steady state, nothing — files exist only between arriving and draining,
normally under a second. Two things persist:

- The directory grows during a Postgres outage and drains when it returns.
- `quarantine/` holds captures that could not be inserted for a reason retrying
  will not fix. Those are never deleted and are irreplaceable, which is why the
  PVC carries the daily Longhorn backup labels rather than being treated as
  scratch.

A file in `quarantine/` is worth reading. It is a capture the system could not
store and could not honestly discard.
