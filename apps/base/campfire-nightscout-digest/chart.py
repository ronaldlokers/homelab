#!/usr/bin/env python3
"""Draw the glucose day as a PNG, with no dependencies at all.

matplotlib would be the obvious tool and is the wrong one here: it would mean
building a wheel on arm64 or carrying a much larger image, for charts whose
whole content is rectangles, a line and some 5x7 text. `zlib` and `struct` are
in the standard library and a PNG is not complicated — an IHDR, a
zlib-compressed block of RGB rows each prefixed with a filter byte, and an IEND.

So the CronJob keeps running on a stock `python:3.13-alpine` with nothing
installed.

The first version had no text, on the theory that the numbers were in the
message the image is attached to. That was wrong in a way worth recording: with
nothing marking where 3.9 and 10.0 sit, the eye reads the plot background as the
target range and a day that was 100% in range looks like it has excursions. A
chart that disagrees with its own caption is worse than no chart, so everything
is labelled now — axes, legend, and the day's range.
"""

import math
import struct
import zlib

import font

# Shared by every part of the image, so a colour means the same thing in each.
# Roughly the AGP convention: red below, green in range, amber and orange above.
COLOURS = {
    "very low": (153, 27, 27),
    "low": (239, 68, 68),
    "in range": (34, 197, 94),
    "high": (234, 179, 8),
    "very high": (249, 115, 22),
}
WHITE = (255, 255, 255)
INK = (30, 41, 59)
MUTED = (100, 116, 139)
GRID = (226, 232, 240)
PLOT_BG = (248, 250, 252)
TARGET_BG = (219, 247, 228)
TARGET_EDGE = (134, 213, 166)

WIDTH, HEIGHT = 1100, 560
PIE_CENTRE, PIE_RADIUS = (170, 320), 120
LEGEND = (320, 200)
PLOT = (630, 150, 1070, 470)

MMOL = 18.0182
# Fixed vertical extent rather than one fitted to the data: a flat day should
# look flat, not fill the frame. The ceiling is 300 mg/dL because readings above
# it are rare and reserving a third of the plot for them wastes the part you
# actually read; anything higher clamps to the top edge, still plainly orange.
Y_MIN, Y_MAX = 40, 300
# Gridlines and labels, in mmol/L. 3.9 and 10.0 are the target edges and are
# the two that matter.
Y_TICKS = (3.9, 6.0, 10.0, 14.0)


class Canvas:
    def __init__(self, width, height, fill=WHITE):
        self.width, self.height = width, height
        self.buf = bytearray(bytes(fill) * (width * height))

    def set(self, x, y, colour):
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.buf[i : i + 3] = bytes(colour)

    def rect(self, left, top, right, bottom, colour):
        left, right = max(left, 0), min(right, self.width)
        if right <= left:
            return
        row = bytes(colour) * (right - left)
        for y in range(max(top, 0), min(bottom, self.height)):
            start = (y * self.width + left) * 3
            self.buf[start : start + len(row)] = row

    def dot(self, x, y, colour, size=2):
        for dy in range(-size, size + 1):
            for dx in range(-size, size + 1):
                if dx * dx + dy * dy <= size * size:
                    self.set(x + dx, y + dy, colour)

    def line(self, x0, y0, x1, y1, colour, width=1):
        """Bresenham, thickened by drawing a small disc at each step."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx + dy
        while True:
            self.dot(x0, y0, colour, width)
            if x0 == x1 and y0 == y1:
                return
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += sx
            if doubled <= dx:
                error += dx
                y0 += sy

    def text(self, x, y, string, colour=INK, scale=2, tracking=1):
        """Top-left anchored. Unknown characters draw as blanks."""
        cursor = x
        for character in string.upper():
            rows = font.GLYPHS.get(character, font.GLYPHS[" "])
            for row_index, bits in enumerate(rows):
                for column in range(font.WIDTH):
                    if bits & (1 << (font.WIDTH - 1 - column)):
                        self.rect(
                            cursor + column * scale,
                            y + row_index * scale,
                            cursor + (column + 1) * scale,
                            y + (row_index + 1) * scale,
                            colour,
                        )
            cursor += (font.WIDTH + tracking) * scale

    def text_right(self, right, y, string, colour=INK, scale=2):
        self.text(right - font.text_width(string, scale), y, string, colour, scale)

    def png(self):
        def chunk(tag, data):
            body = tag + data
            return (
                struct.pack(">I", len(data))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)  # filter type 0: none
            raw += self.buf[y * stride : (y + 1) * stride]
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(
                b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
            )
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
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


def draw_pie(canvas, values, bands):
    """Time in range as a pie, by testing the angle of every pixel.

    Per-pixel rather than per-slice because filling an arc properly needs
    polygon scanline code, and a 240px circle is 45,000 tests — nothing.
    """
    fractions, running = [], 0.0
    for name, share in shares(values, bands):
        fractions.append((running, running + share, COLOURS[name]))
        running += share

    cx, cy = PIE_CENTRE
    for y in range(cy - PIE_RADIUS - 1, cy + PIE_RADIUS + 2):
        for x in range(cx - PIE_RADIUS - 1, cx + PIE_RADIUS + 2):
            dx, dy = x - cx, y - cy
            if dx * dx + dy * dy > PIE_RADIUS * PIE_RADIUS:
                continue
            # Clockwise from twelve o'clock, as a fraction of the circle.
            angle = (math.degrees(math.atan2(dx, -dy)) % 360) / 360
            for start, end, colour in fractions:
                if start <= angle < end:
                    canvas.set(x, y, colour)
                    break


def draw_legend(canvas, values, bands):
    """Swatch, name and share — the key to both charts at once.

    Every band is listed even at 0%, because "no lows" is a thing worth reading
    and a missing row only tells you the row is missing.
    """
    x, y = LEGEND
    for name, share in shares(values, bands):
        canvas.rect(x, y + 3, x + 18, y + 21, COLOURS[name])
        canvas.rect(x, y + 3, x + 18, y + 4, INK)
        canvas.rect(x, y + 20, x + 18, y + 21, INK)
        canvas.text(x + 28, y + 5, name, INK, 2)
        canvas.text_right(x + 215, y + 5, f"{share * 100:.0f}%", INK, 2)
        y += 34


def draw_day(canvas, readings, bands, day_start_ms):
    """Glucose against time of day, with the target band shaded and labelled."""
    left, top, right, bottom = PLOT
    canvas.rect(left, top, right, bottom, PLOT_BG)

    def y_for(mgdl):
        clamped = max(Y_MIN, min(Y_MAX, mgdl))
        return int(bottom - (clamped - Y_MIN) / (Y_MAX - Y_MIN) * (bottom - top))

    # The target range, shaded and edged, so "inside the green" is unambiguous.
    canvas.rect(left, y_for(180), right, y_for(70), TARGET_BG)
    for edge in (70, 180):
        canvas.rect(left, y_for(edge), right, y_for(edge) + 2, TARGET_EDGE)

    for mmol in Y_TICKS:
        y = y_for(mmol * MMOL)
        if mmol * MMOL not in (70, 180):
            canvas.rect(left, y, right, y + 1, GRID)
        canvas.text_right(left - 10, y - 7, f"{mmol:.1f}", MUTED, 2)
    canvas.text_right(left - 10, top - 34, "mmol/L", MUTED, 2)

    for hour in range(0, 25, 6):
        x = left + int(hour / 24 * (right - left))
        canvas.rect(x, top, x + 1, bottom, GRID)
        label = f"{hour:02d}:00"
        canvas.text(
            min(x - font.text_width(label, 2) // 2, right - font.text_width(label, 2)),
            bottom + 12,
            label,
            MUTED,
            2,
        )

    points = []
    for stamp, value in sorted(readings):
        minutes = (stamp - day_start_ms) / 60000
        if not 0 <= minutes <= 1440:
            continue
        x = left + int(minutes / 1440 * (right - left))
        points.append((x, y_for(value), COLOURS[band_of(value, bands)]))

    previous = None
    for x, y, colour in points:
        # Only join readings adjacent in time. A gap where the sensor dropped
        # out should read as a gap, not as a straight line through values that
        # were never measured.
        if previous and x - previous[0] <= 8:
            canvas.line(previous[0], previous[1], x, y, colour, 1)
        canvas.dot(x, y, colour, 2)
        previous = (x, y)


def render(readings, bands, day_start_ms, title="", subtitle=""):
    canvas = Canvas(WIDTH, HEIGHT)
    values = [v for _, v in readings]

    canvas.text(60, 40, title, INK, 4)
    if subtitle:
        canvas.text(60, 92, subtitle, MUTED, 2)

    draw_pie(canvas, values, bands)
    draw_legend(canvas, values, bands)
    draw_day(canvas, readings, bands, day_start_ms)

    # The extremes, which are the two numbers a glance actually wants and the
    # ones a shaded band cannot give you.
    canvas.text(
        PLOT[0],
        PLOT[3] + 40,
        f"low {min(values) / MMOL:.1f}   high {max(values) / MMOL:.1f}   "
        f"target 3.9-10.0",
        MUTED,
        2,
    )
    return canvas.png()
