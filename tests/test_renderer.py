import io
import random
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

import miq
import miq_layouts
from miq_fonts import FONT_DIR, coverage, find_font, find_italic_font, font_at
from miq_text import EmojiImages, MixedFont, fit
from miq_words import GRAPHEME, NO_END, NO_START, units

ROOT = Path(__file__).resolve().parents[1]
AVATAR = ROOT / "demo-avatar.png"


def maker(px):
    return MixedFont(
        font_at(find_font(None), px),
        str(FONT_DIR / "NotoColorEmoji.ttf"),
        find_italic_font(),
    )


@pytest.mark.parametrize(
    "value", [(0, 360), (640, -1), (199, 120), "bad", (640,), (640.5, 360)]
)
def test_invalid_dimensions(value):
    with pytest.raises(ValueError):
        miq.resolve_size(value)


def test_words_and_graphemes():
    assert units("じゃあ8時間ホモになるか") == ["じゃあ", "8時間", "ホモに", "なるか"]
    assert units("ものづくりに集中するか。") == ["ものづくりに", "集中するか。"]
    assert units("おはようございます。") == ["おはようございます。"]
    text = "👩🏽‍💻🇯🇵1️⃣e\u0301<a:wave:123>"
    assert GRAPHEME.findall(text) == ["👩🏽‍💻", "🇯🇵", "1️⃣", "e\u0301", "<a:wave:123>"]
    assert "café" in units("今日はcaféで話す。")[1]


def test_quote_word_wrap_and_kinsoku():
    result = fit("じゃあ8時間ホモになるか", maker, 257, 205, 25, classic=True)
    assert result.lines == ["じゃあ8時間ホモに", "なるか"]
    text = "「『大切な問い』を考えよう。」次の発見につながる。" * 8
    result = fit(text, maker, 257, 205, 25, classic=True)
    assert "".join(result.lines) == text
    assert all(
        row[0] not in NO_START and row[-1] not in NO_END for row in result.lines if row
    )
    assert all(
        result.font.width(row) * result.scale <= 257 + 0.01 for row in result.lines
    )


def test_no_minimum_size_or_truncation():
    text = "全文を残して小さく表示する。" * 12000
    result = fit(text, maker, 257, 205, 25, classic=True)
    assert 0 < result.size < 1
    assert "".join(result.lines) == text
    assert result.height <= 205 + 0.01
    assert result.scale == result.size / result.font.px


def test_long_word_stays_whole():
    text = "antidisestablishmentarianism" * 4000
    result = fit(text, maker, 257, 205, 25, classic=True)
    assert result.lines == [text]
    assert 0 < result.size < 1
    assert result.font.width(text) * result.scale <= 257 + 0.01


def test_tail_of_many_explicit_lines_changes_pixels(tmp_path):
    prefix = "\n".join(f"本文{i}" for i in range(119)) + "\n"
    outputs = []
    for tail in ["末尾まで表示する", "LAST LINE IS DIFFERENT"]:
        path = tmp_path / f"{len(outputs)}.png"
        miq.render(AVATAR, prefix + tail, path, layout="classic")
        outputs.append(Image.open(path).copy())
    assert ImageChops.difference(*outputs).getbbox() is not None


def test_custom_emoji_isolation_and_fallback(tmp_path):
    paths = []
    for color in ["red", "blue"]:
        p = tmp_path / f"{color}.png"
        Image.new("RGB", (40, 20), color).save(p)
        paths.append(p)
    first = EmojiImages(custom={"123": paths[0]}).get("<:same:123>")
    second = EmojiImages(custom={123: paths[1]}).get("<:same:123>")
    assert first.getpixel((5, 5)) != second.getpixel((5, 5))
    font = MixedFont(font_at(find_font(None), 24))
    assert font.getlength("<:missing:456>") == font.font.getlength(":missing:")
    assert EmojiImages(custom={}).get("<:missing:456>") is None
    with pytest.raises(OSError):
        EmojiImages(custom={"123": tmp_path / "invalid.png"}).get("<:same:123>")


def test_emoji_cache_is_bounded():
    images = EmojiImages()
    for i in range(160):
        images.get(f"<:missing:{i}>")
    assert len(images.cache) <= 128


@pytest.mark.parametrize("layout", [name for name, _ in miq.LAYOUT_WEIGHTS])
@pytest.mark.parametrize("shape", [(120, 400), (400, 120)])
def test_layout_attachment_and_repeatability(tmp_path, layout, shape):
    source = tmp_path / "photo.png"
    Image.new("RGBA", shape, (45, 100, 180, 180)).save(source)
    outputs = []
    for i in range(2):
        output = tmp_path / f"{i}.png"
        miq.render(
            source,
            "日本語と English 👩🏽‍💻",
            output,
            "著者 Name",
            "handle",
            layout=layout,
            attachment=True,
            size=(854, 480),
            seed=3,
        )
        with Image.open(output) as image:
            assert image.size == (854, 480)
            outputs.append(image.copy())
    assert ImageChops.difference(*outputs).getbbox() is None


def test_uniform_selection_boundaries_and_seed():
    class Fixed:
        def __init__(self, value):
            self.value = value

        def random(self):
            return self.value

    # On-request layouts (切り抜き) are never drawn by lot.
    layouts = miq_layouts.names()
    assert "cutout" in miq.LAYOUTS and "cutout" not in layouts
    assert len(layouts) >= 12 and len(set(layouts)) == len(layouts)
    for index, name in enumerate(layouts):
        assert miq.choose_layout(Fixed((index + 0.5) / len(layouts))) == name
    assert miq.choose_layout(Fixed(0)) == layouts[0]
    assert miq.choose_layout(Fixed(0.99999)) == layouts[-1]
    a, b = random.Random(42), random.Random(42)
    assert [miq.choose_layout(a) for _ in range(50)] == [
        miq.choose_layout(b) for _ in range(50)
    ]
    cute = {miq.choose_layout(random.Random(i), "cute") for i in range(200)}
    assert cute == set(miq_layouts.names("cute"))
    with pytest.raises(ValueError):
        miq.choose_layout(category="missing")


def test_style_draw_is_reproducible_and_respects_layout_fonts():
    for seed in range(40):
        layout, face = miq.choose_style(seed=seed)
        assert (layout, face) == miq.choose_style(seed=seed)
        assert face in miq.REGISTRY[layout].fonts
    assert miq.choose_style("notebook", "magic") == ("notebook", "magic")
    assert miq.choose_style(38, seed=1)[0] == "zatsu"
    assert miq.choose_style("classic", "mincho") == ("classic", "mincho")
    with pytest.raises(ValueError):
        miq.choose_style("classic", "comic-sans")


@pytest.mark.parametrize(
    "text",
    [
        "لا تتوقف عن التعلم. Discord 8:30 👩🏽‍💻",
        "Не переставай задавать вопросы.",
        "כל יום הוא הזדמנות ללמוד. Discord 8:30",
        "सीखना कभी बंद मत करो।",
        "อย่าหยุดเรียนรู้และตั้งคำถาม",
        "日本語と العربية、English を一緒に。",
    ],
)
def test_multilingual_layout_retains_text(text):
    result = fit(text, maker, 257, 205, 25, classic=True)
    assert "".join("".join(result.lines).split()) == "".join(text.split())
    assert result.height <= 205 + 0.01
    assert all(
        result.font.width(row) * result.scale <= 257 + 0.01 for row in result.lines
    )


def test_rtl_custom_emoji_and_missing_label(tmp_path):
    for custom in [{}, {"123": AVATAR}]:
        output = tmp_path / f"rtl-{len(custom)}.png"
        miq.render(
            AVATAR,
            "مرحبا <:picture:123> Discord 123",
            output,
            "ليلى",
            custom_emojis=custom,
            layout="classic",
        )
        assert Image.open(output).size == (640, 360)


def test_cli_and_original_positional_api(tmp_path):
    output = tmp_path / "cli.png"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "miq.py"),
            "--image",
            str(AVATAR),
            "--quote",
            "引用文",
            "--layout",
            "classic",
            "--size",
            "640x360",
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Image.open(output).size == (640, 360)
    miq.render(
        AVATAR,
        "引用文",
        output,
        "氏名",
        "name",
        None,
        None,
        "640x360",
        None,
        None,
        None,
        "right",
        seed=1,
    )
    with pytest.raises((ValueError, OSError)):
        miq.render(tmp_path / "missing.jpg", "引用文", output, layout="classic")
    with pytest.raises(ValueError):
        miq.render(AVATAR, "", output, layout="classic")


def test_animation_first_frame_and_exif_orientation(tmp_path):
    from miq_images import read_image

    gif = tmp_path / "animation.gif"
    Image.new("RGB", (30, 20), "red").save(
        gif,
        save_all=True,
        append_images=[Image.new("RGB", (30, 20), "blue")],
        duration=100,
    )
    image = EmojiImages(custom={"123": gif}).get("<a:animated:123>")
    assert image.getpixel((5, 5))[:3] == (255, 0, 0)
    exif = Image.Exif()
    exif[274] = 6
    jpeg = tmp_path / "rotated.jpg"
    Image.new("RGB", (80, 40), "green").save(jpeg, exif=exif)
    assert read_image(jpeg).size == (40, 80)


def test_optional_fonts_and_invalid_explicit_font(tmp_path, monkeypatch):
    monkeypatch.setattr(miq, "find_emoji_font", lambda _: None)
    monkeypatch.setattr(miq, "find_italic_font", lambda *a, **k: None)
    monkeypatch.setattr(miq, "FONT_DIR", tmp_path)
    miq.render(AVATAR, "日本語の本文", tmp_path / "plain.png", layout="classic")
    from miq_fonts import find_emoji_font, find_italic_font

    for finder in (find_emoji_font, find_italic_font):
        with pytest.raises(ValueError):
            finder(str(tmp_path / "missing.ttf"))


def test_real_font_weights_and_cjk_without_kana():
    regular = font_at(FONT_DIR / "NotoSansJP.ttf", 32, 400)
    heavy = font_at(FONT_DIR / "NotoSansJP.ttf", 32, 800)
    assert bytes(regular.getmask("日本語")) != bytes(heavy.getmask("日本語"))
    text = "不要急着寻找答案，先把问题想清楚。" * 8
    result = fit(text, maker, 257, 205, 25, classic=True)
    assert len(result.lines) > 1
    assert "".join(result.lines) == text


def test_bidi_order_and_plain_digits():
    import pangocffi as pango

    font = maker(24)
    text = "مرحبا Discord 123"
    layout, _ = font.complex.layout(text)
    positions = {}
    offset = 0
    for char in text:
        rect = pango.Rectangle()
        pango.pango.pango_layout_index_to_pos(layout.pointer, offset, rect.pointer)
        positions[char] = min(rect.x, rect.x + rect.width)
        offset += len(char.encode())
    for word, reverse in [("مرحبا", True), ("Discord", False), ("123", False)]:
        points = [positions[c] for c in word]
        assert points == sorted(points, reverse=reverse)
    width, _ = font.complex.measure("8:30")
    assert abs(width - font.italic.getlength("8:30")) < 3


def test_concurrent_render_requests(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def run(index):
        out = tmp_path / f"concurrent-{index}.png"
        miq.render(
            AVATAR,
            "مرحبا Discord <:same:123>",
            out,
            custom_emojis={"123": AVATAR},
            layout="classic",
            font_family="gothic",
        )
        return out.read_bytes()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert len(set(results)) == 1


def test_caches_are_reused_but_never_shared_by_callers():
    from miq_images import edge_mask

    path = FONT_DIR / "NotoSansJP.ttf"
    assert font_at(path, 24) is font_at(path, 24)
    assert font_at(path, 24) is not font_at(path, 25)
    assert font_at(path, 24) is not font_at(path, 24, 800)

    mask = edge_mask((40, 30), 4)
    centre = (mask.width // 2, mask.height // 2)
    assert mask.getpixel(centre) > 0
    mask.putpixel(centre, 0)
    assert edge_mask((40, 30), 4).getpixel(centre) > 0

    background = miq.dark_background((320, 180))
    background.putpixel((10, 10), (255, 0, 0, 255))
    assert miq.dark_background((320, 180)).getpixel((10, 10)) != (255, 0, 0, 255)


def test_coverage_matches_the_font_cmap():
    from fontTools.ttLib import TTFont

    path = str(FONT_DIR / "NotoSansJP.ttf")
    with TTFont(path, fontNumber=0, lazy=True) as font:
        expected = frozenset(font.getBestCmap() or {})
    assert expected
    assert coverage(path) == expected


HARD_TEXT = (
    "長い文章でも全文を残す。URL https://example.com/very/long/path も割らない。" * 5
)


@pytest.mark.parametrize("layout", list(miq.LAYOUTS))
def test_every_layout_handles_hard_inputs(tmp_path, layout):
    tall = tmp_path / "tall.png"
    Image.new("RGBA", (90, 400), (200, 80, 60, 255)).save(tall)
    for size, name, handle, text in [
        ((640, 360), "", "", HARD_TEXT),
        ((500, 500), "名前 Name", "user", "短い 👩🏽‍💻 <:wave:123>"),
        ((1280, 720), "ليلى", "", "مرحبا Discord 123 日本語"),
    ]:
        png, chosen, face = miq.render_bytes(
            tall,
            text,
            name,
            handle,
            size=size,
            layout=layout,
            seed=5,
            custom_emojis={"123": AVATAR},
        )
        assert chosen == layout and face in miq.REGISTRY[layout].fonts
        with Image.open(__import__("io").BytesIO(png)) as image:
            assert image.size == size
            assert (
                image.convert("L").getextrema()[0] != image.convert("L").getextrema()[1]
            )


@pytest.mark.parametrize("layout", list(miq.LAYOUTS))
def test_one_typeface_per_image(monkeypatch, layout):
    import miq_layouts as module

    used = set()
    original = module.font_at

    def spy(path, px, weight=None):
        used.add(str(path))
        return original(path, px, weight)

    monkeypatch.setattr(module, "font_at", spy)
    face = miq.REGISTRY[layout].fonts[-1]
    miq.compose(
        AVATAR,
        "日本語と English 123 👩🏽‍💻",
        "著者 Name",
        "handle",
        layout=layout,
        font_family=face,
    )
    from miq_fonts import FAMILIES

    assert used == {FAMILIES[face].path}


def test_render_accepts_bytes_images_and_streams(tmp_path):
    import io

    data = AVATAR.read_bytes()
    buffer = io.BytesIO()
    miq.render(data, "本文", buffer, layout="noir", font_family="gothic")
    first = buffer.getvalue()
    with Image.open(AVATAR) as image:
        png, layout, face = miq.render_bytes(
            image, "本文", layout="noir", font_family="gothic"
        )
    assert (layout, face) == ("noir", "gothic")
    assert first[:8] == b"\x89PNG\r\n\x1a\n" and first == png


def test_discord_markup_is_cleaned():
    from datetime import datetime, timezone

    from miq_discord import clean

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert clean("**太字** と *斜体* と ~~消し~~", now=now) == "太字 と 斜体 と 消し"
    assert clean("`code *x*` と __下線__", now=now) == "code *x* と 下線"
    assert clean("<@123> <@!456> <#7> <@&8>", {"123": "たろう"}) == (
        "@たろう @ユーザー #チャンネル @ロール"
    )
    assert clean("||ネタバレ||だよ") == "■■■■だよ"
    assert clean("> 引用\n# 見出し\n- 項目") == "引用\n見出し\n・項目"
    assert clean("5*3*2 = 30 と a_b_c") == "5*3*2 = 30 と a_b_c"
    assert clean("<:wave:123> \\*そのまま\\*") == "<:wave:123> *そのまま*"
    assert clean("[リンク](https://example.com)") == "リンク"
    assert clean("<t:1700000000:R>", now=now) == "2年前"
    assert clean("<t:1700000000:d>", tz=timezone.utc) == "2023/11/14"


def test_markup_can_be_disabled(tmp_path):
    raw, _, _ = miq.compose(
        AVATAR, "**x**", layout="noir", font_family="gothic", markup=False
    )
    cleaned, _, _ = miq.compose(AVATAR, "**x**", layout="noir", font_family="gothic")
    assert ImageChops.difference(raw.convert("RGB"), cleaned.convert("RGB")).getbbox()
    with pytest.raises(ValueError):
        miq.compose(AVATAR, "||  ||", layout="noir")


def test_vertical_text_rules():
    from miq_vertical import suits_vertical, upright

    assert suits_vertical("答えを急がず、問いを深めよう。Stay curious. 👩🏽‍💻")
    assert not suits_vertical("Stay curious and keep asking good questions.")
    assert not suits_vertical("مرحبا 日本語")
    assert not suits_vertical("日本語" * 200)
    assert upright("2026年!? Discord 8:30") == "２０２６年！？ Discord 8:30"


def test_orphan_lines_take_a_word_from_the_line_above():
    from miq_text import _orphan

    class Font:
        def width(self, text):
            return len(text)

    assert _orphan([["ab", "cd"], ["e"]], Font(), 10) == [["ab"], ["cd", "e"]]
    assert _orphan([["ab", "cd"], ["efg"]], Font(), 10) == [["ab", "cd"], ["efg"]]
    assert _orphan([["abcdefgh", "ij"], ["k"]], Font(), 2) == [
        ["abcdefgh", "ij"],
        ["k"],
    ]


def test_pango_wrapping_keeps_japanese_phrases():
    text = (
        "مرحبا "
        + "答えを急がず、問いを深めよう。小さな発見が、明日の景色を変えていく。"
    )
    font = maker(40)
    starts = set()
    offset = text.index("答")
    for word in units(text[offset:]):
        starts.add(offset)
        offset += len(word)
    for width in (180, 260, 340):
        rows = font.complex.wrap(text, width)
        position = 0
        for row in rows[:-1]:
            position = text.index(row, position) + len(row)
            following = len(text) - len(text[position:].lstrip())
            if following > text.index("答"):
                assert following in starts, (width, rows)


def test_greedy_wrap_respects_inseparable_leaders():
    assert units("それは……違う") == ["それは", "……", "違う"]
    assert units("100%の力") == ["100%の", "力"]


@pytest.mark.parametrize("layout", list(miq.LAYOUTS))
def test_every_layout_renders_at_the_minimum_size(layout):
    png, chosen, face = miq.render_bytes(
        AVATAR, "短い引用", "名前", "user", size=(200, 120), layout=layout,
    )
    assert chosen == layout
    with Image.open(__import__("io").BytesIO(png)) as image:
        assert image.size == (200, 120)
        assert image.convert("L").getextrema()[0] != image.convert("L").getextrema()[1]


@pytest.mark.parametrize("layout", list(miq.LAYOUTS))
@pytest.mark.parametrize("text", ["  \n\nabc\n\n", "\u200b\n本文", "a\n \u3000\nb"])
def test_blank_and_zero_width_lines_do_not_crash(layout, text):
    # A whitespace-only or zero-width-only line used to make the type-size
    # search halve forever and divide by zero in every layout but classic.
    miq.compose(AVATAR, text, "n", "h", layout=layout, seed=1, fast=True)


def test_zero_width_only_text_renders():
    canvas, _, _ = miq.compose(AVATAR, "\u200b", "n", "h", layout="noir", fast=True)
    assert canvas.size == (640, 360)


def test_private_use_placeholder_characters_are_restored():
    from miq_discord import clean

    text = "hello \uf8f00\uf8f1 and \uf8f0 0 \uf8f1"
    assert clean(text) == text
    assert clean("**bold** \uf8f00\uf8f1") == "bold \uf8f00\uf8f1"


def test_a_break_is_allowed_after_punctuation_before_latin():
    assert units("深めよう。Stay curious.") == ["深めよう。", "Stay ", "curious."]
    assert units("じゃあ8時間ホモになるか") == ["じゃあ", "8時間", "ホモに", "なるか"]


def test_ascii_art_is_detected_and_rendered():
    from miq_aa import detect

    text = (ROOT / "tests" / "aa" / "mona.txt").read_text()
    assert detect(text)[0] == "sjis"
    tux = (ROOT / "tests" / "aa" / "tux.txt").read_text()
    assert detect(tux)[0] == "mono"
    for layout in ("classic", "noir", "telop", "brush"):
        png, chosen, face = miq.render_bytes(
            AVATAR, text, "名前", "handle", size=(640, 360), layout=layout,
            font_family=miq.REGISTRY[layout].fonts[0], markup=False,
        )
        assert chosen == layout
        with Image.open(__import__("io").BytesIO(png)) as image:
            assert image.size == (640, 360)
            assert image.convert("L").getextrema()[0] != image.convert("L").getextrema()[1]


def test_cli_lists_layouts():
    result = subprocess.run(
        [sys.executable, str(ROOT / "miq.py"), "--list-layouts"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.strip().splitlines()
    assert len(lines) == len(miq.LAYOUTS)
    assert lines[0].split()[:2] == ["1", "classic"]


# Users type these numbers (miq12); they must never move.
NUMBERS = {
    1: "classic", 2: "mirrored", 5: "noir", 12: "duotone", 15: "manga",
    25: "notebook", 34: "news", 35: "fade", 36: "brush", 37: "telop",
    38: "zatsu", 39: "wanted", 40: "cutout",
}


def test_layout_numbers_are_stable_and_unique():
    numbers = [spec.number for spec in miq.REGISTRY.values()]
    assert len(set(numbers)) == len(numbers) and min(numbers) == 1
    for number, name in NUMBERS.items():
        assert miq.REGISTRY[name].number == number
        assert miq.find_layout(number) == name == miq.find_layout(str(number))
    with pytest.raises(ValueError):
        miq.find_layout(9999)


def test_orientation_draws_and_default_sizes():
    for orientation, size in (("portrait", (540, 960)), ("square", (640, 640))):
        for seed in range(6):
            png, layout, _ = miq.render_bytes(
                AVATAR, "縦の引用", "名前", orientation=orientation, seed=seed
            )
            assert orientation in miq.REGISTRY[layout].orientations
            with Image.open(io.BytesIO(png)) as image:
                assert image.size == size
    # A size alone picks the orientation; a numbered layout keeps its own
    # unless it can draw the requested one.
    _, layout, _ = miq.render_bytes(AVATAR, "x", size=(540, 960), seed=1)
    assert "portrait" in miq.REGISTRY[layout].orientations
    png, layout, _ = miq.render_bytes(AVATAR, "x", layout=37, orientation="portrait")
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (540, 960)
    png, layout, _ = miq.render_bytes(AVATAR, "x", layout=1, orientation="portrait")
    assert layout == "classic"
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (640, 360)


@pytest.mark.parametrize("mood", ["vs", "love", "talk"])
@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_dialogue_moods_render_both_orientations(mood, orientation):
    from miq_dialogue import Speaker, render_dialogue_bytes

    left = Speaker(AVATAR, "先に言った方 👩🏽‍💻 <:wave:123>", "あおい", "aoi")
    right = Speaker(AVATAR, "返信した方\n二行目" * 3, "", "naoto", attachment=True)
    png, drawn, face = render_dialogue_bytes(
        left,
        right,
        mood,
        orientation=orientation,
        seed=3,
        mentions={},
        custom_emojis={"123": AVATAR},
    )
    assert drawn == mood and face in miq.FAMILIES
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == miq.default_size(orientation)


def test_dialogue_rejects_empty_and_unknown():
    from miq_dialogue import Speaker, compose_dialogue

    with pytest.raises(ValueError):
        compose_dialogue(Speaker(AVATAR, "a"), Speaker(AVATAR, "  "), "vs")
    with pytest.raises(ValueError):
        compose_dialogue(Speaker(AVATAR, "a"), Speaker(AVATAR, "b"), "hate")


def test_catalog_pages_number_every_layout():
    from miq_catalog import pages

    result = pages()
    assert [name for name, _ in result] == ["miq-layouts-1.png", "miq-layouts-2.png"]
    for _, png in result:
        with Image.open(io.BytesIO(png)) as image:
            assert image.width >= 900 and image.height > 300


def test_cutout_removes_a_flat_background_without_the_network(monkeypatch):
    import miq_cutout

    monkeypatch.setattr(miq_cutout, "model_path", lambda name=None: None)
    miq_cutout._CACHE.clear()
    image = Image.new("RGBA", (200, 200), (250, 220, 40, 255))
    ImageDraw.Draw(image).ellipse((50, 40, 150, 190), fill=(30, 40, 120, 255))
    subject, removed = miq_cutout.cutout(image)
    assert removed
    assert subject.getpixel((2, 2))[3] == 0  # the corner is background
    assert subject.getpixel((subject.width // 2, subject.height // 2))[3] == 255
    # A picture with nothing to separate is kept whole.
    plain = Image.new("RGBA", (120, 120), (90, 160, 90, 255))
    kept, removed = miq_cutout.cutout(plain)
    assert not removed and kept.size == plain.size


@pytest.mark.parametrize("orientation", ["landscape", "portrait", "square"])
def test_cutout_layout_renders_every_orientation(orientation):
    png, layout, _ = miq.render_bytes(
        AVATAR, "後ろに大きく", "名前", "h", layout=40, orientation=orientation
    )
    assert layout == "cutout"
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == miq.default_size(orientation)
