"""Joke layouts (ネタ枠): big pictures, big type, loud fonts.

Every layout here draws landscape, portrait and square. Coordinates are in the
design space of the orientation being drawn (1920x1080, 1080x1920 or
1080x1080); ``_by(s, landscape, portrait, square)`` picks the right set.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageOps

import miq_effects as fx
from miq_fonts import BLACKLETTER, font_at
from miq_layouts import fill, layout

ALL = ("landscape", "portrait", "square")


def _by(s, landscape, portrait, square):
    return {"landscape": landscape, "portrait": portrait, "square": square}[
        s.orientation
    ]


@lru_cache(maxsize=32)
def _ramp(size, start, end, axis):
    """``L`` mask: opaque before ``start``, clear after ``end`` (pixels), smooth.

    Cached per size: callers only read it."""

    start, end = round(start), round(end)
    length = size[0] if axis == "x" else size[1]
    span = max(1.0, end - start)
    values = []
    for i in range(length):
        t = min(1.0, max(0.0, (i - start) / span))
        values.append(round(255 * (1 - t * t * (3 - 2 * t))))
    line = Image.new("L", (length, 1) if axis == "x" else (1, length))
    line.putdata(values)
    return line.resize(size, Image.Resampling.NEAREST)


def _photo(s, canvas, design_box, *, fade=None, gray=False, centering=(0.5, 0.35)):
    """Cover a design box with the picture; ``fade=(axis, start, end)`` in design
    units dissolves it into the background."""

    l, t, r, b = s.box(design_box)
    source = fx.grayscale(s.image, contrast=1.15) if gray else s.image
    photo = fx.cover(source, (r - l, b - t), centering)
    if fade:
        axis, start, end = fade
        if axis == "x":
            start, end = s.X(start) - l, s.X(end) - l
        else:
            start, end = s.Y(start) - t, s.Y(end) - t
        photo.putalpha(
            ImageChops.multiply(
                photo.getchannel("A"), _ramp(photo.size, start, end, axis)
            )
        )
    canvas.alpha_composite(photo, (l, t))
    return l, t, r, b


def _strokes(canvas, mask, strokes, fill_color=None):
    """Outline ``mask`` (``strokes``: outermost first, ``(color, width_px)``)
    and fill it, working only on the area the lettering covers."""

    box = mask.getbbox()
    if box is None:
        return
    pad = round(max((w for _, w in strokes), default=0) * 1.5) + 2
    l, t = max(0, box[0] - pad), max(0, box[1] - pad)
    r, b = min(mask.width, box[2] + pad), min(mask.height, box[3] + pad)
    part = mask.crop((l, t, r, b))
    for color, width in strokes:
        canvas.alpha_composite(fx.colored(fx.outline(part, width), color), (l, t))
    if fill_color is not None:
        canvas.alpha_composite(fx.colored(part, fill_color), (l, t))


def _stroke_size(result, factor):
    size = getattr(result, "size", None) or getattr(result, "px", 0)
    return max(1.0, size * factor)


def _lettering(s, canvas, design_box, fill_color, maximum, strokes=(), **where):
    """The quote with outlines (``strokes``: outermost first, as ``(color, k)``
    where ``k`` is the stroke width relative to the type size)."""

    region = s.box(design_box)
    result = s.fit(region, maximum)
    if not strokes:
        s.write(canvas, design_box, fill_color, maximum, result=result, **where)
        return result, region
    mask = s.text_mask(result, region, **where)
    _strokes(
        canvas,
        mask,
        [(color, _stroke_size(result, k)) for color, k in strokes],
        fill_color,
    )
    s.pictures(canvas, result, region, **where)
    return result, region


def _stroked_line(s, canvas, text, xy, px, color, stroke, width, *, anchor="left"):
    """One line of text with a solid outline (TV caption style)."""

    if not text:
        return
    layer = Image.new("RGBA", s.size, (0, 0, 0, 0))
    s.line(layer, text, xy, px, (255, 255, 255), anchor=anchor, width=width)
    _strokes(canvas, layer.getchannel("A"), [(stroke, s.S(px * 0.16))])
    s.line(canvas, text, xy, px, color, anchor=anchor, width=width)


def _credit_line(s):
    return "  ".join(part for part in (s.name, s.handle_text) if part)


# ---------------------------------------------------------------------------
# 34 news: "The Daily Quote" — halftone photo, huge serif quote
# ---------------------------------------------------------------------------


@layout(
    "news",
    "新聞",
    "cool",
    ("mincho", "shippori", "syuku"),
    number=34,
    orientations=ALL,
)
def news(s):
    paper, ink = (227, 224, 215), (24, 22, 20)
    canvas = fill(s.size, paper)
    fx.grain(canvas, 0.05)
    W, H = s.design
    title_px, title_y = _by(s, (118, 118), (112, 128), (100, 112))
    try:
        title = font_at(BLACKLETTER, title_px * s.k)
        ImageDraw.Draw(canvas).text(
            (s.X(W / 2), s.Y(title_y)),
            "The Daily Quote",
            font=title,
            fill=ink,
            anchor="ms",
        )
    except OSError:
        s.line(
            canvas,
            "The Daily Quote",
            (W / 2, title_y),
            title_px * 0.8,
            ink,
            anchor="center",
        )
    top = title_y + 32
    s.hline(canvas, 50, W - 50, top, ink, 5)
    s.hline(canvas, 50, W - 50, top + 12, ink, 2)
    row = top + 52
    s.line(
        canvas,
        f"No. {s.rng.randrange(1, 999)}",
        (60, row),
        26,
        ink,
        kind="handle",
        width=400,
    )
    s.line(
        canvas,
        "特報 · 発言録",
        (W / 2, row),
        26,
        ink,
        kind="handle",
        anchor="center",
        width=400,
    )
    s.line(
        canvas,
        "PRICE ¥0",
        (W - 60, row),
        26,
        ink,
        kind="handle",
        anchor="right",
        width=400,
    )
    s.hline(canvas, 50, W - 50, row + 22, ink, 3)
    photo, caption, rule, text = _by(
        s,
        ((60, 290, 640, 870), (60, 940), ("v", 700, 290, 1040), (760, 270, 1870, 1050)),
        (
            (120, 300, 960, 1140),
            (120, 1205),
            ("h", 60, 1020, 1240),
            (60, 1270, 1020, 1880),
        ),
        ((50, 280, 480, 710), (50, 770), ("v", 520, 280, 1040), (560, 260, 1040, 1050)),
    )
    l, t, r, b = s.box(photo)
    dots = fx.halftone(
        fx.cover(s.image, (r - l, b - t), (0.5, 0.35)), s.S(8), ink=ink, paper=paper
    )
    canvas.alpha_composite(dots, (l, t))
    credit = _credit_line(s)
    if credit:
        s.line(
            canvas,
            "— " + credit,
            caption,
            34,
            ink,
            kind="handle",
            width=photo[2] - photo[0],
        )
    if rule[0] == "v":
        s.vline(canvas, rule[1], rule[2], rule[3], ink, 4)
    else:
        s.hline(canvas, rule[1], rule[2], rule[3], ink, 4)
    s.write(canvas, text, ink, 240, align="left")
    return canvas


# ---------------------------------------------------------------------------
# 35 fade: the picture dissolves into black, big white type beside it
# ---------------------------------------------------------------------------


@layout(
    "fade",
    "フェード",
    "cool",
    ("gothic", "dela", "pop", "rocknroll"),
    number=35,
    orientations=ALL,
)
def fade(s):
    canvas = fill(s.size, (0, 0, 0))
    photo, ramp, text, x, rule = _by(
        s,
        ((0, 0, 1080, 1080), ("x", 560, 1080), (960, 100, 1850, 790), 1405, 860),
        ((0, 0, 1080, 1080), ("y", 620, 1080), (60, 1020, 1020, 1580), 540, 1660),
        ((0, 0, 720, 1080), ("x", 300, 720), (520, 90, 1040, 760), 780, 830),
    )
    _photo(s, canvas, photo, fade=ramp)
    s.write(canvas, text, (255, 255, 255), 200, align="center")
    accent = fx.accent(s.image, value=(0.8, 1.0))
    if fx.luminance(accent) < 0.35:
        accent = (250, 204, 21)
    s.hline(canvas, x - 55, x + 55, rule, accent, 6)
    width = text[2] - text[0]
    if s.name:
        s.line(
            canvas,
            s.name,
            (x, rule + 85),
            52,
            (255, 255, 255),
            anchor="center",
            width=width,
        )
    if s.handle:
        s.line(
            canvas,
            s.handle_text,
            (x, rule + (150 if s.name else 85)),
            30,
            (150, 150, 150),
            kind="handle",
            anchor="center",
            width=width,
        )
    return canvas


# ---------------------------------------------------------------------------
# 36 brush: grey photo into black, huge vertical brush lettering
# ---------------------------------------------------------------------------


@layout(
    "brush",
    "筆",
    "cool",
    ("syuku", "shippori", "mincho"),
    number=36,
    orientations=ALL,
)
def brush(s):
    from miq_vertical import draw_vertical, suits_vertical

    canvas = fill(s.size, (0, 0, 0))
    photo, ramp, text, x, y = _by(
        s,
        ((0, 0, 1000, 1080), ("x", 460, 1000), (1060, 70, 1840, 840), 1450, 935),
        ((0, 0, 1080, 1000), ("y", 520, 1000), (100, 860, 980, 1650), 540, 1745),
        ((0, 0, 620, 1080), ("x", 260, 620), (590, 60, 1030, 840), 810, 930),
    )
    _photo(s, canvas, photo, fade=ramp, gray=True)
    white = (250, 250, 248)
    if s.aa or not suits_vertical(s.text):
        s.write(canvas, text, white, 220, align="center")
    else:
        draw_vertical(canvas, s, s.box(text), white, max_px=300 * s.k * s.family.scale)
    width = text[2] - text[0]
    if s.name:
        s.line(canvas, "- " + s.name, (x, y), 46, white, anchor="center", width=width)
    if s.handle:
        s.line(
            canvas,
            s.handle_text,
            (x, y + (64 if s.name else 0)),
            28,
            (140, 140, 140),
            kind="handle",
            anchor="center",
            width=width,
        )
    return canvas


# ---------------------------------------------------------------------------
# 37 telop: a variety-show caption over the picture
# ---------------------------------------------------------------------------

TAGS = (
    "衝撃発言",
    "問題発言",
    "本人談",
    "速報",
    "名言",
    "迷言",
    "ご本人",
    "※個人の感想です",
)


@lru_cache(maxsize=8)
def _dusk(size):
    """Darkens the lower part so the caption reads over any picture."""

    ramp = _ramp(size, size[1] * 0.42, size[1], "y")
    return fx.colored(ImageOps.invert(ramp).point(lambda v: v * 150 // 255), (0, 0, 0))


TELOP = (
    # fill, inner stroke, outer stroke
    ((255, 241, 0), (0, 0, 0), (255, 255, 255)),
    ((255, 255, 255), (214, 0, 34), (255, 255, 255)),
    ((255, 255, 255), (0, 0, 0), (0, 154, 255)),
    ((255, 120, 190), (255, 255, 255), (120, 0, 70)),
)


@layout(
    "telop",
    "テロップ",
    "neta",
    ("dela", "pop", "potta", "rocknroll"),
    number=37,
    orientations=ALL,
)
def telop(s):
    canvas = fill(s.size, (0, 0, 0))
    W, H = s.design
    _photo(s, canvas, (0, 0, W, H), centering=(0.5, 0.45))
    canvas.alpha_composite(_dusk(s.size))
    tag = s.rng.choice(TAGS)
    face, inner, outer = s.rng.choice(TELOP)
    font = s.name_font(max(1, round(56 * s.k * s.family.scale)))
    tag_w = min(W - 120, font.getlength(tag) / s.kx + 90)
    s.vector(
        canvas,
        (214, 0, 34),
        lambda d, P, S: d.polygon(
            [P(40, 40), P(40 + tag_w + 30, 40), P(40 + tag_w, 140), P(40, 140)],
            fill=255,
        ),
        (36, 36, 44 + tag_w + 30, 144),
    )
    s.line(canvas, tag, (80, 114), 56, (255, 255, 255), width=tag_w - 40)
    if s.name:
        _stroked_line(
            s,
            canvas,
            s.name + "さん",
            (W - 50, 115),
            54,
            (255, 255, 255),
            (0, 0, 0),
            W * 0.55,
            anchor="right",
        )
    text = _by(s, (50, 470, 1870, 1050), (40, 1020, 1040, 1880), (40, 480, 1040, 1050))
    _lettering(
        s,
        canvas,
        text,
        face,
        230,
        ((outer, 0.24), (inner, 0.12)),
        align="center",
        valign="bottom",
    )
    return canvas


# ---------------------------------------------------------------------------
# 38 zatsu: a sloppy MS-Paint collage (雑コラ)
# ---------------------------------------------------------------------------

MARKERS = ((230, 0, 18), (0, 70, 230), (20, 20, 20), (0, 150, 60))


def _wobble(rng, points, amount):
    return [
        (x + rng.uniform(-amount, amount), y + rng.uniform(-amount, amount))
        for x, y in points
    ]


@layout(
    "zatsu",
    "雑コラ",
    "neta",
    ("pop", "magic", "hachimaru", "potta"),
    number=38,
    orientations=ALL,
)
def zatsu(s):
    rng = s.rng
    canvas = fill(s.size, (255, 255, 255))
    icon, text, arrow, name = _by(
        s,
        (
            (70, 150, 830, 910),
            (880, 90, 1860, 900),
            ((1000, 780), (800, 640)),
            (1370, 1000),
        ),
        (
            (170, 90, 910, 830),
            (50, 960, 1030, 1760),
            ((330, 990), (330, 840)),
            (540, 1850),
        ),
        (
            (40, 140, 520, 620),
            (550, 50, 1050, 860),
            ((620, 760), (500, 640)),
            (790, 980),
        ),
    )
    l, t, r, b = s.box(icon)
    photo = fx.fitted(s.image, (r - l, b - t), s.attachment).convert("RGBA")
    angle = rng.uniform(-9, 9)
    photo = photo.rotate(angle, Image.Resampling.NEAREST, expand=True)
    x = (l + r - photo.width) // 2
    y = (t + b - photo.height) // 2
    canvas.alpha_composite(photo, (x, y))
    red = (230, 0, 18)
    width = max(2, round(s.S(9)))
    # A red marker circle drawn in one hasty stroke (it overshoots).
    cx, cy = (l + r) / 2, (t + b) / 2 - (b - t) * 0.08
    rx, ry = (r - l) * 0.36, (b - t) * 0.34
    start = rng.uniform(0, math.tau)
    ring = []
    for i in range(80):
        a = start + i / 80 * math.tau * 1.12
        grow = 1 + 0.06 * math.sin(a * 2 + start) + i * 0.0012
        ring.append((cx + math.cos(a) * rx * grow, cy + math.sin(a) * ry * grow))
    draw = ImageDraw.Draw(canvas)
    draw.line(ring, fill=red, width=width, joint="curve")
    # A wobbly arrow from the text to the picture.
    (ax, ay), (bx, by) = [(s.X(px), s.Y(py)) for px, py in arrow]
    path = _wobble(
        rng,
        [(ax + (bx - ax) * i / 6, ay + (by - ay) * i / 6) for i in range(7)],
        s.S(10),
    )
    path[-1] = (bx, by)
    draw.line(path, fill=red, width=width, joint="curve")
    head = math.atan2(by - path[-2][1], bx - path[-2][0])
    for side in (-0.55, 0.55):
        draw.line(
            [
                (bx, by),
                (
                    bx - math.cos(head + side) * s.S(60),
                    by - math.sin(head + side) * s.S(60),
                ),
            ],
            fill=red,
            width=width,
        )
    # The quote, slightly crooked, in a marker colour.
    region = s.box(text)
    result = s.fit(region, 230)
    layer = Image.new("RGBA", s.size, (0, 0, 0, 0))
    s.write(layer, text, rng.choice(MARKERS), 230, align="center", result=result)
    tilt = rng.uniform(-4, 4)
    # Rotate just the lettering (with room to turn), not the whole canvas.
    box = layer.getbbox()
    if box:
        grow = round((box[2] - box[0] + box[3] - box[1]) * 0.05) + 4
        box = (
            max(0, box[0] - grow),
            max(0, box[1] - grow),
            min(s.w, box[2] + grow),
            min(s.h, box[3] + grow),
        )
        part = layer.crop(box).rotate(tilt, Image.Resampling.BICUBIC)
        canvas.alpha_composite(part, box[:2])
    if s.name:
        s.line(
            canvas, f"（{s.name}）", name, 44, (20, 20, 20), anchor="center", width=900
        )
    return canvas


# ---------------------------------------------------------------------------
# 39 wanted: a bounty poster
# ---------------------------------------------------------------------------

BOUNTIES = (
    "¥3,000,000,000",
    "¥500",
    "¥0",
    "うまい棒10本",
    "時価",
    "¥8,000,000,000",
    "ジュース1本",
    "¥1,500,000",
)


@layout(
    "wanted",
    "手配書",
    "neta",
    ("shippori", "mincho", "dela", "syuku"),
    number=39,
    orientations=ALL,
)
def wanted(s):
    canvas = fx.radial(
        s.size, (238, 220, 178), (176, 140, 88), center=(0.5, 0.45), radius=0.85
    ).convert("RGBA")
    fx.grain(canvas, 0.08)
    ink = (62, 38, 20)
    W, H = s.design
    s.frame(canvas, (22, 22, W - 22, H - 22), ink, 7)
    s.frame(canvas, (40, 40, W - 40, H - 40), ink, 2)
    spec = _by(
        s,
        dict(
            title=(470, 215, 170, 820),
            photo=(200, 255, 740, 795),
            dead=(470, 875, 50),
            name=(470, 970, 56),
            crime=(1360, 150),
            quote=(900, 180, 1830, 820),
            rule=(960, 1760, 850),
            bounty=(1360, 920, 1025, 820),
        ),
        dict(
            title=(540, 245, 220, 960),
            photo=(190, 300, 890, 1000),
            dead=(540, 1080, 56),
            name=(540, 1165, 60),
            crime=(540, 1240),
            quote=(80, 1270, 1000, 1640),
            rule=(140, 940, 1665),
            bounty=(540, 1730, 1840, 960),
        ),
        dict(
            title=(540, 185, 150, 980),
            photo=(70, 225, 500, 655),
            dead=(285, 725, 40),
            name=(285, 800, 46),
            crime=(790, 255),
            quote=(540, 280, 1030, 790),
            rule=(560, 1010, 835),
            bounty=(540, 905, 1010, 960),
        ),
    )
    x, y, px, width = spec["title"]
    s.line(canvas, "WANTED", (x, y), px, ink, anchor="center", width=width)
    l, t, r, b = s.box(spec["photo"])
    photo = fx.duotone(
        fx.cover(s.image, (r - l, b - t), (0.5, 0.35)), ink, (240, 224, 184)
    )
    canvas.alpha_composite(photo, (l, t))
    s.frame(canvas, spec["photo"], ink, 6)
    x, y, px = spec["dead"]
    s.line(canvas, "DEAD OR ALIVE", (x, y), px, ink, anchor="center", width=640)
    x, y, px = spec["name"]
    label = s.name or s.handle_text
    if label:
        s.line(canvas, label, (x, y), px, ink, anchor="center", width=640)
    x, y = spec["crime"]
    s.line(canvas, "― 罪状 ―", (x, y), 38, ink, anchor="center", width=600)
    s.write(canvas, spec["quote"], ink, 170, align="center")
    s.hline(canvas, *spec["rule"], ink, 3)
    x, label_y, y, width = spec["bounty"]
    s.line(canvas, "懸賞金", (x, label_y), 38, ink, anchor="center", width=600)
    s.line(
        canvas,
        s.rng.choice(BOUNTIES),
        (x, y),
        96,
        (150, 24, 18),
        anchor="center",
        width=width,
    )
    return canvas


# ---------------------------------------------------------------------------
# 40 cutout (miq 切り抜き): the character steps out in front of huge type
# ---------------------------------------------------------------------------


POSTER = (
    (255, 214, 0),
    (255, 92, 138),
    (0, 190, 255),
    (255, 120, 0),
    (120, 220, 60),
    (150, 90, 255),
    (240, 240, 236),
    (30, 30, 36),
)


@layout(
    "cutout",
    "切り抜き",
    "neta",
    ("dela", "pop", "potta", "gothic"),
    number=40,
    orientations=ALL,
    hidden=True,
)
def cutout(s):
    from miq_cutout import cutout as cut

    subject, removed = cut(s.image)
    # A loud flat colour, as far as possible from the character's own colours.
    own = fx.palette(subject if removed else s.image, 4)
    background = max(
        POSTER,
        key=lambda c: min(sum((a - b) ** 2 for a, b in zip(c, o)) for o in own),
    )
    canvas = fx.gradient(
        s.size,
        [fx.shade(background, value=1.12), fx.shade(background, value=0.78)],
        90,
    ).convert("RGBA")
    ink = fx.ink_for(background)
    depth = fx.shade(background, value=0.55)
    W, H = s.design
    text, slot, label = _by(
        s,
        # The figure overlaps only the edge of the text: depth, yet all readable.
        ((50, 110, 1250, 1050), (1060, 40, 1910, 1080), (60, 80)),
        ((40, 110, 1040, 900), (60, 760, 1020, 1920), (50, 80)),
        ((40, 110, 640, 1050), (600, 260, 1080, 1080), (50, 75)),
    )
    if not removed:
        # Nothing to cut out: show the whole picture as a sticker instead.
        slot = _by(
            s, (1240, 300, 1840, 900), (240, 1180, 840, 1780), (640, 500, 1040, 900)
        )
    region = s.box(text)
    result = s.fit(region, 330)
    mask = s.text_mask(result, region, align="left", valign="middle")
    offset = round(s.S(10))
    shadow = Image.new("L", s.size)
    shadow.paste(mask, (offset, offset))
    canvas.alpha_composite(fx.colored(shadow, depth))
    canvas.alpha_composite(fx.colored(mask, ink))
    s.pictures(canvas, result, region, align="left", valign="middle")
    # The character in front: as tall as its slot, standing on the bottom edge.
    l, t, r, b = s.box(slot)
    if removed:
        figure = fx.contain(subject, (r - l, b - t))
        x = (l + r - figure.width) // 2
        y = b - figure.height
    else:
        figure = fx.cover(s.image, (r - l, b - t))
        figure = fx.masked(figure, fx.round_mask(figure.size, s.S(40)))
        x, y = l, t
    # Room around the figure for its white sticker edge.
    pad = round(s.S(16))
    padded = Image.new("RGBA", (figure.width + 2 * pad, figure.height + 2 * pad))
    padded.paste(figure, (pad, pad))
    figure, x, y = padded, x - pad, y - pad
    edge = fx.outline(figure.getchannel("A"), s.S(12))
    fx.shadow(canvas, edge, (x, y), blur=s.S(22), opacity=0.35, offset=(0, s.S(12)))
    canvas.alpha_composite(fx.colored(edge, (255, 255, 255)), (x, y))
    canvas.alpha_composite(figure, (x, y))
    credit = _credit_line(s)
    if credit:
        s.line(canvas, credit, label, 40, ink, width=W * 0.6)
    return canvas
