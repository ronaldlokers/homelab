#!/usr/bin/env python3
"""Draw a fortnight of glucose as a PNG, with no dependencies at all.

DIRECTION CONTRACT
THESIS: A morning report that leads with what the fortnight noticed, not with
yesterday's score. It refuses the AGP one-pager, whose comparability across
patients is a cost with no payer when there is exactly one reader.
OWN-WORLD: The Vienna Method — Neurath and Arntz's pictorial statistics.
Printed off-white stock, flat offset inks, a hard modular grid, geometric sans
set lowercase, and quantities built from countable unit marks rather than
smoothed into curves.
STORY: The reader learns one true thing about the last two weeks in a sentence,
sees the fourteen days that produced it, and leaves with the exact figures.
FIRST VIEWPORT: A source line, a rule, then the finding set at 56px across two
lines. Below it fourteen rows, newest first, each a run of marks whose length
is that day's time in range. Then the typical day, then the remaining findings,
then the figures on a ruled foot.
FORM: Counted rows plus a headline finding; candidate 6 of the grounded list,
seed key f091d619.
FINISH: unreviewed and undocumented is unfinished; this build ends with the
finish review, the verdict, and DESIGN.md.

matplotlib would be the obvious tool and is the wrong one here: it would mean
building a wheel on arm64 or carrying a much larger image, for a sheet whose
whole content is rectangles, a line and some text. `zlib` and `struct` are in
the standard library, so the CronJob keeps running on a stock
`python:3.13-alpine` with nothing installed.

The type is real type and still costs no dependency: `bake_font.py` rasterises
URW Gothic and JetBrains Mono on a workstation, and `glyphs.py` ships the
coverage bitmaps.

Two constraints are load-bearing and neither is negotiable. Clinical colour
meaning is binding — green reads in range, warm reads high, red reads low — and
the figures stay exact. Everything else on the sheet was chosen.
"""

import math
import statistics
import struct
import zlib

import glyphs

# --- palette ---------------------------------------------------------------
#
# Printed stock, not a screen. Campfire's own chrome is light, so a dark card
# punches a hole in the message column; a printed ground sits in it. The inks
# are flat and few, the way a statistical sheet is printed.

GROUND = (232, 228, 218)
PAPER = (222, 217, 205)
INK = (26, 26, 24)
MUTED = (110, 106, 96)
RULE = (196, 190, 176)
GRID = (210, 205, 193)
TARGET_FILL = (206, 214, 196)
BAND_FILL = (196, 204, 186)

# Reds below, green in range, ambers above — the one piece of the category's
# vocabulary worth keeping. Stepped so neighbouring bands differ in lightness
# as well as hue, because an earlier palette put `low` and `in range` 2.2 ΔE
# apart under deuteranopia, which on a chart about hypoglycaemia is a defect.
COLOURS = {
    "very low": (150, 25, 30),
    "low": (214, 122, 122),
    "in range": (46, 125, 79),
    "high": (232, 163, 61),
    "very high": (209, 98, 42),
}
# Worst first, so a row reads outward from the trouble.
SEVERITY = ("in range", "high", "very high", "low", "very low")

# --- layout ----------------------------------------------------------------
#
# Campfire caps an attachment on height, at about 339x400 CSS px, which is
# 1017x1200 device pixels on a 3x phone. 1000x1200 fills that box at 1:1, so a
# pixel drawn is a pixel shown — and 30px of type here is about 10 CSS px on
# the phone, which is the floor. Everything is sized from that, not from how it
# looks on a desktop.

WIDTH, HEIGHT = 1000, 1200
MARGIN = 56
SOURCE_BASELINE = 64
HEADLINE = (150, 64)  # first baseline, line step
ROWS = (250, 30, 22, 6)  # top, row height, mark, gap
ROW_LABEL_X, ROW_MARKS_X = MARGIN, 176
TYPICAL = (712, 730, 880)  # caption baseline, box top, box bottom
FINDINGS = 918
FINDING_STEP = 70
FOOT = 1060

# The daily sheet spends its middle on one chart instead of fourteen rows, so
# the trace gets the room the rows had.
AGAINST = (330, 350, 780)
DAY_MARKS = 850
DAY_FINDINGS = 920

MMOL = 18.0182
Y_MIN, Y_MAX = 40, 14.0 * MMOL
Y_EDGES = (70, 180)
# The consensus target. The findings hang off it rather than off a number
# invented here.
TARGET = 0.70

SOURCE = "glucose, fourteen days  ·  nightscout"
DAY_SOURCE = "glucose  ·  nightscout"


class Canvas:
    def __init__(self, width, height, fill):
        self.width, self.height = width, height
        self.buf = bytearray(bytes(fill) * (width * height))

    def blend(self, x, y, colour, alpha=1.0):
        if alpha <= 0 or not (0 <= x < self.width and 0 <= y < self.height):
            return
        i = (y * self.width + x) * 3
        if alpha >= 1:
            self.buf[i : i + 3] = bytes(colour)
            return
        for channel in range(3):
            was = self.buf[i + channel]
            self.buf[i + channel] = int(was + (colour[channel] - was) * alpha + 0.5)

    def rect(self, left, top, right, bottom, colour, alpha=1.0):
        left, right = max(int(left), 0), min(int(right), self.width)
        if right <= left:
            return
        if alpha >= 1:
            row = bytes(colour) * (right - left)
            for y in range(max(int(top), 0), min(int(bottom), self.height)):
                start = (y * self.width + left) * 3
                self.buf[start : start + len(row)] = row
            return
        for y in range(max(int(top), 0), min(int(bottom), self.height)):
            for x in range(left, right):
                self.blend(x, y, colour, alpha)

    def segment(self, x0, y0, x1, y1, colour, thickness):
        """Anti-aliased line, by distance from the segment."""
        half = thickness / 2
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy or 1.0
        for y in range(int(min(y0, y1) - half - 1), int(max(y0, y1) + half + 2)):
            for x in range(int(min(x0, x1) - half - 1), int(max(x0, x1) + half + 2)):
                t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length_sq))
                distance = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
                self.blend(x, y, colour, min(1.0, max(0.0, half + 0.5 - distance)))

    def png(self):
        """Adaptive per-row filtering, which is most of the file size."""
        stride = self.width * 3
        raw = bytearray()
        previous = bytes(stride)
        for y in range(self.height):
            row = bytes(self.buf[y * stride : (y + 1) * stride])
            best = None
            for kind in range(5):
                candidate = _filter_row(kind, row, previous, 3)
                score = sum(b if b < 128 else 256 - b for b in candidate)
                if best is None or score < best[0]:
                    best = (score, kind, candidate)
            raw.append(best[1])
            raw += best[2]
            previous = row

        def chunk(tag, data):
            body = tag + data
            return (
                struct.pack(">I", len(data))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0),
            )
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def _filter_row(kind, row, previous, bpp):
    if kind == 0:
        return row
    out = bytearray(len(row))
    for i, value in enumerate(row):
        left = row[i - bpp] if i >= bpp else 0
        up = previous[i]
        upper_left = previous[i - bpp] if i >= bpp else 0
        if kind == 1:
            out[i] = (value - left) & 0xFF
        elif kind == 2:
            out[i] = (value - up) & 0xFF
        elif kind == 3:
            out[i] = (value - (left + up) // 2) & 0xFF
        else:
            estimate = left + up - upper_left
            a, b, c = (
                abs(estimate - left),
                abs(estimate - up),
                abs(estimate - upper_left),
            )
            nearest = left if a <= b and a <= c else (up if b <= c else upper_left)
            out[i] = (value - nearest) & 0xFF
    return bytes(out)


class Type:
    """Draws the baked glyphs. Coverage byte per pixel, blended as alpha."""

    def __init__(self):
        self.faces = glyphs.INDEX
        self.starts = {}
        for name, face in self.faces.items():
            cursor = face["offset"]
            for character in face["order"]:
                width, height = face["glyphs"][character][:2]
                self.starts[(name, character)] = cursor
                cursor += width * height

    def width(self, face, string):
        table = self.faces[face]["glyphs"]
        return sum(table[c][4] for c in string if c in table)

    def draw(self, canvas, x, baseline, face, string, colour, alpha=1.0):
        table = self.faces[face]["glyphs"]
        pen, baseline = x, int(baseline)
        for character in string:
            glyph = table.get(character)
            if glyph is None:
                # An unforeseen character advances a space rather than raising,
                # so a new label degrades to a gap instead of failing at 07:00.
                pen += table.get(" ", [0, 0, 0, 0, 8])[4]
                continue
            width, height, dx, dy, advance = glyph
            start = self.starts[(face, character)]
            for row in range(height):
                base = start + row * width
                for column in range(width):
                    coverage = glyphs.BITMAPS[base + column]
                    if coverage:
                        canvas.blend(
                            int(pen) + dx + column,
                            baseline + dy + row,
                            colour,
                            alpha * coverage / 255,
                        )
            pen += advance

    def wrap(self, face, string, width):
        words, line, lines = string.split(), "", []
        for word in words:
            trial = f"{line} {word}".strip()
            if line and self.width(face, trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        return lines


# --- the data, described ---------------------------------------------------


def band_of(value, bands):
    for name, low, high in bands:
        if low <= value <= high:
            return name
    return "very high"


def shares(values, bands):
    return [
        (name, sum(1 for v in values if low <= v <= high) / len(values))
        for name, low, high in bands
    ]


def time_in_range(values):
    return sum(1 for v in values if 70 <= v <= 180) / len(values)


def hours_by_band(readings, bands):
    """Twenty-four hours, each labelled by the band it mostly sat in."""
    out = []
    for hour in range(24):
        window = [v for m, v in readings if hour * 60 <= m < (hour + 1) * 60]
        if not window:
            out.append(None)
            continue
        counts = {}
        for value in window:
            name = band_of(value, bands)
            counts[name] = counts.get(name, 0) + 1
        out.append(max(counts, key=counts.get))
    return out


def findings(days, bands):
    """What the fortnight says about itself, most consequential first.

    These are descriptions of the reader's own data and nothing else. They name
    what happened and how often; they never say what to do about it. This is a
    review of a day already over, posted into a chat room — advice about
    insulin or food is not the job, and a digest that gives it is a medical
    device wearing a chat message.

    Ordered so the sheet's headline is the most consequential *true* thing, not
    merely the first thing computed: a night below range outranks a habitual
    bad hour, which outranks a week-on-week move, which outranks a streak.
    """
    out = []
    labelled = [(label, [v for _, v in readings]) for label, readings in days]

    lows = [label for label, values in labelled if min(values) < 70]
    if lows:
        out.append(
            (
                "below 3.9",
                f"on {len(lows)} of {len(days)} days, most recently {lows[-1]}",
            )
        )

    worst_hour, worst_count = None, 0
    for hour in range(24):
        count = sum(
            1
            for _, readings in days
            if (hours_by_band(readings, bands)[hour] or "in range") != "in range"
        )
        if count > worst_count:
            worst_hour, worst_count = hour, count
    if worst_count >= max(3, len(days) // 3):
        out.append(
            (
                f"{worst_hour:02d}:00",
                f"out of range on {worst_count} of {len(days)} days, "
                "more than any other hour",
            )
        )

    if len(days) >= 8:
        half = len(days) // 2
        recent = statistics.fmean(time_in_range(v) for _, v in labelled[half:])
        earlier = statistics.fmean(time_in_range(v) for _, v in labelled[:half])
        change = (recent - earlier) * 100
        if abs(change) >= 3:
            out.append(
                (
                    "this week",
                    f"{recent * 100:.0f}% in range, "
                    f"{'up' if change > 0 else 'down'} {abs(change):.0f} points "
                    "on the week before",
                )
            )

    night = [v for _, readings in days for m, v in readings if m < 360]
    daytime = [v for _, readings in days for m, v in readings if m >= 360]
    if night and daytime:
        out.append(
            (
                "nights",
                f"{time_in_range(night) * 100:.0f}% in range, against "
                f"{time_in_range(daytime) * 100:.0f}% by day",
            )
        )

    streak = 0
    for _, values in reversed(labelled):
        if time_in_range(values) >= TARGET:
            streak += 1
        else:
            break
    if streak >= 2:
        out.append(
            ("streak", f"{streak} days running at or above the {TARGET:.0%} target")
        )

    # A fortnight with nothing notable in it still gets a sentence.
    if not out:
        overall = time_in_range([v for _, values in labelled for v in values])
        out.append(("steady", f"{overall * 100:.0f}% in range across the fortnight"))
    return out


def modal_day(days, slots=48):
    """The median day and the middle half around it, across the fortnight.

    The one idea worth taking from the standard report: what the body typically
    does at eight in the morning is a different question from what it did
    yesterday, and only this can answer it.
    """
    out = []
    span = 1440 / slots
    for slot in range(slots):
        window = sorted(
            v
            for _, readings in days
            for m, v in readings
            if slot * span <= m < (slot + 1) * span
        )
        if window:
            out.append(
                (
                    (slot + 0.5) * span,
                    window[len(window) // 4],
                    window[len(window) // 2],
                    window[3 * len(window) // 4],
                )
            )
    return out


# --- the sheet -------------------------------------------------------------


def draw_rows(canvas, type_, days, bands):
    """One row per day, newest first, hours sorted into bands.

    Sorted rather than left in clock order on purpose: sorted, the length of
    the green run *is* the time in range, and a length can be counted. Clock
    order would make this a heatmap, where the eye estimates shading instead.
    Time of day is not lost — it is what the findings and the typical day are
    for.
    """
    top, row_h, mark, gap = ROWS
    for index, (label, readings) in enumerate(reversed(days)):
        y = top + index * row_h
        newest = index == 0
        ink = INK if newest else MUTED
        type_.draw(canvas, ROW_LABEL_X, y + mark - 4, "small", label, ink)
        hours = [h for h in hours_by_band(readings, bands) if h]
        hours.sort(key=SEVERITY.index)
        x = ROW_MARKS_X
        for name in hours:
            canvas.rect(x, y, x + mark, y + mark, COLOURS[name])
            x += mark + gap
        share = time_in_range([v for _, v in readings])
        type_.draw(canvas, x + 16, y + mark - 4, "figure", f"{share * 100:.0f}%", ink)
        if newest:
            canvas.rect(MARGIN, y + mark + 6, WIDTH - MARGIN, y + mark + 7, RULE)
    return top + len(days) * row_h


def draw_typical_day(canvas, type_, days):
    caption, top, bottom = TYPICAL
    left, right = MARGIN, WIDTH - MARGIN
    type_.draw(canvas, left, caption, "body", "a typical day, drawn from all fourteen", INK)
    canvas.rect(left, top, right, bottom, PAPER)

    def y_for(value):
        clamped = max(Y_MIN, min(Y_MAX, value))
        return bottom - 14 - (clamped - Y_MIN) / (Y_MAX - Y_MIN) * (bottom - top - 28)

    canvas.rect(left, y_for(180), right, y_for(70), TARGET_FILL)
    band = modal_day(days)
    if not band:
        return
    step = (right - left) / len(band)
    for minute, low, _, high in band:
        x = left + minute / 1440 * (right - left)
        canvas.rect(x, y_for(high), x + step + 1, y_for(low), BAND_FILL)
    previous = None
    for minute, _, median, _ in band:
        x = left + minute / 1440 * (right - left)
        y = y_for(median)
        if previous:
            canvas.segment(previous[0], previous[1], x, y, COLOURS[band_of(median, BANDS_FALLBACK)], 3.4)
        previous = (x, y)
    for edge in Y_EDGES:
        canvas.rect(left, y_for(edge), right, y_for(edge) + 1, RULE)
        type_.draw(canvas, left + 10, int(y_for(edge)) - 10, "small", f"{edge / MMOL:.1f}", MUTED)
    for hour in (6, 12, 18):
        x = left + hour / 24 * (right - left)
        canvas.rect(x, top, x + 1, bottom, GRID)
        type_.draw(canvas, x + 10, bottom - 12, "small", f"{hour:02d}", MUTED)


# Only used to colour the median line, which is a summary rather than a
# reading; the caller's own bands drive everything that describes real data.
BANDS_FALLBACK = (
    ("very low", 0, 53),
    ("low", 54, 69),
    ("in range", 70, 180),
    ("high", 181, 250),
    ("very high", 251, 10_000),
)


def hour_means(readings):
    """Mean mg/dL for each hour of a day, or None where the sensor sat out."""
    out = []
    for hour in range(24):
        window = [v for m, v in readings if hour * 60 <= m < (hour + 1) * 60]
        out.append(statistics.fmean(window) if window else None)
    return out


def outliers(days, bands):
    """How yesterday departed from the days before it, biggest first.

    Same rule as `findings`: these describe the reader's own data and never
    instruct. "The 08:00 hour ran higher than on any of the last thirteen days"
    is a fact; what to do about it is not this program's business.

    A day that was simply ordinary says so. That is a real answer to "was
    anything unusual", and the most common one — a sheet that manufactures a
    finding every morning teaches the reader to ignore all of them.
    """
    label, today = days[-1]
    history = days[:-1]
    values = [v for _, v in today]
    out = []

    if len(history) < 3:
        return [("first days", f"only {len(days)} days on record, so there is nothing to compare against yet")]

    # A low is the one thing worth leading with whenever it happened.
    if min(values) < 70:
        clear = sum(1 for _, readings in history if min(v for _, v in readings) >= 70)
        when = min(((m, v) for m, v in today if v < 70), key=lambda r: r[1])
        out.append(
            (
                "below 3.9",
                f"reached {when[1] / MMOL:.1f} at {int(when[0]) // 60:02d}:{int(when[0]) % 60:02d}, "
                f"after {clear} of the last {len(history)} days stayed clear",
            )
        )

    # The hour that departed furthest from its own normal, which is the whole
    # point of holding history behind a single day.
    today_hours = hour_means(today)
    past_hours = [hour_means(readings) for _, readings in history]
    worst = None
    for hour in range(24):
        mine = today_hours[hour]
        theirs = [h[hour] for h in past_hours if h[hour] is not None]
        if mine is None or len(theirs) < 3:
            continue
        spread = statistics.pstdev(theirs)
        if spread < 6:  # a flat hour makes every deviation look enormous
            continue
        gap = (mine - statistics.fmean(theirs)) / spread
        if worst is None or abs(gap) > abs(worst[1]):
            worst = (hour, gap, mine - statistics.fmean(theirs))
    if worst and abs(worst[1]) >= 1.5:
        hour, _, delta = worst
        out.append(
            (
                f"{hour:02d}:00",
                f"ran {abs(delta) / MMOL:.1f} mmol {'above' if delta > 0 else 'below'} "
                f"its usual, the day's biggest departure",
            )
        )

    # Where the day placed among its neighbours.
    mine = time_in_range(values)
    beaten = sum(1 for _, readings in history if time_in_range([v for _, v in readings]) < mine)
    if beaten == len(history):
        out.append(("best in weeks", f"{mine * 100:.0f}% in range, the best of the last {len(days)} days"))
    elif beaten == 0:
        out.append(("hardest lately", f"{mine * 100:.0f}% in range, the lowest of the last {len(days)} days"))
    else:
        out.append(
            ("in range", f"{mine * 100:.0f}%, better than {beaten} of the last {len(history)} days")
        )

    night = [v for m, v in today if m < 360]
    past_night = [v for _, readings in history for m, v in readings if m < 360]
    if night and past_night:
        delta = statistics.fmean(night) - statistics.fmean(past_night)
        if abs(delta) / MMOL >= 0.5:
            out.append(
                (
                    "the night",
                    f"{abs(delta) / MMOL:.1f} mmol {'higher' if delta > 0 else 'lower'} "
                    "than your usual night",
                )
            )

    if len(out) == 1 and out[0][0] == "in range":
        out.insert(0, ("nothing unusual", "yesterday sat inside its normal spread all day"))
    return out


def draw_against_normal(canvas, type_, today, history, bands, caption=None):
    """Yesterday's trace over the band its own history draws.

    The shaded band is the middle half of the previous days at that time of
    day; the line leaving it is the only thing the reader has to look for.
    """
    default_caption, top, bottom = AGAINST
    caption = caption or default_caption
    top = max(top, caption + 20)
    left, right = MARGIN, WIDTH - MARGIN
    type_.draw(
        canvas, left, caption, "body",
        f"yesterday, against the middle half of the last {len(history)} days", INK,
    )
    canvas.rect(left, top, right, bottom, PAPER)

    def y_for(value):
        clamped = max(Y_MIN, min(Y_MAX, value))
        return bottom - 16 - (clamped - Y_MIN) / (Y_MAX - Y_MIN) * (bottom - top - 32)

    canvas.rect(left, y_for(180), right, y_for(70), TARGET_FILL)
    band = modal_day(history) if history else []
    if band:
        step = (right - left) / len(band)
        for minute, low, _, high in band:
            x = left + minute / 1440 * (right - left)
            canvas.rect(x, y_for(high), x + step + 1, y_for(low), BAND_FILL)

    previous = None
    for minute, value in today:
        x = left + minute / 1440 * (right - left)
        y = y_for(value)
        if previous and x - previous[0] <= 20 / 1440 * (right - left):
            canvas.segment(previous[0], previous[1], x, y, COLOURS[band_of(value, bands)], 3.4)
        previous = (x, y)

    for edge in Y_EDGES:
        canvas.rect(left, y_for(edge), right, y_for(edge) + 1, RULE)
        _label_on_pad(canvas, type_, left + 10, int(y_for(edge)) - 10, f"{edge / MMOL:.1f}")
    for hour in (6, 12, 18):
        x = left + hour / 24 * (right - left)
        canvas.rect(x, top, x + 1, bottom, GRID)
        type_.draw(canvas, x + 10, bottom - 14, "small", f"{hour:02d}", MUTED)


def _label_on_pad(canvas, type_, x, baseline, text):
    """An axis label the trace can pass behind without eating it."""
    width = type_.width("small", text)
    canvas.rect(x - 6, baseline - 24, x + width + 6, baseline + 8, PAPER)
    type_.draw(canvas, x, baseline, "small", text, MUTED)


def draw_day_marks(canvas, type_, today, bands):
    """The day as counted hours, so both sheets speak the same language."""
    top, mark, gap = DAY_MARKS, 26, 8
    hours = [h for h in hours_by_band(today, bands) if h]
    hours.sort(key=SEVERITY.index)
    type_.draw(canvas, MARGIN, top - 14, "body", "the day, hour by hour", INK)
    x = MARGIN
    for name in hours:
        canvas.rect(x, top, x + mark, top + mark, COLOURS[name])
        x += mark + gap


def draw_findings(canvas, type_, items, top):
    """Each finding takes the height its own text needs.

    These are computed sentences of unknowable length, so a fixed row height is
    a collision waiting for the day the wording runs long — which it did, on
    the second line of the first one written.
    """
    for key, text in items:
        canvas.rect(MARGIN, top, WIDTH - MARGIN, top + 1, RULE)
        type_.draw(canvas, MARGIN, top + 40, "body", key, COLOURS["in range"])
        lines = type_.wrap("body", text, WIDTH - MARGIN - 250)
        for index, line in enumerate(lines):
            type_.draw(canvas, 250, top + 40 + index * 34, "body", line, INK)
        top += FINDING_STEP + (len(lines) - 1) * 34
    return top


def draw_foot(canvas, type_, values):
    mean = statistics.fmean(values)
    canvas.rect(MARGIN, FOOT, WIDTH - MARGIN, FOOT + 2, INK)
    cells = (
        ("time in range", f"{time_in_range(values) * 100:.0f}%"),
        ("average", f"{mean / MMOL:.1f}"),
        # Glucose Management Indicator, the standard estimate of HbA1c from
        # mean glucose. Defined on mg/dL, hence the unconverted mean.
        ("gmi", f"{3.31 + 0.02392 * mean:.1f}%"),
        ("spread", f"{statistics.pstdev(values) / MMOL:.1f}"),
    )
    for index, (name, value) in enumerate(cells):
        x = MARGIN + index * ((WIDTH - 2 * MARGIN) / 4)
        type_.draw(canvas, x, FOOT + 40, "small", name, MUTED)
        type_.draw(canvas, x, FOOT + 92, "stat", value, INK)


def _masthead(canvas, type_, source, items):
    """Returns the baseline the sheet may start under.

    The headline is a computed sentence, so its length is not knowable when the
    layout is written. It takes as many lines as it needs, up to three, and
    everything below flows from where it ended — clipping it at a fixed two
    lines cut sentences mid-word.
    """
    type_.draw(canvas, MARGIN, SOURCE_BASELINE, "small", source, MUTED)
    canvas.rect(MARGIN, SOURCE_BASELINE + 18, WIDTH - MARGIN, SOURCE_BASELINE + 20, INK)
    key, text = items[0]
    baseline, step = HEADLINE
    lines = type_.wrap("headline", f"{key} {text}", WIDTH - 2 * MARGIN)[:3]
    for index, line in enumerate(lines):
        type_.draw(canvas, MARGIN, baseline + index * step, "headline", line, INK)
    return baseline + (len(lines) - 1) * step


def render_fortnight(days, bands, source=SOURCE):
    """The Saturday sheet: fourteen days, and what they say together.

    `days` is [(label, [(minute_of_day, mg/dL), ...]), ...], oldest first.
    """
    canvas = Canvas(WIDTH, HEIGHT, GROUND)
    type_ = Type()
    items = findings(days, bands)

    _masthead(canvas, type_, source, items)
    draw_rows(canvas, type_, days, bands)
    draw_typical_day(canvas, type_, days)
    draw_findings(canvas, type_, items[1:3], FINDINGS)
    draw_foot(canvas, type_, [v for _, readings in days for _, v in readings])
    return canvas.png()


def render_day(days, bands, source=DAY_SOURCE):
    """Every other morning: yesterday, against what yesterday usually looks like.

    The question a single day can honestly answer is not "how did it go" — the
    message beside the picture already says that — but "was any of it unusual".
    So the day is drawn on top of its own normal, and the sheet leads with the
    largest departure from it.
    """
    canvas = Canvas(WIDTH, HEIGHT, GROUND)
    type_ = Type()
    label, today = days[-1]
    history = days[:-1]
    items = outliers(days, bands)

    headline_bottom = _masthead(canvas, type_, f"{label}  ·  {source}", items)
    draw_against_normal(canvas, type_, today, history, bands, headline_bottom + 52)
    draw_day_marks(canvas, type_, today, bands)
    draw_findings(canvas, type_, items[1:3], DAY_FINDINGS)
    draw_foot(canvas, type_, [v for _, v in today])
    return canvas.png()
