#!/usr/bin/env python3
"""Draw the glucose day as a PNG, with no dependencies at all.

matplotlib would be the obvious tool and is the wrong one here: it would mean
building a wheel on arm64 or carrying a much larger image, for a chart whose
whole content is rectangles, a line and some text. `zlib` and `struct` are in
the standard library and a PNG is not complicated — an IHDR, a zlib-compressed
block of filtered RGB rows, and an IEND. So the CronJob keeps running on a
stock `python:3.13-alpine` with nothing installed.

The type is real type, and still costs no dependency: `bake_font.py` rasterises
Adwaita Sans and JetBrains Mono on a workstation and `glyphs.py` ships the
resulting coverage bitmaps. Everything here draws by blending those bytes.

Three things this is built around, all learned the hard way:

*Everything is labelled.* The first version had no text, on the theory that the
numbers were in the message the image is attached to. With nothing marking
where 3.9 and 10.0 sit, the eye reads the plot background as the target range,
and a day that was 100% in range looks like it has excursions. A chart that
disagrees with its own caption is worse than no chart.

*It is sized to the box it is shown in*, which a screenshot settled: Campfire
caps an attachment on height, so a tall image is scaled down and shown narrow.
The canvas is 1000x1200 because that is the box exactly.

*It draws only what stays legible at that size.* Roughly 340 CSS pixels across
is not enough for a five-row legend and three statistic tiles as well, and
every number those carried is in the message posted directly above the picture.
Type big enough to read beats a second copy of the text.
"""

import math
import statistics
import struct
import zlib

import glyphs

# --- palette ---------------------------------------------------------------
#
# Dark, blue-grey rather than black: the image is looked at in a lit room in the
# morning, and a black rectangle in the middle of the room's own dark chrome
# reads as a hole. Cards a step lighter than the page do the grouping that
# rules and boxes would otherwise have to.

PAGE = (15, 21, 35)
PANEL = (26, 34, 53)
NIGHT = (19, 26, 42)
INK = (226, 232, 240)
# Two text tones, not three. A third, fainter grey for units read at 2.2:1 on
# the cards — below the 4.5:1 floor for text this size, and unreadable on a
# phone in daylight, which is the whole use scene. Hierarchy comes from size
# and weight instead, which costs nothing.
MUTED = (128, 143, 170)
GRID = (44, 57, 84)
TARGET_FILL = (28, 54, 43)
TARGET_EDGE = (46, 96, 71)

# The AGP convention — reds below, green in range, ambers above — re-stepped so
# neighbouring bands differ in lightness as well as hue.
#
# The colours this replaces failed a colour-vision check badly: `low` and
# `in range` were 2.2 ΔE apart under deuteranopia, which is to say identical to
# roughly one man in twelve. On a chart about hypoglycaemia that is not a
# styling nit. `high` and `very high` were 14.6 ΔE apart under *normal* vision,
# too close for anyone.
#
# The ladder now runs pale-to-saturated on each side of the target, so severity
# reads as intensity in either direction and survives every simulation:
# adjacent pairs are ≥12 ΔE under protanopia, deuteranopia and tritanopia, and
# every step clears 3:1 against the card it sits on. Checked with the dataviz
# skill's validator, dark mode, surface #1A2235.
COLOURS = {
    "very low": (244, 63, 94),
    "low": (253, 164, 175),
    "in range": (34, 165, 90),
    "high": (253, 224, 71),
    "very high": (249, 115, 22),
}

# --- layout ----------------------------------------------------------------
#
# Sized to the box Campfire actually gives an attachment, which a screenshot
# settled: it is capped on *height*, at about 339x400 CSS px, and 3x on a
# phone makes that 1017x1200 device pixels. The old 800x1360 hit the height cap
# first and was drawn at 706x1200 — downscaled twelve percent with a third of
# the available width left empty. Portrait was the wrong instinct: past this
# aspect ratio, taller only means smaller.
#
# 1000x1200 fills the box at 1:1. Nothing here is drawn smaller than it has to
# be, and nothing is drawn that the message beside it already says.

WIDTH, HEIGHT = 1000, 1200
MARGIN = 56
HERO = (MARGIN, 340)
BAR = (MARGIN, 444, WIDTH - MARGIN, 486)
ENCOURAGEMENT = 552
CHIPS = (600, 678)
CHIP_GAP = 16
# The left edge is set so the widest y label starts exactly on the page margin:
# the axis column is part of the page's left edge, not an indent from it.
PLOT = (MARGIN, 766, WIDTH - MARGIN, 1110)
# The axis label column, inside the card, and a matching breath on the right so
# the trace does not run into the border.
PLOT_GUTTER, PLOT_EDGE = 96, 20
PLOT_INSET = 30

MMOL = 18.0182
# Fixed vertical extent rather than one fitted to the data: a flat day should
# look flat, not fill the frame. The ceiling is 14 mmol/L, the top of the "high"
# band — above it the readings are rare and the space they reserve is stolen
# from the part actually read, which is the four mmol either side of target.
# Anything higher clamps to the top edge, still plainly orange, and the marker
# on the day's peak still carries its true value.
Y_MIN, Y_MAX = 40, 14.0 * MMOL
# Gridlines and labels, in mmol/L. 3.9 and 10.0 are the target edges and are
# the two that matter. The last is the ceiling itself: without it the top of
# the frame is an unlabelled edge, and a trace pinned against it looks like a
# reading rather than a clamp. The true value is still on the high marker.
Y_TICKS = (3.9, 6.0, 10.0, Y_MAX / MMOL)
# Shaded as night. Not an interval anyone chose — it is simply the part of the
# day whose numbers were produced while asleep, and it reads differently.
NIGHT_HOURS = (0, 6)

# What the picture is of. Opened full screen the message it was attached to is
# no longer on the page, and forwarded it never was, so the image has to say.
SOURCE = "Blood glucose · Nightscout"


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

    def round_rect(self, left, top, right, bottom, radius, colour, alpha=1.0):
        left, top, right, bottom = int(left), int(top), int(right), int(bottom)
        # Fill the straight parts wholesale, then only the four corner squares
        # need per-pixel coverage.
        self.rect(left + radius, top, right - radius, bottom, colour, alpha)
        self.rect(left, top + radius, left + radius, bottom - radius, colour, alpha)
        self.rect(right - radius, top + radius, right, bottom - radius, colour, alpha)
        for x, y in _corner_pixels(left, top, right, bottom, radius):
            cover = _round_coverage(x, y, left, top, right, bottom, radius)
            self.blend(x, y, colour, alpha * cover)

    def round_corners(self, left, top, right, bottom, radius, backdrop):
        """Paint `backdrop` back over the corners of an already-drawn block.

        Rounding a run of butted-up rectangles this way keeps them one shape.
        Filling each rectangle as its own rounded rect would round the joins in
        the middle too, which is not a bar, it is five lozenges.
        """
        for x, y in _corner_pixels(left, top, right, bottom, radius):
            cover = _round_coverage(x, y, left, top, right, bottom, radius)
            self.blend(x, y, backdrop, 1 - cover)

    def dot(self, cx, cy, radius, colour, alpha=1.0):
        for y in range(int(cy - radius) - 1, int(cy + radius) + 2):
            for x in range(int(cx - radius) - 1, int(cx + radius) + 2):
                cover = min(1.0, max(0.0, radius + 0.5 - math.hypot(x - cx, y - cy)))
                self.blend(x, y, colour, alpha * cover)

    def segment(self, x0, y0, x1, y1, colour, thickness):
        """Anti-aliased line, by distance from the segment.

        Cheaper and smoother than drawing a disc at every Bresenham step, which
        is what this used to do: the discs overlapped into a rope with visibly
        lumpy edges, and at 288 readings a day the lumps were the texture of the
        whole trace.
        """
        half = thickness / 2
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy or 1.0
        for y in range(int(min(y0, y1) - half - 1), int(max(y0, y1) + half + 2)):
            for x in range(int(min(x0, x1) - half - 1), int(max(x0, x1) + half + 2)):
                t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length_sq))
                distance = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
                self.blend(x, y, colour, min(1.0, max(0.0, half + 0.5 - distance)))

    def png(self):
        """Adaptive per-row filtering, which is most of the file size.

        A PNG row may be encoded as a difference from the row above or the
        pixel to the left, and the encoder picks per row. Flat cards and smooth
        gradients compress several times better under Up/Sub than under the
        None this used to emit, for about thirty lines of arithmetic.
        """
        stride = self.width * 3
        raw = bytearray()
        previous = bytes(stride)
        for y in range(self.height):
            row = bytes(self.buf[y * stride : (y + 1) * stride])
            best = None
            for kind in range(5):
                candidate = _filter_row(kind, row, previous, 3)
                # The standard heuristic: the encoding whose bytes are closest
                # to zero on average is the one that compresses best.
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


def _coverage(x, y, cx, cy, radius):
    """How much of one pixel falls inside a circle, sampled 3x3."""
    hits = 0
    for sy in (0.17, 0.5, 0.83):
        for sx in (0.17, 0.5, 0.83):
            if math.hypot(x + sx - cx, y + sy - cy) <= radius:
                hits += 1
    return hits / 9


def _round_coverage(x, y, left, top, right, bottom, radius):
    """How much of one pixel falls inside a rounded rectangle.

    A pixel is only on an arc when it is past the radius in *both* axes; the
    straight edges are simply inside. Getting that wrong eats the ends off a
    bar and leaves the corner arc floating beside it.
    """
    corner_x = (
        left + radius
        if x < left + radius
        else (right - radius - 1 if x > right - radius - 1 else None)
    )
    corner_y = (
        top + radius
        if y < top + radius
        else (bottom - radius - 1 if y > bottom - radius - 1 else None)
    )
    if corner_x is None or corner_y is None:
        return 1.0
    return _coverage(x, y, corner_x, corner_y, radius)


def _corner_pixels(left, top, right, bottom, radius):
    """The four corner squares, which are the only pixels needing coverage."""
    for x0, x1 in ((left, left + radius), (right - radius, right)):
        for y0, y1 in ((top, top + radius), (bottom - radius, bottom)):
            for y in range(y0, y1):
                for x in range(x0, x1):
                    yield x, y


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
                # A character with no glyph advances a space rather than
                # raising, so an unforeseen label degrades to a gap instead of
                # failing the run at 07:00.
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

    def right(self, canvas, right, baseline, face, string, colour, alpha=1.0):
        self.draw(
            canvas, right - self.width(face, string), baseline, face, string, colour, alpha
        )

    def centre(self, canvas, centre, baseline, face, string, colour, alpha=1.0):
        self.draw(
            canvas,
            centre - self.width(face, string) / 2,
            baseline,
            face,
            string,
            colour,
            alpha,
        )


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


def encouragement(values, bands):
    """One line about how the day went, in the room's own voice.

    Rules it is written to. It praises a good day and stays warm on a hard one.
    It never instructs, never implies fault, and never says anything that could
    be mistaken for medical advice — this is a review of a day already over, so
    there is nothing left to act on and nothing to be told. A digest that
    scolds is a digest that gets muted, and the whole point of it arriving
    daily is that the trend only exists if it keeps arriving.

    70% in range is the consensus target, so the ladder is hung off that rather
    than off a number invented here.
    """
    in_range = dict(shares(values, bands))["in range"]
    lows = sum(1 for v in values if v < 70) / len(values)
    if in_range >= 0.95:
        return "Outstanding! Almost the whole day in range."
    if in_range >= 0.85:
        return "Great day! Comfortably past the 70% target."
    if in_range >= 0.70:
        return "Target hit! That's the number that counts."
    if lows >= 0.06:
        return "A bumpy night, but you rode it out."
    if in_range >= 0.50:
        return "Not your day, and that's fine. One day is never a trend."
    return "Some days just go like this. The average forgives it."


def draw_summary(canvas, type_, values, bands):
    """Time in range as the headline, the split beneath it as a length.

    The one number worth reading from across the room is time in range, so it
    is the only thing set large. The bar is there because a proportion is
    easier to judge as a length than as a percentage.

    The five-row legend and the average/GMI/spread tiles that used to sit here
    are gone. Campfire renders this image about 340 CSS pixels wide, which is
    not enough for all of it at a legible size, and every number they carried
    is already in the message posted directly above the picture. Type big
    enough to read beats a second copy of the text.
    """
    parts = shares(values, bands)

    in_range = dict(parts)["in range"]
    type_.draw(canvas, HERO[0], HERO[1], "hero", f"{in_range * 100:.0f}%", INK)
    type_.draw(canvas, HERO[0], HERO[1] + 56, "body", "time in range 3.9-10.0", MUTED)

    _draw_split_bar(canvas, parts)
    type_.draw(canvas, MARGIN, ENCOURAGEMENT, "body", encouragement(values, bands), INK)


def _draw_split_bar(canvas, parts):
    """Two pixels of page colour between segments, so the split is legible as
    shape and not only as hue — the boundary between two adjacent bands is
    exactly where colour vision fails. That gap and the fixed band order are
    what stop the bar depending on hue alone.
    """
    left, top, right, bottom = BAR
    span = right - left
    cursor = float(left)
    for name, share in parts:
        if share <= 0:
            continue
        end = cursor + share * span
        canvas.rect(cursor, top, end, bottom, COLOURS[name])
        cursor = end
        if cursor < right:
            canvas.rect(cursor - 1, top, cursor + 1, bottom, PANEL)
    canvas.round_corners(left, top, right, bottom, (bottom - top) // 2, PANEL)


def plot_area():
    """The data rectangle inside the plot card.

    The axis labels live in a column *inside* the card rather than beside it.
    Outside, they pushed the card's left edge 52px further in than its right,
    so the one element with an axis was the one element not on the page margin.
    """
    panel_left, top, panel_right, bottom = PLOT
    return panel_left + PLOT_GUTTER, top, panel_right - PLOT_EDGE, bottom


def draw_day(canvas, type_, readings, bands, day_start_ms):
    """Glucose against time of day, with the target band shaded and labelled."""
    canvas.round_rect(*PLOT, 12, PANEL)
    left, top, right, bottom = plot_area()

    # The scale stops short of the frame at both ends. Without that gutter a
    # reading at the ceiling is drawn on the border itself, and the label for a
    # night spent against the floor has nowhere to go but on top of the trace.
    floor, ceiling = bottom - PLOT_INSET, top + PLOT_INSET

    def y_for(mgdl):
        clamped = max(Y_MIN, min(Y_MAX, mgdl))
        return floor - (clamped - Y_MIN) / (Y_MAX - Y_MIN) * (floor - ceiling)

    def x_for(minutes):
        return left + minutes / 1440 * (right - left)

    canvas.rect(x_for(NIGHT_HOURS[0] * 60), top, x_for(NIGHT_HOURS[1] * 60), bottom, NIGHT)
    canvas.rect(left, y_for(180), right, y_for(70), TARGET_FILL)
    for edge in (70, 180):
        canvas.rect(left, y_for(edge) - 1, right, y_for(edge) + 1, TARGET_EDGE)

    for mmol in Y_TICKS:
        y = y_for(mmol * MMOL)
        if mmol * MMOL not in (70, 180):
            canvas.rect(left, y, right, y + 1, GRID)
        type_.right(canvas, left - 16, int(y) + 11, "tick", f"{mmol:.1f}", MUTED)
    type_.draw(canvas, MARGIN, top - 30, "body", "mmol/L", MUTED)

    # Every three hours, but only the six-hour marks are labelled and ruled.
    # The short ticks are enough to count 03:00 off without another line
    # crossing the trace.
    for hour in range(0, 25, 3):
        x = x_for(hour * 60)
        if hour % 6:
            canvas.rect(x, bottom - 8, x + 1, bottom, GRID)
            continue
        canvas.rect(x, top, x + 1, bottom, GRID)
        type_.centre(
            canvas, min(max(x, left + 14), right - 14), bottom + 34, "tick", f"{hour:02d}", MUTED
        )

    points = []
    for stamp, value in sorted(readings):
        minutes = (stamp - day_start_ms) / 60000
        if not 0 <= minutes <= 1440:
            continue
        points.append((x_for(minutes), y_for(value), value))

    # Twenty minutes, expressed in pixels, so the join rule survives a resize.
    # Only readings adjacent in time are joined: a gap where the sensor dropped
    # out should read as a gap, not as a straight line through values that were
    # never measured.
    max_gap = 20 / 1440 * (right - left)
    previous = None
    for x, y, value in points:
        if previous and x - previous[0] <= max_gap:
            canvas.segment(previous[0], previous[1], x, y, COLOURS[band_of(value, bands)], 2.4)
        previous = (x, y)

    _mark_extremes(canvas, type_, points, bands)


def _mark_extremes(canvas, type_, points, bands):
    """Ring and label the day's lowest and highest reading.

    These are the two numbers a glance actually wants and the two a shaded band
    cannot give you. They were a line of text under the chart; on the curve
    they also say *when*, which is the more useful half.
    """
    left, top, right, bottom = plot_area()
    lowest = min(points, key=lambda p: p[2])
    highest = max(points, key=lambda p: p[2])

    # A day that never moved has one extreme, not two. Marking it twice draws
    # both labels on the same reading, where they overlap into nonsense — which
    # is exactly what a flat day above the ceiling produced.
    marks = [(highest, True)] if lowest[2] == highest[2] else [(lowest, False), (highest, True)]

    placed = []
    for (x, y, value), above in marks:
        colour = COLOURS[band_of(value, bands)]
        canvas.dot(x, y, 6, PAGE, 0.75)
        canvas.dot(x, y, 4.5, colour)

        label = f"{value / MMOL:.1f}"
        half = type_.width("tick", label) / 2 + 12
        # Below the low and above the high — never the other way round. The
        # opposite side of an extreme is the side the trace arrived and left
        # on, so a label flipped over there lands squarely on the curve. When
        # the preferred side is out of frame the label goes *beside* the
        # reading instead, level with it, on whichever side has more room.
        if (y - 52 >= top + 4) if above else (y + 52 <= bottom - 4):
            box = [y - 52, y - 12] if above else [y + 12, y + 52]
            # The extreme is often at one end of the day — a night that ended
            # low, a dinner still climbing — so the label slides along the axis
            # to stay in frame while the marker stays on the reading.
            centre = min(max(x, left + half + 4), right - half - 4)
        else:
            box = [y - 20, y + 20]
            offset = half + 26
            centre = x + offset if x < (left + right) / 2 else x - offset
            centre = min(max(centre, left + half + 4), right - half - 4)

        # A spike whose peak and trough are minutes apart puts the two labels
        # on top of each other. Push the later one clear rather than dropping
        # it: both numbers are only on the chart, not in the message.
        for other_centre, other_half, other_box in placed:
            overlaps_x = abs(centre - other_centre) < half + other_half
            overlaps_y = box[0] < other_box[1] and other_box[0] < box[1]
            if overlaps_x and overlaps_y:
                shift = other_box[1] - box[0] + 8 if above else other_box[0] - box[1] - 8
                box = [box[0] + shift, box[1] + shift]

        placed.append((centre, half, box))
        canvas.round_rect(centre - half, box[0], centre + half, box[1], 10, PAGE, 0.85)
        type_.centre(canvas, centre, box[0] + 32, "tick", label, INK)


def draw_chips(canvas, type_, values):
    """The three numbers that describe the day's shape rather than its extremes.

    One line each, label and value side by side: at this size a stacked chip
    would cost twice the height and buy nothing. Values are mono so the three
    of them line up as a row of figures rather than three separate labels.
    """
    mean = statistics.fmean(values)
    chips = (
        ("average", f"{mean / MMOL:.1f}"),
        # Glucose Management Indicator, the standard estimate of HbA1c from
        # mean glucose. Defined on mg/dL, hence the unconverted mean.
        ("GMI", f"{3.31 + 0.02392 * mean:.1f}%"),
        ("spread", f"{statistics.pstdev(values) / MMOL:.1f}"),
    )
    top, bottom = CHIPS
    width = (WIDTH - 2 * MARGIN - 2 * CHIP_GAP) / 3
    for index, (label, value) in enumerate(chips):
        left = MARGIN + index * (width + CHIP_GAP)
        canvas.round_rect(left, top, left + width, bottom, 14, PANEL)
        type_.draw(canvas, left + 22, top + 52, "body", label, MUTED)
        type_.right(canvas, left + width - 22, top + 54, "stat", value, INK)


def render(readings, bands, day_start_ms, title="", subtitle="", source=SOURCE):
    canvas = Canvas(WIDTH, HEIGHT, PAGE)
    type_ = Type()
    values = [v for _, v in readings]

    # The date is the heading; what it is a reading of belongs with the rest of
    # the provenance, on one line under it. It spent a version as a kicker above
    # the date, which is a label wearing a heading's position: it took the eye
    # first and said the one thing that never changes.
    type_.draw(canvas, MARGIN, 92, "title", title, INK)
    meta = " · ".join(part for part in (source, subtitle) if part)
    if meta:
        type_.draw(canvas, MARGIN, 148, "body", meta, MUTED)

    draw_summary(canvas, type_, values, bands)
    draw_chips(canvas, type_, values)
    draw_day(canvas, type_, readings, bands, day_start_ms)
    return canvas.png()
