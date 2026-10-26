"""Measured text, bounded emoji resources and proportion-preserving fitting."""

import hashlib
import io
import math
import unicodedata
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageOps

from miq_fonts import coverage, font_at
from miq_words import CUSTOM, GRAPHEME, chunks, runs, units


class EmojiImages:
    """A render-local cache: another request cannot reuse stale custom images."""

    def __init__(self, font_path=None, custom=None):
        self.path = font_path
        self.custom = {str(k): v for k, v in (custom or {}).items()}
        self.cache = OrderedDict()
        self.font = None

    def get(self, token):
        if token in self.cache:
            self.cache.move_to_end(token)
            return self.cache[token]
        matched = CUSTOM.fullmatch(token)
        image = None
        if matched:
            path = self.custom.get(matched[2])
            if isinstance(path, (bytes, bytearray, memoryview)):
                path = io.BytesIO(bytes(path))
            if path:
                with Image.open(path) as source:
                    image = ImageOps.exif_transpose(source).convert("RGBA")
        elif self.path:
            if self.font is None:
                for size in (109, 128, 64):
                    try:
                        self.font = font_at(self.path, size)
                        break
                    except OSError:
                        continue
                if self.font is None:
                    raise ValueError(f"絵文字フォントを読み込めません: {self.path}")
            image = Image.new("RGBA", (320, 280))
            ImageDraw.Draw(image).text(
                (4, 4), token, font=self.font, anchor="lt", embedded_color=True
            )
            bbox = image.getbbox()
            image = image.crop(bbox) if bbox else None
        if image is not None:
            image = ImageOps.contain(image, (128, 128), Image.Resampling.LANCZOS)
        self.cache[token] = image
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return image

    @staticmethod
    def label(token):
        matched = CUSTOM.fullmatch(token)
        return f":{matched[1]}:" if matched else token


class MixedFont:
    def __init__(
        self,
        font,
        emoji_font=None,
        italic_font=None,
        custom_emojis=None,
        *,
        images=None,
        editorial=False,
        latin=None,
    ):
        self.font = font
        self.px = font.size
        self.emoji_font = emoji_font
        self.images = images or EmojiImages(emoji_font, custom_emojis)
        self.italic = latin or (font_at(italic_font, self.px) if italic_font else None)
        self.editorial = editorial
        self._emoji_advance = self.px * (1.1 if editorial else 1.245)
        self._metrics = OrderedDict()
        self._complex = None

    @property
    def has_emoji(self):
        return bool(self.emoji_font or self.images.custom)

    def face_for(self, kind):
        return self.italic if kind == "latin" and self.italic else self.font

    def ascent(self):
        return self.font.getmetrics()[0]

    def emoji_size(self):
        return self.px if self.editorial else max(1, round(self.px * 1.05))

    def complex_text(self, text):
        for token, kind in runs(text, self.editorial):
            if kind == "emoji":
                continue
            face = self.face_for(kind)
            cmap = coverage(str(face.path))
            for ch in token:
                if unicodedata.bidirectional(ch) in ("R", "AL", "AN"):
                    return True
                if (
                    ord(ch) not in cmap
                    and not ch.isspace()
                    and unicodedata.category(ch) not in ("Cf", "Cc")
                ):
                    return True
        return False

    @property
    def complex(self):
        if self._complex is None:
            from miq_complex import ComplexText

            self._complex = ComplexText(self)
        return self._complex

    def pieces(self, text, tiled=False):
        for token, kind in runs(text, self.editorial):
            image = self.images.get(token) if kind == "emoji" else None
            if image is not None:
                yield token, None, image, self._emoji_advance
            else:
                face = self.face_for("text" if kind == "emoji" else kind)
                token = self.images.label(token) if kind == "emoji" else token
                parts = (
                    chunks(token, 24 if tiled else 64)
                    if tiled or len(token) > 1024
                    else [token]
                )
                for part in parts:
                    yield part, face, None, self.glyph_metrics(face, part)[0]

    def glyph_metrics(self, face, text):
        """Metrics belong to the face, so the cache travels with the face handle."""

        cache = getattr(face, "_miq_glyph_metrics", None)
        if cache is None:
            cache = face._miq_glyph_metrics = OrderedDict()
        if text in cache:
            cache.move_to_end(text)
            return cache[text]
        result = face.getlength(text), face.getbbox(text, anchor="ls")
        if len(text) <= 1024:
            cache[text] = result
            if len(cache) > 128:
                cache.popitem(last=False)
        return result

    def measure(self, text):
        key = text if len(text) <= 512 else hashlib.sha256(text.encode()).digest()
        if key in self._metrics:
            self._metrics.move_to_end(key)
            return self._metrics[key]
        if self.complex_text(text):
            result = self.complex.measure(text)
        else:
            x = 0.0
            boxes = []
            for part, face, image, advance in self.pieces(text):
                if image is not None:
                    size = self.emoji_size()
                    top = (
                        -114 * self.px / 128 if self.editorial else -size + size * 0.12
                    )
                    boxes.append((x, top, x + size, top + size))
                else:
                    l, t, r, b = self.glyph_metrics(face, part)[1]
                    boxes.append((x + l, t, x + r, b))
                x += advance
            bounds = (
                (
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                )
                if boxes
                else (0, 0, 0, 0)
            )
            result = x, bounds
        self._metrics[key] = result
        if len(self._metrics) > 512:
            self._metrics.popitem(last=False)
        return result

    def getlength(self, text):
        return self.measure(text)[0]

    def width(self, text):
        advance, (left, _, right, _) = self.measure(text)
        return max(advance, right) - min(0, left)


def draw_mixed(draw, canvas, xy, text, font, fill, *, anchor="lt", emoji=True):
    x, y = xy
    if anchor == "mt":
        x -= font.getlength(text) / 2
    if font.complex_text(text):
        font.complex.draw(canvas, text, (x, y + font.ascent()), fill, emoji=emoji)
        return
    if not font.has_emoji and font.italic is None:
        draw.text((x, y), text, font=font.font, fill=fill, anchor="lt")
        return
    baseline = y + font.ascent()
    for part, face, image, advance in font.pieces(text):
        if image is None:
            draw.text((x, baseline), part, font=face, fill=fill, anchor="ls")
        elif emoji:
            size = font.emoji_size()
            image = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
            canvas.alpha_composite(
                image,
                (
                    round(x + (size - image.width) / 2),
                    round(baseline - size + size * 0.12 + (size - image.height) / 2),
                ),
            )
        x += advance


PAUSE = "、。，．,.!?！？…"


def _balanced(words, width, measure):
    """Minimum-raggedness breaks; ``measure(i, j)`` is the width of words[i:j]."""

    n = len(words)
    costs = [math.inf] * n + [0]
    after = [None] * n
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n + 1):
            measured = measure(i, j)
            if measured > width:
                break
            if measured <= 0 and not (i == 0 and j == n):
                continue
            last = words[j - 1].rstrip()[-1:] or " "
            penalty = 0.28 + (1 - measured / width) ** 2
            penalty += 0.18 if j < n and last not in PAUSE else 0
            penalty += 0.6 if j == n and measured < width * 0.25 else 0
            if penalty + costs[j] < costs[i]:
                costs[i], after[i] = penalty + costs[j], j
    if after[0] is None:
        return None
    rows = []
    i = 0
    while i < n:
        j = after[i]
        rows.append("".join(words[i:j]).strip())
        i = j
    return rows


def _orphan(groups, font, width):
    """Avoid a last line of one or two characters (CSS ``text-wrap: pretty``)."""

    if len(groups) < 2 or len(groups[-2]) < 2:
        return groups
    tail = "".join(groups[-1]).strip()
    letters = [g for g in GRAPHEME.findall(tail) if g not in PAUSE and not g.isspace()]
    if len(letters) > 2:
        return groups
    moved = [groups[-2][-1], *groups[-1]]
    if font.width("".join(moved).strip()) > width:
        return groups
    return [*groups[:-2], groups[-2][:-1], moved]


def wrap_words(words, font, width, balanced=False):
    if not words:
        return [""]
    if balanced and len(words) <= 160:
        # pretext-style: measure each word once, then lay lines out with sums.
        advances = [font.getlength(word) for word in words]
        cores = [font.width(word.strip()) for word in words]
        prefix = [0.0]
        for advance in advances:
            prefix.append(prefix[-1] + advance)

        def estimate(i, j):
            return prefix[j - 1] - prefix[i] + cores[j - 1]

        rows = _balanced(words, width, estimate)
        if rows is not None and all(font.width(row) <= width for row in rows):
            return rows
        # Kerning or ink overhang across words broke the estimate: measure.
        return _balanced(
            words, width, lambda i, j: font.width("".join(words[i:j]).strip())
        )
    groups = []
    pending = []
    estimate = 0
    for word in words:
        w = font.width(word)
        if font.width(word.strip()) > width:
            return None
        if pending and estimate + w > width:
            # The final measurement includes kerning and actual ink overhang.
            while len(pending) > 1 and font.width("".join(pending).rstrip()) > width:
                tail = pending.pop()
                groups.append(pending)
                pending = [tail]
            groups.append(pending)
            pending = []
            estimate = 0
        pending.append(word)
        estimate += w
    if pending:
        if font.width("".join(pending).rstrip()) > width:
            return None
        groups.append(pending)
    groups = _orphan(groups, font, width)
    return ["".join(group).rstrip() for group in groups] or [""]


class _AAFont:
    """Just enough of a face for layouts that measure one text width."""

    def __init__(self, width, px):
        self.width = width
        self.px = px
        self.size = px

    def getlength(self, _text):
        return self.width


class AALayout:
    """An ASCII-art block presented through the TextLayout interface."""

    def __init__(self, art, fonts, box, maximum):
        from miq_aa import _measure, layout

        self.aa = art
        self.fonts = fonts
        self.box = box
        self.maximum = maximum
        px, render, path, features, leading = layout(
            art[1], art[0], fonts, box[2] - box[0], box[3] - box[1], maximum
        )
        widths = [_measure(path, render, line, features) for line in art[1]]
        self.px = px
        self.scale = 1.0
        self.lines = art[1]
        self.leading = 0.0
        self.top = 0.0
        self.bottom = 0.0
        self.height = len(art[1]) * px * leading
        self.width = max(widths, default=0.0) * px / render
        self.font = _AAFont(self.width, px)
        self.maker = None


@dataclass
class TextLayout:
    font: MixedFont
    lines: list[str]
    size: float
    leading: float
    top: float
    bottom: float
    # Builds the face at a given pixel size (lets ``paint`` draw natively).
    maker: object = None

    @property
    def scale(self):
        return self.size / self.font.px

    @property
    def height(self):
        return (
            (len(self.lines) - 1) * self.leading + self.bottom - self.top
        ) * self.scale


def fit(text, make_font, width, height, maximum, *, classic=False):
    paragraphs = [units(p) for p in text.split("\n")]
    # Fixed metrics below the ordinary pixel range allow subpixel type sizes.
    reference = make_font(128)
    complex_text = reference.complex_text(text)

    def attempt(size):
        native = classic and size >= 8
        font = make_font(int(size)) if native else reference
        scale = size / font.px
        available = width / scale
        if complex_text:
            rows = font.complex.wrap(text, available)
        else:
            rows = []
            for words in paragraphs:
                wrapped = wrap_words(words, font, available, balanced=not classic)
                if wrapped is None:
                    return None
                rows.extend(wrapped)
        if any(font.width(row) > available + 0.01 for row in rows):
            return None
        bounds = [font.measure(row)[1] for row in rows]
        leading = int(font.px * 1.43) if native else font.px * 1.43
        top = min(b[1] for b in bounds)
        bottom = max(b[3] for b in bounds)
        if classic:
            # Preserve the original line-box placement for ordinary quotes.
            top = min(top, -font.ascent())
            bottom = max(bottom, leading - font.ascent())
        leading = max(leading, bottom - top)
        layout = TextLayout(font, rows, size, leading, top, bottom, make_font)
        return layout if layout.height <= height + 0.01 else None

    if classic:
        low, high = 8, max(8, int(maximum))
        best = None
        while low <= high:
            mid = (low + high) // 2
            result = attempt(mid)
            if result is None:
                high = mid - 1
            else:
                best = result
                low = mid + 1
        if best is not None:
            return best
        maximum = 8
    best = attempt(maximum)
    if best is not None:
        return best
    high = maximum
    low = high / 2
    while (best := attempt(low)) is None:
        if low < 1e-6:
            # Nothing visible to place (a blank or zero-width-only quote).
            raise ValueError("本文に表示できる文字がありません。")
        high = low
        low /= 2
    for _ in range(13):
        if high - low <= low * 0.004:
            break  # well below what a rendered pixel can show
        mid = (low + high) / 2
        trial = attempt(mid)
        if trial is None:
            high = mid
        else:
            low, best = mid, trial
    return best


def paint(
    canvas,
    layout,
    box,
    fill,
    *,
    center=False,
    classic=False,
    cx=None,
    align=None,
    valign="middle",
    emoji=True,
):
    """Paint in bounded strips, including sizes below a physical pixel.

    ``align`` is ``left``/``center``/``right`` (``center=True`` is the old
    spelling); ``valign`` places the block at the ``top``/``middle``/``bottom``
    of the box. ``emoji=False`` leaves pictures out (for text-only masks).
    """
    font = layout.font
    scale = layout.scale
    align = align or ("center" if center else "left")
    if getattr(layout, "aa", None):
        from miq_aa import draw

        draw(
            canvas,
            layout.aa[1],
            layout.aa[0],
            layout.fonts,
            box,
            fill,
            layout.maximum,
            align=align,
            valign=valign,
        )
        return
    spare = box[3] - box[1] - layout.height
    spare = 0 if valign == "top" else spare if valign == "bottom" else spare / 2
    baseline = box[1] + spare - layout.top * scale
    if classic and scale == 1:
        baseline = (
            box[1] + max(0, int((box[3] - box[1] - layout.height) // 2)) + font.ascent()
        )
    from miq_complex import BitmapTiles

    # Ordinary sizes are drawn with the face at the real pixel size (rounded
    # down so lines never outgrow the fitted width); the reference face plus
    # scaling is only needed for sub-pixel text.
    native = None
    if not classic and layout.maker is not None and layout.size >= 8 and scale < 1:
        native = layout.maker(int(layout.size))
    if native is not None:
        font, scale = native, 1
    context = BitmapTiles(canvas) if scale < 1 / 32 else nullcontext(None)
    with context as tiles:
        for row in layout.lines:
            advance, bounds = font.measure(row)
            if align == "center":
                x = (
                    cx if cx is not None else (box[0] + box[2]) / 2
                ) - advance * scale / 2
            elif align == "right":
                x = box[2] - max(advance, bounds[2]) * scale
            else:
                x = box[0] - min(0, bounds[0]) * scale
            if classic and scale == 1:
                draw_mixed(
                    ImageDraw.Draw(canvas),
                    canvas,
                    (x, baseline - font.ascent()),
                    row,
                    font,
                    fill,
                    emoji=emoji,
                )
            elif font.complex_text(row):
                font.complex.draw(canvas, row, (x, baseline), fill, scale, emoji=emoji)
            else:
                paint_line(
                    canvas,
                    font,
                    row,
                    (x, baseline),
                    fill,
                    scale,
                    tiles=tiles,
                    emoji=emoji,
                )
            baseline += layout.leading * layout.scale


def paint_line(canvas, font, text, xy, fill, scale, *, tiles=None, emoji=True):
    advance, bounds = font.measure(text)
    # Ordinary lines use the same rasterization as the approved prototype.
    if advance <= 4096 and scale >= 1 / 32:
        left, top, right, bottom = bounds
        origin = -min(0, left) + 2
        image = Image.new(
            "RGBA",
            (
                max(1, math.ceil(max(advance, right) + origin + 2)),
                max(1, math.ceil(bottom - top + 4)),
            ),
        )
        baseline = -top + 2
        x = origin
        for part, face, sprite, step in font.pieces(text):
            if sprite is not None:
                if not emoji:
                    x += step
                    continue
                size = font.emoji_size()
                sprite = ImageOps.contain(
                    sprite, (round(size), round(size)), Image.Resampling.LANCZOS
                )
                y = -114 * font.px / 128 if font.editorial else -size + size * 0.12
                image.alpha_composite(
                    sprite,
                    (
                        round(x + (size - sprite.width) / 2),
                        round(baseline + y + (size - sprite.height) / 2),
                    ),
                )
            else:
                ImageDraw.Draw(image).text(
                    (x, baseline), part, font=face, fill=fill, anchor="ls"
                )
            x += step
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        canvas.alpha_composite(
            image, (round(xy[0] - origin * scale), round(xy[1] + (top - 2) * scale))
        )
        return
    # Cairo maps each bounded source tile uniformly to fractional pixel space.
    from miq_complex import BitmapTiles

    with nullcontext(tiles) if tiles is not None else BitmapTiles(canvas) as compositor:
        x = xy[0]
        for part, face, sprite, step in font.pieces(text, tiled=True):
            if sprite is not None:
                if not emoji:
                    x += step * scale
                    continue
                size = font.emoji_size()
                sprite = ImageOps.pad(sprite, (round(size), round(size)))
                top = -114 * font.px / 128 if font.editorial else -size + size * 0.12
                compositor.paint(sprite, x, xy[1] + top * scale, scale)
            else:
                compositor.text(face, part, fill, x, xy[1], scale)
            x += step * scale


def credit(
    canvas,
    text,
    maker,
    maximum,
    xy,
    width,
    fill,
    *,
    center=False,
    baseline=False,
    anchor="left",
    emoji=True,
):
    """One line (name, handle, caption) shrunk only if it exceeds ``width``.

    ``anchor`` is ``left``/``center``/``right`` relative to ``xy[0]``
    (``center=True`` is the original spelling). ``baseline`` puts ``xy[1]`` on
    the baseline instead of the top of the line box.
    """
    if not text.strip():
        return
    if center:
        anchor = "center"
    font = maker(maximum)
    scale = min(1, width / max(1, font.width(text)))
    length = font.getlength(text) * scale
    x = xy[0] - (
        length / 2 if anchor == "center" else length if anchor == "right" else 0
    )
    if baseline:
        y = xy[1]
    elif scale == 1:
        draw_mixed(
            ImageDraw.Draw(canvas), canvas, (x, xy[1]), text, font, fill, emoji=emoji
        )
        return
    else:
        y = xy[1] + font.ascent() * scale
    if font.complex_text(text):
        font.complex.draw(canvas, text, (x, y), fill, scale, emoji=emoji)
    else:
        paint_line(canvas, font, text, (x, y), fill, scale, emoji=emoji)
