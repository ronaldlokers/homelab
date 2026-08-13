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

*It is portrait, and sized for a phone*, which is where the digest is read. A
wide chart arrives in the room scaled to the width of the message column, which
is where small text stops being legible.

*It is self-contained.* Tap the attachment and the message is no longer on
screen, so the statistics are drawn into the image rather than left to the text
beside it.
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
NIGHT = (21, 28, 45)
INK = (226, 232, 240)
MUTED = (128, 143, 170)
FAINT = (74, 89, 116)
GRID = (44, 57, 84)
TARGET_FILL = (30, 58, 51)
TARGET_EDGE = (52, 105, 84)

# Roughly the AGP convention: red below, green in range, amber and orange above,
# lifted for a dark background where the original mid-tones went muddy. A colour
# means the same thing in the ring, the bar and the trace.
COLOURS = {
    "very low": (185, 28, 28),
    "low": (248, 113, 113),
    "in range": (52, 199, 123),
    "high": (250, 204, 21),
    "very high": (251, 146, 60),
}

# --- layout ----------------------------------------------------------------

WIDTH, HEIGHT = 800, 1360
MARGIN = 48
CARD = (MARGIN, 150, WIDTH - MARGIN, 470)
HERO = (80, 268)
BAR = (80, 390, WIDTH - 80, 424)
LEGEND = (430, 196)
LEGEND_ROW = 34
PLOT = (118, 570, WIDTH - MARGIN, 1170)
TILES = (1230, 1330)

MMOL = 18.0182
# Fixed vertical extent rather than one fitted to the data: a flat day should
# look flat, not fill the frame. The ceiling is 300 mg/dL because readings above
# it are rare and reserving a third of the plot for them wastes the part you
# actually read; anything higher clamps to the top edge, still plainly orange.
Y_MIN, Y_MAX = 40, 300
# Gridlines and labels, in mmol/L. 3.9 and 10.0 are the target edges and are
# the two that matter.
Y_TICKS = (3.9, 6.0, 10.0, 14.0)
# Shaded as night. Not an interval anyone chose — it is simply the part of the
# day whose numbers were produced while asleep, and it reads differently.
NIGHT_HOURS = (0, 6)


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


def draw_summary(canvas, type_, values, bands):
    """Time in range as the headline, the full split beside and below it.

    The one number worth reading from across the room is time in range, so it
    is the only thing set large. The bar carries the same five figures as the
    legend; it is there because a proportion is easier to judge as a length
    than as a percentage, and the legend is there because "no lows" is worth
    reading and a bar cannot show a band that is 0% wide.
    """
    canvas.round_rect(*CARD, 16, PANEL)
    parts = shares(values, bands)

    in_range = dict(parts)["in range"]
    type_.draw(canvas, HERO[0], HERO[1], "hero", f"{in_range * 100:.0f}%", INK)
    type_.draw(canvas, HERO[0], HERO[1] + 40, "body", "time in range 3.9-10.0", MUTED)

    x, baseline = LEGEND
    for name, share in parts:
        canvas.dot(x + 7, baseline - 7, 7, COLOURS[name])
        type_.draw(canvas, x + 28, baseline, "body", name, INK)
        type_.right(canvas, CARD[2] - 24, baseline, "stat", f"{share * 100:.0f}%", INK)
        baseline += LEGEND_ROW

    left, top, right, bottom = BAR
    cursor = float(left)
    for name, share in parts:
        if share <= 0:
            continue
        end = cursor + share * (right - left)
        canvas.rect(cursor, top, end, bottom, COLOURS[name])
        cursor = end
    canvas.round_corners(left, top, right, bottom, (bottom - top) // 2, PANEL)


def draw_day(canvas, type_, readings, bands, day_start_ms):
    """Glucose against time of day, with the target band shaded and labelled."""
    left, top, right, bottom = PLOT
    canvas.round_rect(left, top, right, bottom, 12, PANEL)

    def y_for(mgdl):
        clamped = max(Y_MIN, min(Y_MAX, mgdl))
        return bottom - (clamped - Y_MIN) / (Y_MAX - Y_MIN) * (bottom - top)

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
        type_.right(canvas, left - 14, int(y) + 8, "tick", f"{mmol:.1f}", MUTED)
    type_.draw(canvas, left, top - 15, "body", "mmol/L", FAINT)

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
    left, top, right, bottom = PLOT
    for point, prefer_above in (
        (min(points, key=lambda p: p[2]), False),
        (max(points, key=lambda p: p[2]), True),
    ):
        x, y, value = point
        # Below the low and above the high, unless that would put the label
        # through the frame — a day that spent the night against the floor puts
        # its minimum within a few pixels of the bottom edge.
        above = prefer_above
        if above and y - 44 < top + 4:
            above = False
        elif not above and y + 44 > bottom - 4:
            above = True
        colour = COLOURS[band_of(value, bands)]
        canvas.dot(x, y, 6, PAGE, 0.75)
        canvas.dot(x, y, 4.5, colour)

        label = f"{value / MMOL:.1f}"
        half = type_.width("tick", label) / 2 + 10
        # The extreme is very often at one edge — a day that starts high, a
        # sensor that ends low — so the label slides along to stay in frame
        # while the marker stays on the reading.
        centre = min(max(x, left + half + 4), right - half - 4)
        canvas.round_rect(
            centre - half,
            (y - 44) if above else (y + 14),
            centre + half,
            (y - 14) if above else (y + 44),
            8,
            PAGE,
            0.82,
        )
        type_.centre(canvas, centre, (y - 22) if above else (y + 36), "tick", label, INK)


def draw_tiles(canvas, type_, values):
    """The three numbers that describe the day's shape rather than its extremes."""
    mean = statistics.fmean(values)
    tiles = (
        ("average", f"{mean / MMOL:.1f}", "mmol/L"),
        # Glucose Management Indicator, the standard estimate of HbA1c from
        # mean glucose. Defined on mg/dL, hence the unconverted mean.
        ("GMI", f"{3.31 + 0.02392 * mean:.1f}%", "HbA1c"),
        ("spread", f"{statistics.pstdev(values) / MMOL:.1f}", "SD"),
    )
    top, bottom = TILES
    width = (WIDTH - 2 * MARGIN - 32) / 3
    for index, (label, value, unit) in enumerate(tiles):
        left = MARGIN + index * (width + 16)
        canvas.round_rect(left, top, left + width, bottom, 14, PANEL)
        # Label alone on the first line, unit trailing the value on the second.
        # Both on one line is how the widest pair — GMI and its unit — collided.
        type_.draw(canvas, left + 20, top + 32, "body", label, MUTED)
        type_.draw(canvas, left + 20, top + 78, "stat", value, INK)
        type_.right(canvas, left + width - 20, top + 78, "body", unit, FAINT)


def render(readings, bands, day_start_ms, title="", subtitle=""):
    canvas = Canvas(WIDTH, HEIGHT, PAGE)
    type_ = Type()
    values = [v for _, v in readings]

    type_.draw(canvas, MARGIN, 78, "title", title, INK)
    if subtitle:
        type_.draw(canvas, MARGIN, 116, "body", subtitle, MUTED)

    draw_summary(canvas, type_, values, bands)
    draw_day(canvas, type_, readings, bands, day_start_ms)
    draw_tiles(canvas, type_, values)
    return canvas.png()
