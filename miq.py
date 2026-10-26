#!/usr/bin/env python3
"""Offline quote PNG renderer: numbered layouts, one typeface per image, CLI and API."""

from __future__ import annotations

import argparse
import io
import os
import random
import re
from pathlib import Path

from PIL import Image, ImageOps

import miq_layouts
from miq_designs import dark_background, line_anchor, text_region
from miq_discord import clean
from miq_fonts import (
    FAMILIES,
    FONT_DIR,
    bundled_fonts,
    coverage,
    custom_family,
    family,
    find_emoji_font,
    find_font,
    find_italic_font,
    font_at,
)
from miq_images import encode_png
from miq_layouts import DESIGNS, REGISTRY, Scene
from miq_text import EmojiImages, MixedFont, fit, wrap_words
from miq_words import units

SIZE = (640, 360)
# Default output per orientation (MIQ_SIZE / MIQ_PORTRAIT_SIZE / MIQ_SQUARE_SIZE).
SIZES = {"landscape": SIZE, "portrait": (540, 960), "square": (640, 640)}
SIZE_ENV = {
    "landscape": "MIQ_SIZE",
    "portrait": "MIQ_PORTRAIT_SIZE",
    "square": "MIQ_SQUARE_SIZE",
}
BASE_SIZE = (1920, 1080)
ALIGN_CHOICES = ("center", "right")
LINE_HEIGHT = 1.43
LAYOUTS = tuple(REGISTRY)
# Every layout is equally likely (kept as pairs for the old public name).
LAYOUT_WEIGHTS = tuple((name, 1) for name in miq_layouts.names())
PNG_COMPRESSION = 3
__all__ = [
    "BASE_SIZE",
    "LAYOUTS",
    "SIZE",
    "choose_layout",
    "choose_style",
    "default_size",
    "find_layout",
    "compose",
    "dark_background",
    "find_font",
    "fit_text",
    "render",
    "render_bytes",
    "resolve_align",
    "resolve_size",
    "text_region",
    "warmup",
    "wrap_line",
]


def resolve_size(value=None):
    value = (
        value if value is not None and value != "" else os.getenv("MIQ_SIZE") or SIZE
    )
    if isinstance(value, (tuple, list)) and len(value) == 2:
        if any(isinstance(n, bool) or int(n) != n for n in value):
            raise ValueError("出力サイズは整数で指定してください。")
        width, height = map(int, value)
    else:
        matched = re.fullmatch(r"\s*(\d+)\s*[xX×,]\s*(\d+)\s*", str(value))
        if not matched:
            raise ValueError(f"出力サイズが不正です: {value}（例 640x360）")
        width, height = map(int, matched.groups())
    if width < 200 or height < 120:
        raise ValueError("出力サイズは200x120以上にしてください。")
    return width, height


def default_size(orientation="landscape"):
    """The output size used when none is given, per orientation."""

    if orientation not in SIZES:
        raise ValueError(f"不明な向き: {orientation}（{', '.join(SIZES)}）")
    return resolve_size(os.getenv(SIZE_ENV[orientation]) or SIZES[orientation])


def find_layout(value):
    """A layout name, its number (12) or a numeric string ("12") → its name."""

    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool):
        spec = miq_layouts.by_number(value)
        if spec is None:
            raise ValueError(
                f"構図番号 {value} はありません（miq.py --list-layouts で一覧）。"
            )
        return spec.name
    if value not in REGISTRY:
        raise ValueError(f"不明なレイアウト: {value}")
    return value


def choose_layout(rng=None, category=None, orientation=None):
    """Uniform draw over every layout (or one category / orientation).

    A category with no layout in the orientation falls back to the whole
    orientation, so ``cute`` + portrait still draws something.
    """

    if category is not None and category not in miq_layouts.categories():
        raise ValueError(
            f"不明なカテゴリ: {category}（{', '.join(miq_layouts.categories())}）"
        )
    pool = miq_layouts.names(category, orientation) or miq_layouts.names(
        None, orientation
    )
    if not pool:
        raise ValueError(f"不明な向き: {orientation}")
    index = int((rng or random).random() * len(pool))
    return pool[min(index, len(pool) - 1)]


def installed_families(layout):
    """The layout's typefaces that are actually on disk (best match first)."""

    keys = [k for k in REGISTRY[layout].fonts if FAMILIES[k].installed()]
    return keys or [k for k, f in FAMILIES.items() if f.installed()]


def choose_style(
    layout="auto",
    font_family="auto",
    *,
    category=None,
    seed=None,
    rng=None,
    orientation=None,
):
    """Resolve ``auto`` choices to concrete ``(layout, font_family)`` names.

    ``layout`` may also be a layout number. ``orientation`` narrows the draw.

    The same seed always gives the same pair, so a bot can show the user which
    style was drawn and re-render it later.
    """

    rng = rng or (random.Random(seed) if seed is not None else random)
    if layout in (None, "", "auto"):
        layout = choose_layout(rng, category, orientation)
    layout = find_layout(layout)
    if font_family in (None, "", "auto"):
        choices = installed_families(layout)
        if not choices:
            raise ValueError(
                "標準フォントがありません。python scripts/setup_fonts.py を実行してください。"
            )
        font_family = choices[min(int(rng.random() * len(choices)), len(choices) - 1)]
    family(font_family)
    return layout, font_family


def wrap_line(para, font, max_width):
    measured = font if isinstance(font, MixedFont) else MixedFont(font)
    # An over-wide word remains intact; fit_text reduces its size.
    return wrap_words(units(para), measured, max_width) or [para]


def fit_text(
    text, font_path, width, height, has_credit, make_font=font_at, align="right"
):
    box = text_region(width, height, has_credit, align)

    def maker(px):
        font = make_font(font_path, px)
        return font if isinstance(font, MixedFont) else MixedFont(font)

    result = fit(
        text,
        maker,
        box[2] - box[0],
        box[3] - box[1],
        max(9, round(76 * min(width / 1920, height / 1080))),
        classic=True,
    )
    widest = max(
        (result.font.getlength(row) * result.scale for row in result.lines), default=0
    )
    cx = line_anchor(width, box[0], box[2], widest, align)
    return (*box, cx, (result.font, result.lines, result.leading), result.scale)


def resolve_align(value=None) -> str:
    """本文の寄せ方を決める（``value`` → ``MIQ_TEXT_ALIGN`` → ``right``）。"""

    raw = (value or os.getenv("MIQ_TEXT_ALIGN") or "right").strip().lower()

    return raw if raw in ALIGN_CHOICES else "right"


def _emoji_font(explicit):
    path = find_emoji_font(explicit)
    if (
        not explicit
        and not os.getenv("MIQ_EMOJI_FONT")
        and (FONT_DIR / "NotoColorEmoji.ttf").is_file()
    ):
        path = str(FONT_DIR / "NotoColorEmoji.ttf")
    return path


def _open_image(source):
    """A path, raw bytes, a binary stream or a PIL image → RGBA image."""

    if isinstance(source, Image.Image):
        image = source
        if getattr(image, "is_animated", False):
            image.seek(0)
        return ImageOps.exif_transpose(image).convert("RGBA")
    if isinstance(source, (bytes, bytearray, memoryview)):
        source = io.BytesIO(bytes(source))
    with Image.open(source) as opened:
        return ImageOps.exif_transpose(opened).convert("RGBA")


def compose(
    avatar,
    text,
    name="",
    handle="",
    font_file=None,
    bold_font_file=None,
    size=None,
    emoji_font_file=None,
    italic_font_file=None,
    custom_emojis=None,
    align=None,
    *,
    layout="auto",
    seed=None,
    attachment=False,
    font_family="auto",
    category=None,
    mentions=None,
    markup=True,
    date=None,
    fast=False,
    orientation=None,
):
    """Draw the quote and return ``(image, layout, font_family)``.

    ``fast=True`` skips the 2× supersampling of the designed layouts: about
    twice as fast, with slightly coarser hairlines and small print.

    ``orientation`` (landscape / portrait / square) narrows the automatic
    draw; without it the orientation follows ``size``. A layout chosen by
    name or number keeps its own orientation, and without an explicit
    ``size`` it gets that orientation's default size.
    """

    if markup:
        # ASCII art is code-like: Discord emphasis/spoiler markers are part of
        # the picture and must not be stripped. Detect it before cleaning.
        try:
            from miq_aa import detect

            ascii_art = detect(text, bundled_fonts())
        except (OSError, ValueError):
            ascii_art = None
        if ascii_art is None:
            text = clean(text, mentions)
        name = clean(name, mentions)
    if not text.strip():
        raise ValueError("本文が空です。")
    explicit = size not in (None, "")
    if orientation is None:
        orientation = (
            miq_layouts.orientation_of(resolve_size(size))
            if explicit or os.getenv("MIQ_SIZE")
            else "landscape"
        )
    elif orientation not in SIZES:
        raise ValueError(f"不明な向き: {orientation}（{', '.join(SIZES)}）")
    align = resolve_align(align)
    body_file = font_file or os.getenv("MIQ_FONT")
    style = dict(category=category, seed=seed, orientation=orientation)
    if body_file:
        chosen = custom_family(find_font(body_file))
        layout, _ = choose_style(layout, "gothic", **style)
    else:
        layout, key = choose_style(layout, font_family, **style)
        chosen = family(key)
        if not chosen.installed():
            raise ValueError(
                f"書体 {chosen.label} がありません。python scripts/setup_fonts.py を実行してください。"
            )
    spec = REGISTRY[layout]
    # Layouts that draw several orientations follow the request; the others
    # keep their own (and its default size when no size is given).
    if explicit:
        target = resolve_size(size)
        drawn = spec.orientation_for(miq_layouts.orientation_of(target))
    else:
        drawn = spec.orientation_for(orientation)
        target = default_size(drawn)
    image = _open_image(avatar)
    images = EmojiImages(_emoji_font(emoji_font_file), custom_emojis)
    latin = italic_font_file or os.getenv("MIQ_ITALIC_FONT")
    latin = find_italic_font(latin) if latin else None
    # Native layouts paint at the output size; the others draw at up to 2×
    # (capped at 1920 wide) and downsample, which keeps small outputs crisp.
    design = DESIGNS[drawn][0]
    factor = 1 if spec.native or fast else min(2.0, max(1.0, design / target[0]))
    if factor > 1.5 and target[0] * 2 <= max(design, 1280):
        factor = 2
    work = (round(target[0] * factor), round(target[1] * factor))
    scene = Scene(
        image,
        text,
        name,
        handle,
        work,
        chosen,
        images,
        attachment,
        date,
        name_file=find_font(bold_font_file) if bold_font_file else None,
        latin_file=latin,
        design=DESIGNS[drawn],
        seed=seed,
    )
    scene.align = align
    canvas = spec.draw(scene)
    if canvas.size != target:
        if canvas.size == (target[0] * 2, target[1] * 2):
            # Exact 2× supersampling: a box reduction is the ideal filter. The
            # canvas is opaque, so reducing RGB skips Pillow's premultiplied
            # RGBA round trip.
            canvas = canvas.convert("RGB").reduce(2)
        else:
            canvas = canvas.resize(target, Image.Resampling.LANCZOS)
    return canvas, layout, chosen.key


def render(
    avatar: Path,
    text: str,
    output: Path,
    name: str = "",
    handle: str = "",
    font_file=None,
    bold_font_file=None,
    size=None,
    emoji_font_file=None,
    italic_font_file=None,
    custom_emojis=None,
    align=None,
    **options,
):
    """Render offline. Fix layout and font_family (or seed) for reproducible output.

    ``avatar`` may be a path, bytes, a binary stream or a PIL image; ``output``
    a path or a writable binary stream. ``attachment=True`` keeps the source
    image's aspect ratio instead of cropping it to a square. Text has no
    character-count or minimum-size limit.
    """
    canvas, _, _ = compose(
        avatar,
        text,
        name,
        handle,
        font_file,
        bold_font_file,
        size,
        emoji_font_file,
        italic_font_file,
        custom_emojis,
        align,
        **options,
    )
    _save(canvas, output)


def render_bytes(avatar, text, name="", handle="", **options):
    """PNG bytes plus the style that was drawn: ``(png, layout, font_family)``.

    Handy for Discord bots: ``discord.File(io.BytesIO(png), "quote.png")``.
    """

    canvas, layout, font_family = compose(avatar, text, name, handle, **options)
    buffer = io.BytesIO()
    _save(canvas, buffer)
    return buffer.getvalue(), layout, font_family


def _save(canvas, output):
    # Same size as Pillow's level 3 at about a third of the time.
    png = encode_png(canvas, PNG_COMPRESSION)
    if hasattr(output, "write"):
        output.write(png)
        return
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(png)


def warmup(full=True):
    """Load everything a first request would otherwise wait for (call at bot start).

    Besides the dictionary, fonts and emoji face, ``full`` draws one small
    quote with every layout: that imports the Pango/Cairo bindings (about a
    second of cffi parsing), reads each typeface's character map and fills the
    mask caches, so even the first real request runs at full speed.
    """

    from miq_words import tokenizer

    tokenizer()
    for key, item in FAMILIES.items():
        if item.installed():
            font_at(item.path, 64, item.body)
    path = _emoji_font(None)
    if path:
        EmojiImages(path).get("😀")
    if not full:
        return
    if os.getenv("MIQ_CUTOUT_PRELOAD") == "1":
        # Load the 切り抜き network now (≈2 s, ~300 MB per process) instead of
        # on the first ``miq 切り抜き`` each worker handles.
        import miq_cutout

        miq_cutout.preload()
    import miq_complex  # noqa: F401
    import miq_vertical  # noqa: F401

    for item in FAMILIES.values():
        if item.installed():
            coverage(str(item.path))
    sample = Image.new("RGBA", (64, 64), (90, 120, 160, 255))
    for name, spec in REGISTRY.items():
        if spec.hidden:
            continue  # on-request layouts (the cutout network) load lazily
        try:
            compose(sample, "あA😀", "n", "h", layout=name, seed=0, fast=True)
        except (OSError, ValueError):
            pass


def main():
    parser = argparse.ArgumentParser(description="画像と本文から引用PNGを作成します。")
    parser.add_argument("--avatar", "--image", dest="avatar", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", "--quote", dest="text")
    source.add_argument("--text-file", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--handle", default="")
    parser.add_argument("--output", "-o", type=Path, default=Path("quote.png"))
    parser.add_argument("--font", help="全テキストに使うフォントファイル")
    parser.add_argument("--bold-font", help="表示名だけに使うフォントファイル")
    parser.add_argument("--size")
    parser.add_argument("--align", choices=ALIGN_CHOICES)
    parser.add_argument("--emoji-font")
    parser.add_argument("--italic-font")
    parser.add_argument(
        "--layout", default="auto", help="auto、構図名または番号（--list-layouts）"
    )
    parser.add_argument(
        "--orientation",
        choices=miq_layouts.ORIENTATIONS,
        help="auto抽選の向き（既定は --size から判断、なければ横）",
    )
    parser.add_argument(
        "--category", choices=miq_layouts.categories(), help="auto抽選の系統を絞る"
    )
    parser.add_argument(
        "--font-family", choices=["auto", *FAMILIES], default="auto", help="書体"
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--attachment", action="store_true", help="画像を正方形に切らず縦横比を維持する"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Discord記法（**太字** や <@id>）を整形しない",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="2倍描画を省いて約2倍速にする（細部はやや粗い）",
    )
    parser.add_argument(
        "--list-layouts", action="store_true", help="構図と書体の一覧を表示して終了"
    )
    args = parser.parse_args()
    if args.list_layouts:
        for name, spec in sorted(REGISTRY.items(), key=lambda kv: kv[1].number):
            fonts = ", ".join(spec.fonts)
            sides = "/".join(o[0] for o in spec.orientations)
            print(
                f"{spec.number:3d} {name:10} {sides:6} {spec.category:7}"
                f" {spec.label}{'（指定時のみ）' if spec.hidden else ''}  [{fonts}]"
            )
        return
    if args.avatar is None or (args.text is None and args.text_file is None):
        parser.error("--avatar と --text（または --text-file）が必要です。")
    try:
        text = (
            args.text_file.read_text(encoding="utf-8-sig")
            if args.text_file
            else args.text
        )
        canvas, layout, font_family = compose(
            args.avatar,
            text,
            args.name,
            args.handle,
            args.font,
            args.bold_font,
            args.size,
            args.emoji_font,
            args.italic_font,
            None,
            args.align,
            layout=args.layout,
            seed=args.seed,
            attachment=args.attachment,
            font_family=args.font_family,
            category=args.category,
            markup=not args.raw,
            fast=args.fast,
            orientation=args.orientation,
        )
        _save(canvas, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"エラー: {exc}\n")
    print(f"Created: {args.output} ({layout} / {font_family})")


if __name__ == "__main__":
    main()
