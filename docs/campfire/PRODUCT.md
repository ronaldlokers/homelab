# Product

<!-- impeccable:product-schema 1 -->

Scope: everything in the **campfire namespace** that posts into a room —
`campfire-nightscout-digest` first, and the house style the other bots
(`campfire-morning-briefing`, `campfire-renovate-digest`, `campfire-status-bot`,
`campfire-alert-bridge`, `campfire-kube-actor`, `campfire-notify-check`) will
inherit. Not a record of the cluster itself; `CLAUDE.md` and the rest of `docs/`
already cover that.

Companion: [DESIGN.md](DESIGN.md) records the visual system built from this.

## Platform

web

## Users

One person: the owner of the cluster and the body being measured. An
experienced developer, fifteen-plus years, who self-hosts everything here and
reads the output on a phone, in the Basecamp Campfire app, in a private
one-person room called **Health**, over coffee, usually within an hour of the
07:00 post. Nobody else is in the room. No clinician sees these images; no
partner or family reads them.

## Product Purpose

Turn the glucose data the cluster already holds into something worth looking at
every morning.

The digest exists because a trend only exists if it keeps arriving. Success is
the reader **noticing a pattern they would otherwise have missed** — the drift,
the streak, the third bad Tuesday in a row — not merely scoring the day just
finished. The day is the newest frame of a running story, and the picture's job
is to place it in that story.

Failure is a picture that gets scrolled past, and a picture that says something
the numbers do not support.

## Positioning

Every CGM product ships a variation of the AGP report: one standardised page,
built so any clinician can read any patient's in seconds. That comparability is
bought with generality.

This surface has exactly one reader, forever. It owes nothing to
cross-patient comparability, to a clinic's workflow, or to a vendor's shared
vocabulary. It can be specific to one person's data, one room, one hour of the
day — the thing a commercial CGM app structurally cannot be.

## Operating Context

- A Kubernetes CronJob runs at 07:00 Europe/Amsterdam and reports the previous
  day. `DIGEST_DATE` re-runs it for any past date.
- Data comes from a self-hosted Nightscout, roughly 288 readings a day, one
  every five minutes. Gaps are normal: sensors end, sensors fail.
- Two things are posted: an HTML message carrying the figures, then a PNG
  attachment. **Campfire caps an attachment on height**, at about 339x400 CSS
  px, which is 1017x1200 device pixels on a 3x phone. Wider-than-tall images
  are shown smaller, not larger.
- The renderer is pure standard-library Python on `python:3.13-alpine`; there
  is no image library and no font library in the pod. Real typefaces are
  rasterised on a workstation and shipped as a generated glyph table.
- Everything is committed to a GitOps repository and reconciled by Flux, so a
  design change is a pull request.

## Capabilities and Constraints

- **Statistics may not be softened.** Average, GMI, spread and the day's
  extremes must appear as exact figures. A redesign that goes vague on numbers
  is a downgrade.
- **Clinical colour meaning is binding.** Green reads in-range, warm reads
  high, red reads low. This is the one piece of the category's vocabulary the
  reader wants kept.
- Time in range is the headline metric; 70% is the consensus target. Bands are
  the international consensus thresholds in mg/dL, converted for display to
  mmol/L.
- Below 70% sensor coverage the statistics are withheld, because a range figure
  from a partial day describes when the sensor was on, not the day.
- **This is not an alarm path and must never become one.** Campfire cannot
  carry CGM alarms — no escalation, no acknowledgement, suppressed delivery
  while the room is open. Nightscout's own alarms exist for that. Everything
  here concerns a day already over.
- History beyond the reported day is **not currently fetched**; showing the day
  in the context of the recent past requires querying a longer window. This is
  a known, unbuilt capability, not an existing one.
- Decoration that encodes nothing is acceptable if it earns its place. Praise
  and judgement in the copy are acceptable to this reader.

## Brand Commitments

None binding. There is no logo, no brand palette, and no house typeface yet;
the reader has explicitly opened all of it — colour, type, icons, marks,
layout, chart form — except the two constraints recorded above.

## Evidence on Hand

- Real Nightscout data for the reader's own days, reachable in-cluster only.
- `apps/base/campfire-nightscout-digest/` — the current renderer, its generated
  glyph table, and the CronJob.
- A phone screenshot establishing Campfire's real attachment size.
- No testimonials, no third-party data, no clinical validation. Nothing here
  may claim medical authority or imply clinical review.

## Product Principles

1. **One reader, forever.** Generality is a cost with no payer here.
2. **The day is a frame, not the film.** Context is the product; a day alone is
   a score.
3. **Numbers stay exact; the picture carries the meaning.** Both, not either.
4. **Nothing urgent lives here.** It reviews; it never alerts.
5. **The room is the frame.** It is designed for the size Campfire actually
   renders, on a phone, once a day.

## Accessibility & Inclusion

Colour must not be the only carrier of band identity: an earlier palette put
`low` and `in range` 2.2 ΔE apart under deuteranopia, which on a chart about
hypoglycaemia is a defect. Adjacent bands differ in lightness as well as hue,
and text meets 4.5:1 against its surface at the size it is actually displayed.
