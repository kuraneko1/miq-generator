"""Two-person exchanges: ``versus`` (ビリビリ), ``love`` (ハート) and ``talk`` (LINE).

``left`` is the earlier speaker (the message that was replied to) and
``right`` the reply. Landscape puts them side by side, portrait stacks them.

    from miq_dialogue import Speaker, render_dialogue_bytes
    png, mood, face = render_dialogue_bytes(
        Speaker(avatar_b, "先に言った方", "B"), Speaker(avatar_a, "返信", "A"), "vs"
    )
"""

from __future__ import annotations

import io
import math
import os
import random
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageDraw

import miq
import miq_effects as fx
from miq_discord import clean
from miq_fonts import FAMILIES, custom_family, family, find_font, font_at
from miq_layouts import DESIGNS, Scene, orientation_of
from miq_text import EmojiImages, paint

MOODS = {
    "vs": ("対戦", ("dela", "rocknroll", "gothic")),
    "love": ("ラブ", ("maru", "rocknroll", "klee")),
    "talk": ("LINE", ("gothic", "maru", "rocknroll")),
}


@dataclass
class Speaker:
    image: object
    text: str
    name: str = ""
    handle: str = ""
    attachment: bool = False
    custom_emojis: dict = field(default_factory=dict)
    # When the message was sent (shown by ``talk``); defaults to ``date``.
    date: datetime | None = None


def _choose_family(mood, font_family, rng):
    if font_family not in (None, "", "auto"):
        return family(font_family)
    choices = [k for k in MOODS[mood][1] if FAMILIES[k].installed()] or [
        k for k, f in FAMILIES.items() if f.installed()
    ]
    if not choices:
        raise ValueError(
            "標準フォントがありません。python scripts/setup_fonts.py を実行してください。"
        )
    return family(choices[min(int(rng.random() * len(choices)), len(choices) - 1)])


def compose_dialogue(
    left: Speaker,
    right: Speaker,
    mood="talk",
    *,
    size=None,
    orientation=None,
    seed=None,
    font_family="auto",
    font_file=None,
    emoji_font_file=None,
    mentions=None,
    custom_emojis=None,
    markup=True,
    date=None,
    fast=False,
):
    """Draw the exchange and return ``(image, mood, font_family)``.

    ``orientation`` is ``landscape`` (default) or ``portrait``.
    """

    if mood not in MOODS:
        raise ValueError(f"不明な掛け合い: {mood}（{', '.join(MOODS)}）")
    speakers = []
    for speaker in (left, right):
        text = clean(speaker.text, mentions) if markup else speaker.text
        name = clean(speaker.name, mentions) if markup else speaker.name
        if not text.strip():
            raise ValueError("本文が空です。")
        speakers.append((speaker, text, name))
    explicit = size not in (None, "")
    if orientation is None:
        orientation = (
            orientation_of(miq.resolve_size(size)) if explicit else "landscape"
        )
    if orientation not in ("landscape", "portrait"):
        raise ValueError("掛け合いは横か縦だけに対応しています。")
    portrait = orientation == "portrait"
    design = DESIGNS["portrait" if portrait else "landscape"]
    target = (
        miq.resolve_size(size)
        if explicit
        else miq.default_size("portrait" if portrait else "landscape")
    )
    rng = random.Random(seed) if seed is not None else random.Random()
    body_file = font_file or os.getenv("MIQ_FONT")
    chosen = (
        custom_family(find_font(body_file))
        if body_file
        else _choose_family(mood, font_family, rng)
    )
    emojis = dict(custom_emojis or {})
    for speaker, _, _ in speakers:
        emojis.update(speaker.custom_emojis or {})
    images = EmojiImages(miq._emoji_font(emoji_font_file), emojis)
    factor = 1 if fast else min(2.0, max(1.0, design[0] / target[0]))
    if factor > 1.5 and target[0] * 2 <= max(design[0], 1280):
        factor = 2
    work = (round(target[0] * factor), round(target[1] * factor))
    scenes = [
        Scene(
            miq._open_image(speaker.image),
            text,
            name,
            speaker.handle,
            work,
            chosen,
            images,
            speaker.attachment,
            speaker.date or date or datetime.now(),
            design=design,
            seed=seed,
        )
        for speaker, text, name in speakers
    ]
    draw = {"vs": versus, "love": love, "talk": talk}[mood]
    canvas = draw(*scenes, portrait)
    if canvas.size != target:
        if canvas.size == (target[0] * 2, target[1] * 2):
            canvas = canvas.convert("RGB").reduce(2)
        else:
            canvas = canvas.resize(target, Image.Resampling.LANCZOS)
    return canvas, mood, chosen.key


def render_dialogue_bytes(left, right, mood="talk", **options):
    """PNG bytes plus what was drawn: ``(png, mood, font_family)``."""

    canvas, mood, face = compose_dialogue(left, right, mood, **options)
    buffer = io.BytesIO()
    miq._save(canvas, buffer)
    return buffer.getvalue(), mood, face


# ---------------------------------------------------------------------------
# shared pieces
# ---------------------------------------------------------------------------


def _fit_pair(a, b, regions, maximum):
    """Fit both quotes, then refit the larger one so both share a type size."""

    ra, rb = a.fit(regions[0], maximum), b.fit(regions[1], maximum)
    sa, sb = getattr(ra, "size", None), getattr(rb, "size", None)
    if sa and sb and abs(sa - sb) > 0.5:
        if sa > sb:
            ra = a.fit(regions[0], sb / (a.k * a.family.scale))
        else:
            rb = b.fit(regions[1], sa / (b.k * b.family.scale))
    return ra, rb


def _extent(result):
    width = max(
        (result.font.getlength(row) * result.scale for row in result.lines), default=0
    )
    return width, result.height


def bubble(
    s,
    canvas,
    region,
    result,
    color,
    ink,
    *,
    side="left",
    valign="top",
    pad=44,
    radius=40,
    outline=None,
    tail=None,
    shadow=(90, 110, 160),
):
    """A speech bubble sized to ``result`` inside the pixel ``region``."""

    p = s.S(pad)
    width, height = _extent(result)
    bw, bh = round(width + 2 * p), round(height + 2 * p)
    l = region[0] if side == "left" else region[2] - bw
    spare = region[3] - region[1] - bh
    t = region[1] + (
        0 if valign == "top" else spare if valign == "bottom" else spare // 2
    )
    l, t = round(l), round(t)
    mask = fx.round_mask((bw, bh), s.S(radius))
    if shadow:
        fx.shadow(
            canvas,
            mask,
            (l, t),
            blur=s.S(18),
            color=shadow,
            opacity=0.2,
            offset=(0, s.S(8)),
        )
    if tail:
        # ``tail`` is the design-space point the bubble points at.
        tx, ty = s.X(tail[0]), s.Y(tail[1])
        cx = min(max(tx, l + s.S(70)), l + bw - s.S(70))
        cy = t + bh / 2
        base = s.S(64)
        if outline:
            _triangle(canvas, (tx, ty), (cx, cy), base + s.S(8), outline)
        _triangle(canvas, (tx, ty), (cx, cy), base, color)
    if outline:
        grow = round(s.S(6))
        ring = fx.round_mask((bw + 2 * grow, bh + 2 * grow), s.S(radius) + grow)
        canvas.alpha_composite(fx.colored(ring, outline), (l - grow, t - grow))
    canvas.alpha_composite(fx.colored(mask, color), (l, t))
    inner = (round(l + p), round(t + p), round(l + bw - p), round(t + bh - p))
    paint(canvas, result, inner, ink)
    return l, t, l + bw, t + bh


def _triangle(canvas, tip, toward, base, color):
    """An antialiased tail from ``tip`` to a base centred on ``toward``."""

    dx, dy = toward[0] - tip[0], toward[1] - tip[1]
    length = math.hypot(dx, dy) or 1
    nx, ny = -dy / length * base / 2, dx / length * base / 2
    points = [tip, (toward[0] + nx, toward[1] + ny), (toward[0] - nx, toward[1] - ny)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    l, t = max(0, math.floor(min(xs))), max(0, math.floor(min(ys)))
    r, b = min(canvas.width, math.ceil(max(xs))), min(canvas.height, math.ceil(max(ys)))
    if r <= l or b <= t:
        return
    n = 4
    mask = Image.new("L", ((r - l) * n, (b - t) * n))
    ImageDraw.Draw(mask).polygon(
        [((x - l) * n, (y - t) * n) for x, y in points], fill=255
    )
    mask = mask.resize((r - l, b - t), Image.Resampling.BOX)
    canvas.alpha_composite(fx.colored(mask, color), (l, t))


def _avatar(s, canvas, box, ring=(255, 255, 255), ring_width=12, glow=None):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    r = (box[2] - box[0]) / 2
    outer = (
        cx - r - ring_width,
        cy - r - ring_width,
        cx + r + ring_width,
        cy + r + ring_width,
    )
    l, t, rr, b = s.box(outer)
    side = min(rr - l, b - t)
    disc = fx.ellipse_mask((side, side))
    ox, oy = round(s.X(cx) - side / 2), round(s.Y(cy) - side / 2)
    if glow:
        fx.shadow(canvas, disc, (ox, oy), blur=s.S(26), color=glow, opacity=0.55)
    canvas.alpha_composite(fx.colored(disc, ring), (ox, oy))
    s.place(canvas, s.image, box, circle=True, cover=True)


def _credit(s, canvas, xy, color, sub, anchor="left", width=600, px=40):
    y = xy[1]
    if s.name:
        s.line(canvas, s.name, (xy[0], y), px, color, anchor=anchor, width=width)
        y += px * 1.3
    if s.handle:
        s.line(
            canvas,
            s.handle_text,
            (xy[0], y),
            px * 0.7,
            sub,
            kind="handle",
            anchor=anchor,
            width=width,
        )


def _big_word(
    s, canvas, text, center, px, color, outline, stroke, rotate=0.0, glow=None
):
    """Heavy lettering (``VS``) with an outline, centred on a design point."""

    size = max(1, round(px * s.k))
    font = font_at(s.family.path, size, s.family.name)
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    pad = round(s.S(stroke)) * 3 + 4
    mask = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad))
    ImageDraw.Draw(mask).text(
        (pad - left, pad - top), text, font=font, fill=255, anchor="ls"
    )
    if rotate:
        mask = mask.rotate(rotate, Image.Resampling.BICUBIC, expand=True)
    edge = fx.outline(mask, s.S(stroke))
    x = round(s.X(center[0]) - mask.width / 2)
    y = round(s.Y(center[1]) - mask.height / 2)
    if glow:
        halo = fx.glow(edge, glow, s.S(24), 1.4)
        _paste(canvas, halo, (x, y))
    _paste(canvas, fx.colored(edge, outline), (x, y))
    _paste(canvas, fx.colored(mask, color), (x, y))


def _paste(canvas, layer, xy):
    x, y = xy
    sx, sy = max(0, -x), max(0, -y)
    canvas.alpha_composite(layer, (max(0, x), max(0, y)), (sx, sy))


def _lightning(
    s, canvas, start, end, width, rng, color=(255, 236, 90), core=(255, 255, 255)
):
    """A jagged bolt (with branches) from ``start`` to ``end`` in design units."""

    def bolt(a, b, steps, jitter):
        points = [a]
        for i in range(1, steps):
            t = i / steps
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            dx, dy = b[1] - a[1], -(b[0] - a[0])
            norm = math.hypot(dx, dy) or 1
            off = rng.uniform(-jitter, jitter) * (1 if i % 2 else -1)
            points.append((x + dx / norm * off, y + dy / norm * off))
        points.append(b)
        return points

    main = bolt(start, end, 11, 70)
    branches = []
    for i in range(2, len(main) - 2, 3):
        a = main[i]
        angle = math.atan2(end[1] - start[1], end[0] - start[0]) + rng.choice(
            (-1, 1)
        ) * rng.uniform(0.5, 1.0)
        reach = rng.uniform(140, 260)
        branches.append(
            bolt(
                a,
                (a[0] + math.cos(angle) * reach, a[1] + math.sin(angle) * reach),
                4,
                30,
            )
        )

    mask = Image.new("L", s.size)
    draw = ImageDraw.Draw(mask)
    P = lambda p: (s.X(p[0]), s.Y(p[1]))
    draw.line(
        [P(p) for p in main], fill=255, width=max(1, round(s.S(width))), joint="curve"
    )
    for branch in branches:
        draw.line(
            [P(p) for p in branch],
            fill=255,
            width=max(1, round(s.S(width * 0.45))),
            joint="curve",
        )
    canvas.alpha_composite(fx.glow(mask, color, s.S(30), 2.2))
    canvas.alpha_composite(fx.colored(mask, color))
    thin = Image.new("L", s.size)
    ImageDraw.Draw(thin).line(
        [P(p) for p in main],
        fill=255,
        width=max(1, round(s.S(width * 0.35))),
        joint="curve",
    )
    canvas.alpha_composite(fx.colored(thin, core))


def _focus_lines(s, canvas, center, count, color, inner, rng):
    """Manga speed lines radiating from ``center`` (design units)."""

    cx, cy = s.X(center[0]), s.Y(center[1])
    reach = math.hypot(s.w, s.h)
    mask = Image.new("L", s.size)
    draw = ImageDraw.Draw(mask)
    for _ in range(count):
        angle = rng.uniform(0, math.tau)
        start = s.S(inner) * rng.uniform(0.85, 1.25)
        spread = rng.uniform(0.002, 0.007)
        draw.polygon(
            [
                (cx + math.cos(angle) * start, cy + math.sin(angle) * start),
                (
                    cx + math.cos(angle - spread) * reach,
                    cy + math.sin(angle - spread) * reach,
                ),
                (
                    cx + math.cos(angle + spread) * reach,
                    cy + math.sin(angle + spread) * reach,
                ),
            ],
            fill=255,
        )
    canvas.alpha_composite(fx.colored(mask, color))


def _hearts(s, canvas, area, count, rng, colors):
    for _ in range(count):
        x, y = rng.uniform(area[0], area[2]), rng.uniform(area[1], area[3])
        size = rng.uniform(26, 80)
        color = rng.choice(colors)
        s.vector(
            canvas,
            color,
            lambda d, P, S, x=x, y=y, size=size: fx.heart(d, *P(x, y), S(size), 255),
            (x - size, y - size, x + size, y + size),
        )


# ---------------------------------------------------------------------------
# versus: red vs blue, speed lines, a lightning bolt between them and "VS"
# ---------------------------------------------------------------------------


def versus(a, b, portrait):
    rng = random.Random(f"{a.text}\n{b.text}")
    red, blue = (214, 40, 52), (36, 86, 214)
    canvas = fx.gradient(
        a.size,
        [fx.shade(red, value=1.1), fx.shade(red, value=0.55)],
        90 if portrait else 0,
    )
    if portrait:
        split = ((0, 1010), (1080, 910))
        second = [(0, 1010), (1080, 910), (1080, 1920), (0, 1920)]
    else:
        split = ((1060, 0), (860, 1080))
        second = [(1060, 0), (1920, 0), (1920, 1080), (860, 1080)]
    other = fx.gradient(
        a.size,
        [fx.shade(blue, value=0.55), fx.shade(blue, value=1.1)],
        90 if portrait else 0,
    )
    mask = Image.new("L", a.size)
    ImageDraw.Draw(mask).polygon([(a.X(x), a.Y(y)) for x, y in second], fill=255)
    canvas.paste(other, (0, 0), mask)
    center = (540, 960) if portrait else (960, 540)
    _focus_lines(a, canvas, center, 120, (255, 255, 255, 70), 360, rng)
    white, ink = (255, 255, 255), (20, 20, 24)
    if portrait:
        boxes = ((70, 90, 400, 420), (680, 1500, 1010, 1830))
        regions = ((80, 460, 1000, 800), (80, 1110, 1000, 1450))
        credits = ((440, 230, "left"), (640, 1650, "right"))
        tails = ((235, 430), (845, 1490))
    else:
        boxes = ((90, 90, 430, 430), (1490, 90, 1830, 430))
        regions = ((70, 500, 860, 1020), (1060, 500, 1850, 1020))
        credits = ((470, 230, "left"), (1450, 230, "right"))
        tails = ((260, 440), (1660, 440))
    for s, box, (cx, cy, anchor), color in zip((a, b), boxes, credits, (red, blue)):
        l, t, r, bb = s.box((box[0] - 14, box[1] - 14, box[2] + 14, box[3] + 14))
        canvas.alpha_composite(Image.new("RGBA", (r - l, bb - t), ink + (255,)), (l, t))
        s.place(canvas, s.image, box, cover=True)
        s.frame(canvas, box, white, 6)
        _credit(s, canvas, (cx, cy), white, (235, 235, 240), anchor, 520, 46)
    pads = [a.box(r) for r in regions]
    inner = [
        (l + a.S(44), t + a.S(44), r - a.S(44), bb - a.S(44)) for l, t, r, bb in pads
    ]
    ra, rb = _fit_pair(a, b, inner, 76)
    # Portrait bubbles hug their speakers so the VS in the middle stays clear.
    valigns = ("top", "bottom") if portrait else ("middle", "middle")
    for s, region, result, side, tail, valign in zip(
        (a, b), pads, (ra, rb), ("left", "right"), tails, valigns
    ):
        bubble(
            s,
            canvas,
            region,
            result,
            white,
            ink,
            side=side,
            valign=valign,
            radius=26,
            outline=ink,
            tail=tail,
            shadow=None,
        )
    _lightning(a, canvas, *split, 26, rng)
    _big_word(
        a,
        canvas,
        "VS",
        center,
        260,
        (255, 226, 60),
        ink,
        16,
        rotate=-8,
        glow=(255, 140, 40),
    )
    return canvas


# ---------------------------------------------------------------------------
# love: pink, floating hearts, a big heart between two round portraits
# ---------------------------------------------------------------------------


def love(a, b, portrait):
    rng = random.Random(f"{a.text}\n{b.text}")
    canvas = fx.gradient(
        a.size, [(255, 226, 238), (255, 196, 220), (246, 206, 255)], 30
    )
    pinks = [(255, 255, 255, 150), (255, 150, 190, 120), (255, 110, 160, 90)]
    design = DESIGNS["portrait" if portrait else "landscape"]
    _hearts(a, canvas, (0, 0, *design), 26, rng, pinks)
    ink, rose = (120, 40, 80), (236, 64, 122)
    if portrait:
        boxes = ((400, 110, 680, 390), (400, 1530, 680, 1810))
        regions = ((90, 450, 990, 850), (90, 1070, 990, 1470))
        credits = ((720, 240, "left"), (360, 1660, "right"))
        heart = (540, 960, 300)
        tails = ((540, 400), (540, 1520))
    else:
        boxes = ((200, 110, 520, 430), (1400, 110, 1720, 430))
        regions = ((70, 540, 900, 1030), (1020, 540, 1850, 1030))
        credits = ((360, 500, "center"), (1560, 500, "center"))
        heart = (960, 300, 360)
        tails = ((360, 440), (1560, 440))
    for s, box, (cx, cy, anchor) in zip((a, b), boxes, credits):
        _avatar(s, canvas, box, glow=(255, 110, 160))
        if not portrait:
            cy = 490
        _credit(s, canvas, (cx, cy), rose, (170, 90, 130), anchor, 440, 38)
    hx, hy, hs = heart
    for size, color in (
        (hs * 1.25, (255, 255, 255, 120)),
        (hs, rose),
        (hs * 0.3, (255, 255, 255, 200)),
    ):
        offset = -hs * 0.18 if size < hs * 0.5 else 0
        x = hx + offset
        y = hy - hs * 0.12 if size < hs * 0.5 else hy
        a.vector(
            canvas,
            color,
            lambda d, P, S, x=x, y=y, size=size: fx.heart(d, *P(x, y), S(size), 255),
            (x - size, y - size, x + size, y + size),
        )
    pads = [a.box(r) for r in regions]
    inner = [
        (l + a.S(44), t + a.S(44), r - a.S(44), bb - a.S(44)) for l, t, r, bb in pads
    ]
    if not portrait:
        # Credits sit under the portraits, so the bubbles start below them.
        name_gap = a.S(110 if (a.handle or b.handle) else 60)
        pads = [(l, t + name_gap, r, bb) for l, t, r, bb in pads]
        inner = [(l, t + name_gap, r, bb) for l, t, r, bb in inner]
        tails = (None, None)
    ra, rb = _fit_pair(a, b, inner, 72)
    for s, region, result, side, tail in zip(
        (a, b), pads, (ra, rb), ("left", "right"), tails
    ):
        bubble(
            s,
            canvas,
            region,
            result,
            (255, 255, 255),
            ink,
            side=side,
            valign="top" if not portrait else "middle",
            radius=60,
            tail=tail,
            shadow=(255, 110, 160),
        )
    return canvas


# ---------------------------------------------------------------------------
# talk: a plain LINE screen — flat colours, no shadows, big type
# ---------------------------------------------------------------------------

LINE_BACKGROUND = (140, 171, 216)
LINE_HEADER = (38, 48, 66)
LINE_GREEN = (138, 232, 120)


def talk(a, b, portrait):
    canvas = Image.new("RGBA", a.size, LINE_BACKGROUND + (255,))
    ink, meta = (18, 18, 20), (236, 241, 250)
    W = 1080 if portrait else 1920
    header = 150 if portrait else 120
    a.rect(canvas, (0, 0, W, header), LINE_HEADER)
    a.line(canvas, "<", (40, header * 0.68), 48, (255, 255, 255), kind="handle")
    title = a.name or a.handle_text
    if title:
        a.line(canvas, title, (110, header * 0.68), 44, (255, 255, 255), width=W - 260)
    a.line(
        canvas,
        "≡",
        (W - 40, header * 0.68),
        48,
        (255, 255, 255),
        kind="handle",
        anchor="right",
    )
    if portrait:
        avatar = (36, 210, 186, 360)
        regions = ((210, 262, 1040, 1010), (110, 1070, 1044, 1880))
        label = (214, 244)
    else:
        avatar = (46, 165, 206, 325)
        regions = ((236, 214, 1560, 610), (380, 660, 1876, 1050))
        label = (240, 196)
    pads = [a.box(r) for r in regions]
    p = a.S(40)
    inner = [(l + p, t + p, r - p, bb - p) for l, t, r, bb in pads]
    ra, rb = _fit_pair(a, b, inner, 110)
    a.place(canvas, a.image, avatar, circle=True, cover=True)
    if a.name:
        a.line(canvas, a.name, label, 30, meta, width=900)
    tail = a.S(30)
    below = None
    for s, region, result, side, color in zip(
        (a, b), pads, (ra, rb), ("left", "right"), ((255, 255, 255), LINE_GREEN)
    ):
        if below is not None:
            # The reply follows right under the first message, as in LINE.
            region = (region[0], min(region[1], below), region[2], region[3])
        l, t, r, bb = bubble(
            s,
            canvas,
            region,
            result,
            color,
            ink,
            side=side,
            valign="top",
            pad=40,
            radius=44,
            shadow=None,
        )
        clock = s.date.strftime("%H:%M")
        if side == "left":
            _triangle(
                canvas,
                (l - tail * 0.7, t + tail * 0.2),
                (l + tail, t + tail * 1.4),
                tail * 1.2,
                color,
            )
            s.line(
                canvas,
                clock,
                (r / s.kx + 16, bb / s.ky - 8),
                24,
                meta,
                kind="handle",
                width=200,
            )
            below = round(bb + s.S(56))
        else:
            _triangle(
                canvas,
                (r + tail * 0.7, t + tail * 0.2),
                (r - tail, t + tail * 1.4),
                tail * 1.2,
                color,
            )
            s.line(
                canvas,
                "既読",
                (l / s.kx - 16, bb / s.ky - 40),
                24,
                meta,
                kind="handle",
                anchor="right",
                width=200,
            )
            s.line(
                canvas,
                clock,
                (l / s.kx - 16, bb / s.ky - 8),
                24,
                meta,
                kind="handle",
                anchor="right",
                width=200,
            )
    return canvas


def thumbnails():
    """``(label, title, image)`` of every mood, for the layout catalog."""

    from miq_catalog import AVATAR, DATE

    a = Speaker(AVATAR, "それは違うと思う！", "あおい", "aoi")
    b = Speaker(AVATAR, "いや、絶対こっちが正しい", "高橋 直人", "naoto")
    tiles = []
    for mood, (title, fonts) in MOODS.items():
        canvas, _, _ = compose_dialogue(
            a, b, mood, font_family=fonts[0], date=DATE, seed=1
        )
        tiles.append((f"miq {mood}", title, canvas))
    return tiles
