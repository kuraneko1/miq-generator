"""Every layout. Coordinates are in the 1920x1080 design space (see Scene)."""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

import miq_effects as fx
from miq_images import classic_image, place_image
from miq_fonts import bundled_fonts
from miq_layouts import fill, layout
from miq_text import fit, paint

BASE_SIZE = (1920, 1080)
TEXT_LEFT = {"right": 1010, "center": 845}
TEXT_RIGHT = 1780
CENTER_SHIFT = 0.05

TEXT_FACES = ("gothic", "mincho", "maru", "shippori", "dela")
CLASSIC_FACES = ("gothic", "mincho", "maru", "shippori", "dela")


# ---------------------------------------------------------------------------
# classic: the original composition, painted natively at the output size
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _background_layer(size: tuple[int, int]) -> Image.Image:
    w, h = size
    kx, ky = w / BASE_SIZE[0], h / BASE_SIZE[1]
    bg = Image.new("RGB", size, (0, 1, 2))
    # Subtle cyan glow; no numpy needed.
    layer = Image.new("RGBA", size)
    gd = ImageDraw.Draw(layer)
    gd.ellipse((-320 * kx, -125 * ky, 1160 * kx, 1220 * ky), fill=(0, 70, 130, 26))
    layer = layer.filter(ImageFilter.GaussianBlur(max(1, int(255 * min(kx, ky)))))
    return Image.alpha_composite(bg.convert("RGBA"), layer)


def dark_background(size: tuple[int, int]) -> Image.Image:
    # The glow only depends on the canvas size; callers paint over their own copy.
    return _background_layer(tuple(size)).copy()


def text_region(width: int, height: int, has_credit: bool, align: str = "right"):
    """本文を置ける領域 ``(left, top, right, bottom)`` を返す。

    ``left`` は折り返しの左端。``center`` は上流と同じ広さを取り、``right`` は
    アイコンの右側だけに取る（長い行でもアイコンに掛からない）。
    """

    kx, ky = width / BASE_SIZE[0], height / BASE_SIZE[1]
    k = min(kx, ky)
    left = round(TEXT_LEFT.get(align, TEXT_LEFT["right"]) * kx)
    right = round(TEXT_RIGHT * kx)
    top = round(150 * k)
    bottom = (
        (round(846 * ky) - round(80 * k)) if has_credit else (height - round(80 * k))
    )

    return left, top, right, bottom


def line_anchor(
    width: int, left: int, right: int, widest: float, align: str = "right"
) -> float:
    """行の中心の x を決める。

    ``center`` は画面中央から少し右（``CENTER_SHIFT``）。ただしアイコン側（``left``）と
    右端（``right``）を越えない範囲で寄せるので、長い行でもアイコンに重ならない。
    """

    if align != "center":
        return (left + right) / 2

    # 画面中央から少しだけ右へ寄せた位置を狙う
    target = width * (0.5 + CENTER_SHIFT)
    low, high = left + widest / 2, right - widest / 2

    if low > high:
        return (left + right) / 2

    return min(max(target, low), high)


@layout("classic", "クラシック", "classic", CLASSIC_FACES, number=1, native=True)
def classic(s):
    from miq_text import credit

    w, h = s.size
    kx, ky, k = s.kx, s.ky, s.k
    align = getattr(s, "align", "right")
    box = text_region(w, h, s.has_credit, align)
    canvas = dark_background(s.size)
    classic_image(canvas, s.image, s.attachment)
    if s.aa:
        from miq_aa import draw

        draw(
            canvas,
            s.aa[1],
            s.aa[0],
            bundled_fonts(),
            box,
            (246, 248, 250),
            max(9, round(104 * k * s.family.scale)),
            align="center",
            valign="middle",
        )
        cx = line_anchor(w, box[0], box[2], 0, align)
    else:
        result = fit(
            s.text,
            s.quote_font,
            box[2] - box[0],
            box[3] - box[1],
            max(9, round(104 * k * s.family.scale)),
            classic=True,
        )
        widest = max(
            (result.font.getlength(row) * result.scale for row in result.lines),
            default=0,
        )
        cx = line_anchor(w, box[0], box[2], widest, align)
        paint(canvas, result, box, (246, 248, 250), center=True, classic=True, cx=cx)
    if s.has_credit:
        y = round(846 * ky)
        ImageDraw.Draw(canvas).line(
            (cx - round(142 * kx), y, cx + round(142 * kx), y),
            fill=(63, 125, 149),
            width=max(1, round(2 * k)),
        )
        name_size = max(9, round(43 * k))
        while (
            name_size > 9
            and s.name_font(name_size).width("— " + s.name) > box[2] - box[0]
        ):
            name_size -= 1
        if s.name:
            credit(
                canvas,
                "— " + s.name,
                s.name_font,
                name_size,
                (cx, y + round(24 * ky)),
                box[2] - box[0],
                (248, 250, 252),
                center=True,
            )
        if s.handle:
            credit(
                canvas,
                s.handle_text,
                s.handle_font,
                max(8, round(30 * k)),
                (cx, y + round((88 if s.name else 45) * ky)),
                box[2] - box[0],
                (132, 160, 173),
                center=True,
            )
    return canvas


# ---------------------------------------------------------------------------
# The original editorial trio
# ---------------------------------------------------------------------------


def _editorial(s, background, ink, secondary, slot, text_box, maximum, author, center):
    canvas = fill(s.size, background)
    place_image(canvas, s.image, s.box(slot), s.attachment)
    s.write(canvas, text_box, ink, maximum, align="center" if center else "left")
    available = 1920 - 104 - author[0]
    s.credits(
        canvas,
        author,
        ink,
        secondary,
        name_px=48,
        handle_px=32,
        gap=62,
        width=available,
    )
    return canvas


@layout("mirrored", "ミラー", "classic", CLASSIC_FACES, number=2)
def mirrored(s):
    return _editorial(
        s,
        "#101010",
        "#f4f3ef",
        "#aaa9a7",
        (946, 65, 1896, 1015),
        (100, 120, 900, 800),
        130,
        (112, 926),
        False,
    )


# ---------------------------------------------------------------------------
# Cool
# ---------------------------------------------------------------------------


@layout("noir", "ノワール", "cool", ("gothic", "mincho", "shippori", "dela"), number=5)
def noir(s):
    # Grey the (small) source before enlarging it, not the full canvas.
    photo = fx.cover(fx.grayscale(s.image, contrast=1.25), s.size, (0.5, 0.35))
    canvas = fill(s.size, "#050505")
    canvas.alpha_composite(photo)
    # Darken from the left (where the text sits) and from the bottom.
    canvas.alpha_composite(
        _shade(s.size, [(0, 0, 0, 240), (0, 0, 0, 200), (0, 0, 0, 40)], 0, (0, 0.45, 1))
    )
    canvas.alpha_composite(
        _shade(s.size, [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 200)], 90, (0, 0.55, 1))
    )
    fx.grain(canvas, 0.07)
    s.write(canvas, (110, 110, 1200, 840), (244, 244, 242), 150, valign="bottom")
    if s.has_credit:
        s.hline(canvas, 120, 200, 885, (244, 244, 242, 220), 3)
        s.credits(
            canvas,
            (120, 950),
            (244, 244, 242),
            (160, 160, 158),
            name_px=42,
            handle_px=30,
            gap=52,
            width=900,
        )
    return canvas


def _shade(size, colors, angle, steps=None):
    """Gradient with per-stop alpha (RGBA stops)."""

    w, h = size
    rgb = fx.gradient(size, [c[:3] for c in colors], angle, steps=steps)
    alpha = fx.gradient(size, [(c[3],) * 3 for c in colors], angle, steps=steps)
    rgb.putalpha(alpha.getchannel("R"))
    return rgb


DUOTONES = (
    ((29, 26, 77), (255, 90, 95), (243, 239, 230)),
    ((14, 34, 64), (62, 242, 200), (236, 242, 238)),
    ((61, 12, 17), (255, 182, 39), (246, 238, 224)),
    ((16, 42, 18), (198, 255, 74), (240, 243, 230)),
    ((40, 14, 70), (255, 130, 220), (245, 236, 246)),
    ((20, 20, 24), (90, 160, 255), (236, 239, 245)),
)


@layout(
    "duotone",
    "デュオトーン",
    "cool",
    ("dela", "gothic", "mincho", "shippori", "pop"),
    number=12,
)
def duotone(s):
    hue = fx.accent(s.image)
    # Pick the palette nearest the photo's own accent colour.
    dark, light, paper = min(
        DUOTONES,
        key=lambda p: sum((a - b) ** 2 for a, b in zip(p[1], hue)),
    )
    canvas = fill(s.size, paper)
    photo_box = s.box((90, 90, 900, 990))
    l, t, r, b = photo_box
    photo = fx.duotone(fx.fitted(s.image, (r - l, b - t), s.attachment), dark, light)
    x, y = l + (r - l - photo.width) // 2, t + (b - t - photo.height) // 2
    canvas.alpha_composite(photo, (x, y))
    over = Image.new("L", s.size)
    over.paste(photo.getchannel("A").point(lambda v: 255 if v > 128 else 0), (x, y))
    region = s.box((680, 150, 1820, 830))
    result = s.fit(region, 160)
    mask = s.text_mask(result, region)
    canvas.alpha_composite(
        fx.colored(ImageChops.multiply(mask, ImageOps.invert(over)), dark)
    )
    canvas.alpha_composite(fx.colored(ImageChops.multiply(mask, over), paper))
    s.pictures(canvas, result, region)
    s.hline(canvas, 1000, 1100, 900, dark, 6)
    s.credits(
        canvas,
        (1000, 975),
        dark,
        fx.mix(dark, paper, 0.45),
        name_px=44,
        handle_px=30,
        gap=0 if not s.name else 52,
        width=820,
    )
    return canvas


@layout(
    "manga",
    "マンガ",
    "neta",
    ("gothic", "dela", "rocknroll", "pop", "potta"),
    number=15,
)
def manga(s):
    canvas = fill(s.size, "#fbfbf8")
    ink = (17, 17, 17)
    left = (70, 70, 820, 1010)
    l, t, r, b = s.box(left)
    photo = fx.cover(s.image, (r - l, b - t), (0.5, 0.4))
    canvas.alpha_composite(
        fx.halftone(photo, s.S(11), ink=ink, paper=(251, 251, 248)), (l, t)
    )
    s.frame(canvas, left, ink, 9)
    panel = (870, 70, 1850, 1010)
    pl, pt, pr, pb = s.box(panel)
    cx, cy = (pl + pr) / 2, (pt + pb) / 2 - s.Y(20)
    lines = Image.new("L", (pr - pl, pb - pt))
    draw = ImageDraw.Draw(lines)
    radius = math.hypot(pr - pl, pb - pt)
    for i in range(140):
        angle = s.rng.uniform(0, math.tau)
        inner = s.rng.uniform(0.42, 0.5) * min(pr - pl, pb - pt)
        spread = s.rng.uniform(0.002, 0.008)
        ox, oy = cx - pl, cy - pt
        draw.polygon(
            [
                (ox + math.cos(angle) * inner, oy + math.sin(angle) * inner * 0.8),
                (
                    ox + math.cos(angle - spread) * radius,
                    oy + math.sin(angle - spread) * radius,
                ),
                (
                    ox + math.cos(angle + spread) * radius,
                    oy + math.sin(angle + spread) * radius,
                ),
            ],
            fill=255,
        )
    canvas.alpha_composite(fx.colored(lines, ink), (pl, pt))
    s.frame(canvas, panel, ink, 9)
    bubble = (900, 130, 1830, 900)

    def bubble_shape(d, P, S, grow=0):
        d.ellipse(
            (
                *P(bubble[0] - grow, bubble[1] - grow),
                *P(bubble[2] + grow, bubble[3] + grow),
            ),
            fill=255,
        )
        d.polygon(
            [P(1000 - grow, 700), P(820 - grow * 2, 900), P(1110 + grow, 780)], fill=255
        )

    s.vector(
        canvas, ink, lambda d, P, S: bubble_shape(d, P, S, 7), (790, 110, 1850, 930)
    )
    s.vector(
        canvas,
        (255, 255, 255),
        lambda d, P, S: bubble_shape(d, P, S, 0),
        (790, 110, 1850, 930),
    )
    rx, ry = (bubble[2] - bubble[0]) / 2, (bubble[3] - bubble[1]) / 2
    mx, my = (bubble[0] + bubble[2]) / 2, (bubble[1] + bubble[3]) / 2
    s.write(
        canvas,
        (mx - rx * 0.74, my - ry * 0.66, mx + rx * 0.74, my + ry * 0.66),
        ink,
        170,
        align="center",
    )
    if s.has_credit:
        label = s.name or s.handle_text
        font = s.name_font(max(1, round(36 * s.k * s.family.scale)))
        width = min(700, font.getlength(label) / s.kx + 60)
        s.rect(
            canvas, (900, 100, 900 + width, 170), (255, 255, 255), outline=ink, width=5
        )
        s.line(canvas, label, (930, 148), 36, ink, width=width - 60)
    return canvas


@layout("notebook", "ノート", "cute", ("klee", "maru", "magic", "hachimaru"), number=25)
def notebook(s):
    canvas = fill(s.size, (255, 253, 245))
    y = 150
    while y < 1080:
        s.hline(canvas, 0, 1920, y, (172, 206, 236), 2)
        y += 76
    s.vline(canvas, 210, 0, 1080, (255, 150, 160), 2.5)
    for y in (180, 540, 900):
        s.vector(
            canvas,
            (232, 226, 210),
            lambda d, P, S, y=y: d.ellipse(
                (*P(95 - 22, y - 22), *P(95 + 22, y + 22)), fill=255
            ),
            (60, y - 40, 130, y + 40),
        )
    # Taped photo, slightly turned.
    pic_box = s.box((1290, 160, 1750, 620))
    side = pic_box[2] - pic_box[0]
    border = round(s.S(18))
    photo = fx.fitted(s.image, (side, pic_box[3] - pic_box[1]), s.attachment)
    card = Image.new(
        "RGBA",
        (photo.width + border * 2, photo.height + border * 2 + round(s.S(40))),
        (255, 255, 255, 255),
    )
    card.alpha_composite(photo, (border, border))
    card = card.rotate(-4, resample=Image.Resampling.BICUBIC, expand=True)
    x, y = pic_box[0], pic_box[1]
    fx.shadow(
        canvas,
        card.getchannel("A"),
        (x, y),
        blur=s.S(14),
        opacity=0.25,
        offset=(s.S(6), s.S(10)),
    )
    canvas.alpha_composite(card, (x, y))
    tape = Image.new("RGBA", (round(s.S(200)), round(s.S(56))), (255, 214, 120, 190))
    stripes = ImageDraw.Draw(tape)
    for i in range(-tape.height, tape.width, max(4, round(s.S(22)))):
        stripes.line(
            (i, tape.height, i + tape.height, 0),
            fill=(255, 240, 200, 190),
            width=max(2, round(s.S(8))),
        )
    tape = tape.rotate(6, resample=Image.Resampling.BICUBIC, expand=True)
    canvas.alpha_composite(
        tape, (round(x + card.width / 2 - tape.width / 2), round(y - tape.height / 2))
    )
    pen = (36, 52, 104)
    s.write(canvas, (260, 150, 1220, 920), pen, 120)
    if s.name:
        font = s.name_font(max(1, round(44 * s.k * s.family.scale)))
        length = min(460, font.getlength(s.name) / s.kx)
        s.rect(canvas, (1300, 762, 1320 + length, 790), (255, 230, 90, 170), radius=8)
        s.line(canvas, s.name, (1310, 780), 44, pen, width=460)
    s.line(
        canvas,
        s.handle_text,
        (1310, 850 if s.name else 780),
        30,
        (90, 100, 140),
        kind="handle",
        width=460,
    )
    s.vector(
        canvas,
        pen,
        lambda d, P, S: fx.star(d, *P(1235, 700), S(26), 255, 5, 0.45),
        (1200, 665, 1270, 735),
    )
    return canvas
