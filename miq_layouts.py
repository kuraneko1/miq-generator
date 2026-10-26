"""Layout registry and the scene helpers every layout draws with.

A layout is a function ``draw(scene) -> Image``. Coordinates are written in a
1920x1080 design space and mapped onto the working canvas with ``scene.box``
and friends, so every layout works at any output size and aspect ratio.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property

from PIL import Image, ImageDraw

import miq_effects as fx
from miq_fonts import FontFamily, bundled_fonts, font_at
from miq_text import MixedFont, credit, fit, paint
from miq_words import runs

DESIGN = (1920, 1080)
# Design space of each orientation; layouts write coordinates in these units.
DESIGNS = {
    "landscape": DESIGN,
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}
ORIENTATIONS = tuple(DESIGNS)


@dataclass(frozen=True)
class Layout:
    name: str
    label: str
    category: str
    fonts: tuple[str, ...]
    draw: object = field(repr=False, compare=False)
    # Native layouts paint at the output size; the others are supersampled.
    native: bool = False
    # Users ask for a layout by this number (miq12), so it never changes.
    number: int = 0
    # Every orientation the layout can draw; the first is its default.
    orientations: tuple[str, ...] = ("landscape",)
    # Hidden layouts are only drawn when asked for (miq 切り抜き), never by lot.
    hidden: bool = False

    @property
    def orientation(self):
        return self.orientations[0]

    @property
    def design(self):
        return DESIGNS[self.orientation]

    def orientation_for(self, wanted):
        """``wanted`` if the layout can draw it, else its own orientation."""

        return wanted if wanted in self.orientations else self.orientation


REGISTRY: dict[str, Layout] = {}


def layout(
    name,
    label,
    category,
    fonts,
    *,
    number,
    native=False,
    orientation="landscape",
    orientations=None,
    hidden=False,
):
    orientations = tuple(orientations or (orientation,))
    if not orientations or any(o not in DESIGNS for o in orientations):
        raise ValueError(f"不明な向き: {orientations}")

    def register(function):
        taken = by_number(number)
        if taken is not None and taken.name != name:
            raise ValueError(f"構図番号 {number} は {taken.name} が使用中です。")
        REGISTRY[name] = Layout(
            name,
            label,
            category,
            tuple(fonts),
            function,
            native,
            number,
            orientations,
            hidden,
        )
        return function

    return register


def by_number(number):
    return next((item for item in REGISTRY.values() if item.number == number), None)


def names(category=None, orientation=None, *, hidden=False):
    """Layouts drawn by lot (``hidden=True`` also lists the on-request ones)."""

    return [
        n
        for n, item in REGISTRY.items()
        if category in (None, item.category)
        and (orientation is None or orientation in item.orientations)
        and (hidden or not item.hidden)
    ]


def categories():
    return sorted({item.category for item in REGISTRY.values()})


def orientation_of(size):
    """Classify an output size: wider than 6:5 is landscape, taller than 5:6 portrait."""

    ratio = size[0] / size[1]
    return "landscape" if ratio > 1.2 else "portrait" if ratio < 1 / 1.2 else "square"


class Scene:
    """Inputs plus the scaled drawing helpers for one render."""

    def __init__(
        self,
        image,
        text,
        name,
        handle,
        size,
        family: FontFamily,
        images,
        attachment=False,
        date=None,
        *,
        name_file=None,
        latin_file=None,
        classic=False,
        design=DESIGN,
        seed=None,
    ):
        self.image = image
        self.text = text
        self.name = name.strip()
        self.handle = handle.strip().lstrip("@")
        self.size = size
        self.w, self.h = size
        self.design = design
        self.orientation = next(
            (k for k, v in DESIGNS.items() if v == tuple(design)), "landscape"
        )
        self.seed = seed
        self.kx, self.ky = self.w / design[0], self.h / design[1]
        self.k = min(self.kx, self.ky)
        self.family = family
        self.images = images
        self.attachment = attachment
        self.date = date or datetime.now()
        self.name_file = name_file
        self.latin_file = latin_file
        self.classic = classic
        # Decorations depend on the quote only, so a re-render is identical.
        self.rng = random.Random(zlib.crc32(text.encode()))
        # ASCII art is painted as one block; the normal text path is used when
        # the AA faces are unavailable or cannot draw the text.
        try:
            from miq_aa import detect

            self.aa = detect(text, bundled_fonts())
        except (OSError, ValueError):
            self.aa = None

    # ----- scaling -------------------------------------------------------
    def X(self, v):
        return v * self.kx

    def Y(self, v):
        return v * self.ky

    def S(self, v):
        return v * self.k

    def box(self, values):
        return tuple(
            round(v * (self.kx if i % 2 == 0 else self.ky))
            for i, v in enumerate(values)
        )

    @property
    def handle_text(self):
        return "@" + self.handle if self.handle else ""

    @property
    def has_credit(self):
        return bool(self.name or self.handle)

    @property
    def date_text(self):
        return self.date.strftime("%Y.%m.%d")

    @cached_property
    def has_pictures(self):
        return any(kind == "emoji" for _, kind in runs(self.text, True))

    # ----- fonts ---------------------------------------------------------
    def quote_font(self, px):
        family = self.family
        latin = font_at(self.latin_file, px) if self.latin_file else None
        return MixedFont(
            font_at(family.path, px, family.body),
            self.images.path,
            images=self.images,
            editorial=not self.latin_file,
            latin=latin,
        )

    def name_font(self, px):
        path = self.name_file or self.family.path
        weight = None if self.name_file else self.family.name
        return MixedFont(
            font_at(path, px, weight), self.images.path, images=self.images
        )

    def handle_font(self, px):
        return MixedFont(
            font_at(self.family.path, px, self.family.handle),
            self.images.path,
            images=self.images,
        )

    # ----- text ----------------------------------------------------------
    def fit(self, box, maximum, text=None):
        if self.aa:
            from miq_text import AALayout

            return AALayout(
                self.aa,
                bundled_fonts(),
                box,
                maximum * self.k * self.family.scale,
            )
        return fit(
            self.text if text is None else text,
            self.quote_font,
            box[2] - box[0],
            box[3] - box[1],
            maximum * self.k * self.family.scale,
        )

    def write(
        self,
        canvas,
        design_box,
        fill,
        maximum,
        *,
        align="left",
        valign="middle",
        result=None,
        region=None,
    ):
        """Fit the quote into a design-space box and paint it."""

        region = region or self.box(design_box)
        result = result or self.fit(region, maximum)
        paint(canvas, result, region, fill, align=align, valign=valign)
        return result, region

    def text_mask(self, result, region, *, align="left", valign="middle"):
        """The quote as an ``L`` mask (pictures excluded) for effects."""

        layer = Image.new("RGBA", self.size, (0, 0, 0, 0))
        paint(
            layer,
            result,
            region,
            (255, 255, 255, 255),
            align=align,
            valign=valign,
            emoji=False,
        )
        return layer.getchannel("A")

    def pictures(self, canvas, result, region, *, align="left", valign="middle"):
        """Paint only the emoji of the quote (text drawn fully transparent)."""

        if self.has_pictures and not self.aa:
            paint(canvas, result, region, (0, 0, 0, 0), align=align, valign=valign)

    def block(self, result, region, align="left", valign="middle"):
        """The painted extent ``(l, t, r, b)`` of a fitted quote."""

        width = max(
            (result.font.getlength(row) * result.scale for row in result.lines),
            default=0,
        )
        height = result.height
        spare = region[3] - region[1] - height
        top = region[1] + (
            0 if valign == "top" else spare if valign == "bottom" else spare / 2
        )
        if align == "center":
            left = (region[0] + region[2] - width) / 2
        elif align == "right":
            left = region[2] - width
        else:
            left = region[0]
        return left, top, left + width, top + height

    def line(
        self,
        canvas,
        text,
        xy,
        px,
        fill,
        *,
        kind="name",
        anchor="left",
        width=None,
    ):
        """A single baseline-anchored line in design coordinates."""

        if not text:
            return
        maker = {
            "name": self.name_font,
            "handle": self.handle_font,
            "quote": self.quote_font,
        }[kind]
        x, y = self.X(xy[0]), self.Y(xy[1])
        limit = self.X(width) if width else self.w
        credit(
            canvas,
            text,
            maker,
            max(1, round(px * self.k * self.family.scale)),
            (x, y),
            limit,
            fill,
            baseline=True,
            anchor=anchor,
        )

    def credits(
        self,
        canvas,
        xy,
        fill,
        secondary,
        *,
        anchor="left",
        name_px=46,
        handle_px=32,
        gap=60,
        width=700,
        prefix="",
        handle=True,
    ):
        """Name above handle; a missing name lets the handle take its place."""

        y = xy[1]
        if self.name:
            self.line(
                canvas,
                prefix + self.name,
                (xy[0], y),
                name_px,
                fill,
                anchor=anchor,
                width=width,
            )
            y += gap
        if handle and self.handle:
            self.line(
                canvas,
                self.handle_text,
                (xy[0], y),
                handle_px,
                secondary,
                kind="handle",
                anchor=anchor,
                width=width,
            )

    # ----- shapes --------------------------------------------------------
    def place(self, canvas, image, design_box, *, radius=0, circle=False, cover=None):
        """Paste ``image`` into a design box (cropped unless an attachment)."""

        l, t, r, b = self.box(design_box)
        if circle:
            # Round slots stay round whatever the output aspect ratio.
            side = min(r - l, b - t)
            l, t = (l + r - side) // 2, (t + b - side) // 2
            r, b = l + side, t + side
        cover = (not self.attachment) if cover is None else cover
        image = (
            fx.cover(image, (r - l, b - t))
            if cover
            else fx.contain(image, (r - l, b - t))
        )
        x = l + (r - l - image.width) // 2
        y = t + (b - t - image.height) // 2
        if circle:
            image = fx.masked(image, fx.ellipse_mask(image.size))
        elif radius:
            image = fx.masked(image, fx.round_mask(image.size, self.S(radius)))
        canvas.alpha_composite(image, (x, y))
        return x, y, x + image.width, y + image.height

    def rect(
        self, canvas, design_box, fill, *, radius=0, outline=None, width=0, scaled=True
    ):
        l, t, r, b = self.box(design_box) if scaled else tuple(map(round, design_box))
        w, h = max(1, r - l), max(1, b - t)
        if outline and width:
            # A filled outer shape with the fill inset by the stroke width.
            stroke = max(1, round(self.S(width)))
            outer = fx.round_mask((w, h), self.S(radius))
            canvas.alpha_composite(fx.colored(outer, outline), (l, t))
            l, t, w, h = l + stroke, t + stroke, w - 2 * stroke, h - 2 * stroke
            radius = max(0, radius - width)
        if fill is not None and w > 0 and h > 0:
            canvas.alpha_composite(
                fx.colored(fx.round_mask((w, h), self.S(radius)), fill), (l, t)
            )
        return (l, t, r, b)

    def frame(self, canvas, design_box, color, width, *, radius=0):
        """An outline-only rounded rectangle."""

        l, t, r, b = self.box(design_box)
        w, h = max(1, r - l), max(1, b - t)
        stroke = max(1, round(self.S(width)))
        outer = fx.round_mask((w, h), self.S(radius))
        inner = fx.round_mask(
            (max(1, w - 2 * stroke), max(1, h - 2 * stroke)),
            max(0, self.S(radius) - stroke),
        )
        ring = outer.copy()
        hole = Image.new("L", (w, h), 0)
        hole.paste(inner, (stroke, stroke))
        from PIL import ImageChops

        ring = ImageChops.subtract(ring, hole)
        canvas.alpha_composite(fx.colored(ring, color), (l, t))

    def vector(self, canvas, color, painter, design_box=None, factor=4):
        """Antialiased polygons: ``painter(draw, P, S)`` in design coordinates."""

        l, t, r, b = self.box(design_box) if design_box else (0, 0, self.w, self.h)
        l, t = max(0, l), max(0, t)
        r, b = min(self.w, r), min(self.h, b)
        if r <= l or b <= t:
            return
        mask = Image.new("L", ((r - l) * factor, (b - t) * factor))

        def P(x, y):
            return ((self.X(x) - l) * factor, (self.Y(y) - t) * factor)

        def S(v):
            return self.S(v) * factor

        painter(ImageDraw.Draw(mask), P, S)
        mask = mask.resize((r - l, b - t), Image.Resampling.BOX)
        canvas.alpha_composite(fx.colored(mask, color), (l, t))

    def hline(self, canvas, x0, x1, y, color, width=2):
        stroke = max(1, round(self.S(width)))
        top = round(self.Y(y) - stroke / 2)
        _bar(canvas, (round(self.X(x0)), top, round(self.X(x1)), top + stroke), color)

    def vline(self, canvas, x, y0, y1, color, width=2):
        stroke = max(1, round(self.S(width)))
        left = round(self.X(x) - stroke / 2)
        _bar(canvas, (left, round(self.Y(y0)), left + stroke, round(self.Y(y1))), color)


def _bar(canvas, box, color):
    """Blend a solid rectangle (end-exclusive box) over just the pixels it covers."""

    l, t = max(0, box[0]), max(0, box[1])
    r, b = min(canvas.width, box[2]), min(canvas.height, box[3])
    if r > l and b > t:
        color = fx.rgb(color) if isinstance(color, str) else tuple(color)
        color = color if len(color) == 4 else (*color, 255)
        canvas.alpha_composite(Image.new("RGBA", (r - l, b - t), color), (l, t))


def fill(size, color):
    return Image.new("RGBA", size, fx.rgb(color) + (255,))


# Importing the modules registers their layouts.
import miq_designs  # noqa: E402,F401
import miq_neta  # noqa: E402,F401
