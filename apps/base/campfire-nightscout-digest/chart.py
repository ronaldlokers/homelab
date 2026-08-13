#!/usr/bin/env python3
"""Draw the glucose day as a PNG, with no dependencies at all.

matplotlib would be the obvious tool and is the wrong one here: it would mean
building a wheel on arm64 or carrying a much larger image, for two charts whose
whole content is coloured rectangles and a line. `zlib` and `struct` are in the
standard library and a PNG is not complicated — an IHDR, a zlib-compressed
block of RGB rows each prefixed with a filter byte, and an IEND.

So the CronJob keeps running on a stock `python:3.13-alpine` with nothing
installed.

There is deliberately no text. Drawing glyphs without a font library means
shipping a bitmap font, and every number worth reading is already in the
message the image is attached to. The shaded target band and the colours carry
the meaning on their own.
"""

import math
import struct
import zlib

# Shared by both charts, so a colour means the same thing in each. Roughly the
# AGP convention: red below, green in range, amber and orange above.
COLOURS = {
    "very low": (153, 27, 27),
    "low": (239, 68, 68),
    "in range": (34, 197, 94),
    "high": (234, 179, 8),
    "very high": (249, 115, 22),
}
WHITE = (255, 255, 255)
GRID = (226, 232, 240)
PLOT_BG = (248, 250, 252)
TARGET_BG = (219, 247, 228)  # the in-range band, tinted

WIDTH, HEIGHT = 1000, 420
PIE_CENTRE, PIE_RADIUS = (215, 210), 150
PLOT = (420, 40, 980, 380)  # left, top, right, bottom

# The graph's vertical extent, in mg/dL. Fixed rather than fitted to the data:
# a day of flat readings should look flat, not fill the frame. The ceiling is
# 300 rather than the sensor's 400 because readings that high are rare and
# reserving a third of the plot for them wastes the part you actually read;
# anything above clamps to the top edge, still unmistakably orange.
Y_MIN, Y_MAX = 40, 300


class Canvas:
    def __init__(self, width, height, fill=WHITE):
        self.width, self.height = width, height
        self.buf = bytearray(bytes(fill) * (width * height))

    def set(self, x, y, colour):
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.buf[i : i + 3] = bytes(colour)

    def rect(self, left, top, right, bottom, colour):
        for y in range(max(top, 0), min(bottom, self.height)):
            row = (y * self.width + max(left, 0)) * 3
            self.buf[row : row + (min(right, self.width) - max(left, 0)) * 3] = bytes(
                colour
            ) * (min(right, self.width) - max(left, 0))

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
            + chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def band_of(value, bands):
    for name, low, high in bands:
        if low <= value <= high:
            return name
    return "very high"


def draw_pie(canvas, values, bands):
    """Time in range as a pie, by testing the angle of every pixel.

    Per-pixel rather than per-slice because filling an arc properly needs
    polygon scanline code, and a 300px circle is 70,000 tests — nothing.
    """
    total = len(values)
    fractions = []
    running = 0.0
    for name, low, high in bands:
        share = sum(1 for v in values if low <= v <= high) / total
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


def draw_day(canvas, readings, bands, day_start_ms):
    """Glucose against time of day, with the target band shaded."""
    left, top, right, bottom = PLOT
    canvas.rect(left, top, right, bottom, PLOT_BG)

    def y_for(mgdl):
        clamped = max(Y_MIN, min(Y_MAX, mgdl))
        return int(bottom - (clamped - Y_MIN) / (Y_MAX - Y_MIN) * (bottom - top))

    # The in-range band, so "inside the green" is readable without axis labels.
    canvas.rect(left, y_for(180), right, y_for(70), TARGET_BG)
    for edge in (70, 180):
        canvas.rect(left, y_for(edge), right, y_for(edge) + 1, GRID)
    # Six-hourly gridlines. Unlabelled, but they make the shape of a night
    # versus an afternoon legible.
    for hour in range(0, 25, 6):
        x = left + int(hour / 24 * (right - left))
        canvas.rect(x, top, x + 1, bottom, GRID)

    points = []
    for stamp, value in sorted(readings):
        minutes = (stamp - day_start_ms) / 60000
        if not 0 <= minutes <= 1440:
            continue
        x = left + int(minutes / 1440 * (right - left))
        points.append((x, y_for(value), COLOURS[band_of(value, bands)]))

    previous = None
    for x, y, colour in points:
        # Only join readings that are actually adjacent in time. A gap where the
        # sensor dropped out should read as a gap, not as a straight line
        # through values that were never measured.
        if previous and x - previous[0] <= 8:
            canvas.line(previous[0], previous[1], x, y, colour, 1)
        canvas.dot(x, y, colour, 2)
        previous = (x, y)


def render(readings, bands, day_start_ms):
    canvas = Canvas(WIDTH, HEIGHT)
    draw_pie(canvas, [v for _, v in readings], bands)
    draw_day(canvas, readings, bands, day_start_ms)
    return canvas.png()
