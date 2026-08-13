# Design

<!-- impeccable:design-schema 1 -->

Scope: the images posted by the **campfire namespace**. Recorded from the built
sheets in `apps/base/campfire-nightscout-digest/chart.py`, not from intentions.

Product truth lives in [PRODUCT.md](PRODUCT.md) beside this file.

## The world

**The Vienna Method** — Otto Neurath and Gerd Arntz's pictorial statistics,
Vienna 1925–34. Its governing idea is that a quantity should be *counted*, not
estimated: repeated unit marks of fixed value, so the length of a run is the
number. That is the opposite of the AGP report every CGM product ships, which
smooths a day into a curve.

It was chosen because the reader's question is "what is my body doing lately",
and a countable row answers it without arithmetic.

## Ground and ink

| Token | Value | Use |
|---|---|---|
| `GROUND` | `#E8E4DA` | the sheet |
| `PAPER` | `#DED9CD` | plot fields inside the sheet |
| `INK` | `#1A1A18` | headline, figures, the day being reported |
| `MUTED` | `#6E6A60` | labels, older days, captions |
| `RULE` | `#C4BEB0` | hairlines under findings and rows |
| `GRID` | `#D2CDC1` | vertical hour rules |
| `TARGET_FILL` | `#CED6C4` | the 3.9–10.0 band |
| `BAND_FILL` | `#C4CCBA` | the middle half of the history |

Light, not dark, and derived from the reading scene rather than from taste:
Campfire's own chrome is light grey, so a dark card punches a hole in the
message column while printed stock sits in it. The neutrals are warm-biased
toward the paper, never a pure grey.

## Clinical colours — binding

Never restyled for visual reasons. Green reads in range, warm reads high, red
reads low, in every sheet and every element.

| Band | Value |
|---|---|
| very low | `#96191E` |
| low | `#D67A7A` |
| in range | `#2E7D4F` |
| high | `#E8A33D` |
| very high | `#D1622A` |

Adjacent bands differ in lightness as well as hue. An earlier palette put `low`
and `in range` 2.2 ΔE apart under deuteranopia; on a chart about hypoglycaemia
that is a defect, not a preference.

## Type

Two faces, baked to bitmaps by `bake_font.py` so the pod needs no font library.

- **URW Gothic** (Demi, Book) — the Avant Garde clone, the closest installed
  face to the geometric sans the Vienna Method's charts were set in. Chosen for
  that lineage. Set lowercase; sentence case only in the headline's data.
- **JetBrains Mono** (Bold, Regular) — every figure, so columns of numbers line
  up as columns of numbers.

| Face | Size | Role |
|---|---|---|
| `headline` | 56 | the finding that leads the sheet |
| `body` | 30 | findings, captions |
| `small` | 26 | row labels, axis labels, source line |
| `stat` | 42 | the four figures on the foot |
| `figure` | 28 | per-row percentages |

Sizes are set from the display box, not from a desktop preview: Campfire caps
an attachment on height at ~339×400 CSS px, so a 1000×1200 canvas renders 1:1
and 30px here is ~10 CSS px on the phone. That is the floor; nothing carrying
meaning goes below it.

## Composition

1000×1200, `MARGIN` 56 on every side including the plot card.

Both sheets share one skeleton: source line, rule, **headline finding**, the
middle, findings, ruled foot with four exact figures.

- **Fortnight sheet** (Saturdays): fourteen counted rows, newest first, hours
  sorted by severity so the green run's length is the time in range. Then the
  typical day — median and middle half across all fourteen.
- **Daily sheet** (every other morning): yesterday's trace drawn over the
  middle half of the preceding days, so a departure is the line leaving the
  band. Then the day as counted hours, keeping both sheets in one language.

## Voice

Findings describe the reader's own data and never instruct. "The 08:00 hour ran
out of range on 11 of 14 days" is a fact; what to do about it is not this
program's business. A day with nothing unusual says so — a sheet that
manufactures a finding every morning teaches the reader to ignore all of them.

## What this refuses

- The AGP one-pager. Its comparability across patients is a cost with no payer
  when there is exactly one reader, forever.
- The wellness score ring, and the pastel gradient it travels with.
- A chronological hour grid. It was built and rejected: it becomes a heatmap,
  where the eye estimates shading instead of counting marks.
- Any advice, encouragement to act, or implied clinical authority.
