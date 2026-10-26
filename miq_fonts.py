"""Font discovery, glyph coverage and real font weights."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"


@lru_cache(maxsize=256)
def _font(path: str, px: int, weight):
    """Reuse FreeType handles: the binary search asks for the same sizes again."""

    font = ImageFont.truetype(path, px)
    if weight is not None:
        try:
            axes = font.get_variation_axes()
        except OSError:
            axes = []
        if axes:
            font.set_variation_by_axes(
                [
                    max(a["minimum"], min(a["maximum"], weight))
                    if a["name"] == b"Weight"
                    else a["default"]
                    for a in axes
                ]
            )
    return font


def font_at(path, px, weight=None):
    return _font(str(path), max(1, round(px)), weight)


@lru_cache(maxsize=12)
def coverage(path):
    """Codepoints the face can draw; synthesised glyph names keep cmap parsing cheap."""

    with TTFont(str(path), fontNumber=0, lazy=True) as font:
        try:
            count = font["maxp"].numGlyphs
        except (KeyError, AttributeError):
            return frozenset(font.getBestCmap() or {})
        font.setGlyphOrder(["glyph%.5d" % index for index in range(count)])
        return frozenset(font.getBestCmap() or {})


@dataclass(frozen=True)
class FontFamily:
    """One typeface used for every line of an image (weights may differ)."""

    key: str
    label: str
    file: str
    body: int | None = None
    name: int | None = None
    handle: int | None = None
    # Relative size: display faces look larger than text faces at the same px.
    scale: float = 1.0

    @property
    def path(self):
        return str(FONT_DIR / self.file)

    def installed(self):
        return (FONT_DIR / self.file).is_file()


# Faces that only the ASCII-art path uses (AA is not a layout typeface).
FONT_FILES = {
    "aa": "Saitamaar-Regular.ttf",
    "mono": "JetBrainsMono.ttf",
}


@lru_cache(maxsize=1)
def bundled_fonts():
    """Role -> path for the faces the AA renderer needs (checked once)."""

    paths = {role: FONT_DIR / name for role, name in FONT_FILES.items()}
    missing = sorted({path.name for path in paths.values() if not path.is_file()})
    if missing:
        raise ValueError(
            "標準フォントがありません: "
            + ", ".join(missing)
            + "。python scripts/setup_fonts.py を実行してください。"
        )
    return {role: str(path) for role, path in paths.items()}


FAMILIES = {
    family.key: family
    for family in (
        FontFamily("gothic", "Noto Sans JP", "NotoSansJP.ttf", 800, 700, 500),
        FontFamily("mincho", "Noto Serif JP", "NotoSerifJP.ttf", 800, 700, 500),
        FontFamily("maru", "Zen Maru Gothic", "ZenMaruGothic.ttf"),
        FontFamily("shippori", "Shippori Mincho B1", "ShipporiMinchoB1.ttf"),
        FontFamily("dela", "Dela Gothic One", "DelaGothicOne.ttf", scale=0.94),
        FontFamily("dot", "DotGothic16", "DotGothic16.ttf"),
        FontFamily("code", "M PLUS 1 Code", "MPLUS1Code.ttf", 700, 600, 400),
        FontFamily("klee", "Klee One", "KleeOne.ttf"),
        FontFamily("rocknroll", "RocknRoll One", "RocknRollOne.ttf"),
        # Display faces for the joke layouts: pop (創英角ポップ体風), brush pop,
        # brush (筆), marker and girly handwriting.
        FontFamily("pop", "Mochiy Pop One", "MochiyPopOne.ttf", scale=0.94),
        FontFamily("potta", "Potta One", "PottaOne.ttf", scale=0.96),
        FontFamily("syuku", "Yuji Syuku", "YujiSyuku.ttf"),
        FontFamily("magic", "Yusei Magic", "YuseiMagic.ttf"),
        FontFamily("hachimaru", "Hachi Maru Pop", "HachiMaruPop.ttf"),
    )
}

# The newspaper masthead (Latin only); a logo, not a text face.
BLACKLETTER = FONT_DIR / "UnifrakturMaguntia.ttf"


def family(key):
    try:
        return FAMILIES[key]
    except KeyError:
        raise ValueError(f"不明な書体: {key}（{', '.join(FAMILIES)}）") from None


def custom_family(path):
    """A user-supplied font file, treated as a one-weight family."""

    return FontFamily("custom", Path(path).stem, str(Path(path).resolve()))


EMOJI_FONT_CHOICES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "C:/Windows/Fonts/seguiemj.ttf",
]

ITALIC_FONT_CHOICES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/ariali.ttf",
]

BOLD_ITALIC_FONT_CHOICES = [
    "/usr/share/fonts/truetype/noto/NotoSans-BoldItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arialbi.ttf",
]

FONT_CHOICES = [
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/ipafont-gothic/ipag.ttf",
    # macOS
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    # Windows
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
]


def find_font(custom: str | None, bold: bool = False) -> str:
    if custom:
        if not Path(custom).is_file():
            raise ValueError(f"指定されたフォントが見つかりません: {custom}")
        return custom

    from_env = os.getenv("MIQ_FONT")
    if from_env:
        return find_font(from_env, bold)

    return _system_font(bold)


@lru_cache(maxsize=8)
def _system_font(bold: bool = False) -> str:
    """Path probes and ``fc-match`` are process-wide; run them once per process."""

    candidates = []
    for name in FONT_CHOICES:
        if bold:
            name = (
                name.replace("Regular", "Bold")
                .replace("W3", "W6")
                .replace("YuGothM", "YuGothB")
            )
        candidates.append(name)
    candidates.extend(FONT_CHOICES)
    for name in candidates:
        if Path(name).is_file():
            return name

    if shutil.which("fc-match"):
        try:
            p = subprocess.run(
                ["fc-match", "-f", "%{file}", ":lang=ja"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            path = p.stdout.strip()
            if (
                path
                and Path(path).is_file()
                and set(map(ord, "日本語")) <= coverage(path)
            ):
                return path
        except (OSError, subprocess.SubprocessError):
            pass
    raise ValueError(
        "日本語フォントが見つかりません。--font /path/to/font.ttf を指定してください。"
    )


def find_emoji_font(custom: str | None = None) -> str | None:
    """色つき絵文字フォントを探す（無ければ None）。"""

    if custom:
        if not Path(custom).is_file():
            raise ValueError(f"指定された絵文字フォントが見つかりません: {custom}")
        return custom

    from_env = os.getenv("MIQ_EMOJI_FONT")

    if from_env:
        return find_emoji_font(from_env)

    for name in EMOJI_FONT_CHOICES:
        if Path(name).is_file():
            return name

    return None


def find_italic_font(custom: str | None = None, *, bold: bool = False) -> str | None:
    """欧文の斜体フォントを探す（無ければ None）。"""

    if custom:
        if not Path(custom).is_file():
            raise ValueError(f"指定された欧文フォントが見つかりません: {custom}")
        return custom

    from_env = os.getenv("MIQ_ITALIC_FONT")

    if from_env:
        return find_italic_font(from_env, bold=bold)

    for name in BOLD_ITALIC_FONT_CHOICES if bold else ITALIC_FONT_CHOICES:
        if Path(name).is_file():
            return name

    return None
