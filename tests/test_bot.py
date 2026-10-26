"""Bot logic without Discord: command parsing, message gathering, render jobs."""

import asyncio
import io
import struct
import sys
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miq  # noqa: E402
import miq_catalog  # noqa: E402
from bot import gather, jobs  # noqa: E402
from bot.example_bot import (  # noqa: E402
    MAX_CONCURRENT_DOWNLOADS,
    MAX_MEDIA,
    MAX_MEDIA_BYTES,
    Downloads,
    MediaBudget,
    media_of,
)
from bot.commands import MAX_COUNT, Command, parse  # noqa: E402
from miq_dialogue import Speaker, compose_dialogue  # noqa: E402
from miq_images import collage  # noqa: E402
from miq_fonts import FontFamily  # noqa: E402

AVATAR = ROOT / "demo-avatar.png"
T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


# ----- commands ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("miq", Command("quote")),
        ("MIQ", Command("quote")),
        ("ｍｉｑ", Command("quote")),
        ("miqmiq", Command("quote", 2)),
        ("miqmiqmiq", Command("quote", 3)),
        ("miq" * 30, Command("quote", MAX_COUNT)),
        ("miq12", Command("quote", 1, 12)),
        ("miqmiq3", Command("quote", 2, 3)),
        ("miq 12", Command("quote", 1, 12)),
        ("miq 縦", Command("quote", orientation="portrait")),
        ("miq tall", Command("quote", orientation="portrait")),
        ("miq 9:16", Command("quote", orientation="portrait")),
        ("miq 正方形", Command("quote", orientation="square")),
        ("miq 横", Command("quote", orientation="landscape")),
        ("miq vs", Command("quote", mood="vs")),
        ("miq 対戦", Command("quote", mood="vs")),
        ("miq love", Command("quote", mood="love")),
        ("miq ❤️", Command("quote", mood="love")),
        ("miq 会話", Command("quote", mood="talk")),
        ("miqmiq 縦 cute", Command("quote", 2, orientation="portrait", category="cute")),
        ("miq vs　縦", Command("quote", mood="vs", orientation="portrait")),
        ("miq28", Command("quote", 1, 28)),
        ("miq 切り抜き", Command("quote", 1, 40)),
        ("miq cutout 縦", Command("quote", 1, 40, orientation="portrait")),
        ("miq ネタ", Command("quote", category="neta")),
        ("miq line", Command("quote", mood="talk")),
        ("miq 何それ", Command("quote", unknown=("何それ",))),
        ("miq台帳", Command("catalog")),
        ("miq 一覧", Command("catalog")),
        ("miqlist", Command("catalog")),
        ("miq help", Command("help")),
    ],
)
def test_parse(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize("text", ["", "hello", "miq?", "miqm", "あmiq", "miq3miq", "mi q"])
def test_parse_ignores_other_messages(text):
    assert parse(text) is None


# ----- gathering ------------------------------------------------------------


def msg(author, minute, content="text", *, reply=None, kind="default", **extra):
    return SimpleNamespace(
        author=SimpleNamespace(id=author, bot=extra.get("bot", False)),
        created_at=T0 + timedelta(minutes=minute),
        content=content,
        type=SimpleNamespace(name=kind),
        attachments=extra.get("attachments", []),
        stickers=[],
        embeds=[],
        reply=reply,
        id=f"{author}@{minute}:{content}",
    )


def channel(*messages):
    """``history(m)`` over a chronological list, newest first like Discord."""

    def history(anchor):
        index = messages.index(anchor)

        async def generate():
            for message in reversed(messages[:index]):
                yield message

        return generate()

    async def replied_to(message):
        return message.reply

    return history, replied_to


def run(coroutine):
    return asyncio.run(coroutine)


def contents(messages):
    return [m.content for m in messages]


def test_same_author_skips_others_and_commands():
    a1, b1, a2, a3, cmd, b2, a4 = (
        msg(1, 0, "a1"),
        msg(2, 1, "b1"),
        msg(1, 2, "a2"),
        msg(1, 3, "a3"),
        msg(1, 4, "miq"),
        msg(2, 5, "b2"),
        msg(1, 6, "a4"),
    )
    history, _ = channel(a1, b1, a2, a3, cmd, b2, a4)
    assert contents(run(gather.same_author(a4, 1, history))) == ["a4"]
    assert contents(run(gather.same_author(a4, 3, history))) == ["a2", "a3", "a4"]
    assert contents(run(gather.same_author(a4, 10, history))) == ["a1", "a2", "a3", "a4"]


def test_same_author_stops_at_the_time_window_and_system_lines():
    old, join, recent, target = (
        msg(1, -120, "old"),
        msg(1, -5, "joined", kind="new_member"),
        msg(1, -2, "recent"),
        msg(1, 0, "target"),
    )
    history, _ = channel(old, join, recent, target)
    assert contents(run(gather.same_author(target, 5, history))) == ["recent", "target"]


def test_conversation_uses_the_reply_and_each_run():
    b1, b2 = msg(2, 0, "b1"), msg(2, 1, "b2")
    c1 = msg(3, 2, "c1")
    a1 = msg(1, 3, "a1")
    a2 = msg(1, 4, "a2", reply=b2)
    a3 = msg(1, 5, "a3")
    history, replied_to = channel(b1, b2, c1, a1, a2, a3)
    theirs, mine = run(gather.conversation(a3, history, replied_to))
    # a3 is not itself a reply: the other person is whoever spoke before the run.
    assert contents(mine) == ["a1", "a2", "a3"] and contents(theirs) == ["c1"]
    theirs, mine = run(gather.conversation(a2, history, replied_to))
    assert contents(mine) == ["a1", "a2"] and contents(theirs) == ["b1", "b2"]


def test_conversation_run_breaks_on_long_gaps_and_self_replies():
    b = msg(2, 0, "b")
    a_old = msg(1, 1, "a old")
    a_new = msg(1, 30, "a new", reply=a_old)
    history, replied_to = channel(b, a_old, a_new)
    # Self-reply and a 29 minute gap: no partner is found.
    assert run(gather.conversation(a_new, history, replied_to)) is None
    alone = msg(1, 0, "alone")
    history, replied_to = channel(alone)
    assert run(gather.conversation(alone, history, replied_to)) is None


# ----- jobs -----------------------------------------------------------------


def png_bytes(size, color):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def bomb_bytes(size):
    """A small PNG file whose header claims an enormous canvas."""
    raw = bytearray(png_bytes((16, 16), "red"))
    start = raw.find(b"IHDR")
    struct.pack_into(">II", raw, start + 4, *size)
    struct.pack_into(">I", raw, start + 17, zlib.crc32(bytes(raw[start : start + 17])))
    return bytes(raw)


def gif_bytes():
    frames = [Image.new("P", (60, 40), i) for i in (1, 2)]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:])
    return buffer.getvalue()


def test_collage_layouts():
    pictures = [Image.new("RGB", (300, 200), c) for c in ("red", "blue", "green", "white")]
    assert collage(pictures[:1]).size == (300, 200)
    for count in (2, 3, 4):
        assert collage(pictures[:count], size=400).size == (400, 400)


def test_render_job_quote_with_media_and_layout_number():
    part = jobs.Part(
        AVATAR.read_bytes(),
        "",
        "名前",
        "user",
        [(png_bytes((400, 300), "red"), False), (gif_bytes(), True), (b"broken", False)],
        T0,
    )
    png, caption = jobs.render(
        jobs.Request([part], layout=39, orientation="portrait", seed=1)
    )
    assert caption.startswith("#39 ")
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == miq.default_size("portrait")


def test_render_job_survives_a_missing_avatar():
    png, caption = jobs.render(jobs.Request([jobs.Part(b"", "本文", "名前", "u")], seed=4))
    assert png.startswith(b"\x89PNG") and caption.startswith("#")


def test_render_job_dialogue():
    a = jobs.Part(AVATAR.read_bytes(), "先に言った", "B", "b", [], T0)
    b = jobs.Part(AVATAR.read_bytes(), "返信した\n続き", "A", "a", [], T0)
    png, caption = jobs.render(jobs.Request([a, b], mood="vs", orientation="portrait", seed=2))
    assert caption.startswith("対戦")
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == miq.default_size("portrait")


async def _media_groups(messages, downloads, budget):
    return await asyncio.gather(*(media_of(m, downloads, budget) for m in messages))


def test_media_budget_limits_count_bytes_and_parallel_downloads():
    class FakeDownloads:
        active = 0
        peak = 0
        calls = 0

        async def get(self, url):
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls += 1
            await asyncio.sleep(0)
            self.active -= 1
            return b"x" * (7 * 1024 * 1024)

    def attachment(i):
        return SimpleNamespace(
            url=f"https://cdn.discordapp.com/attachments/{i}",
            size=7 * 1024 * 1024,
            content_type="image/png",
            is_spoiler=lambda: False,
        )

    messages = [
        SimpleNamespace(attachments=[attachment(i)], stickers=[], embeds=[])
        for i in range(10)
    ]
    downloads, budget = FakeDownloads(), MediaBudget()
    results = run(_media_groups(messages, downloads, budget))
    assert downloads.calls == MAX_MEDIA
    assert downloads.peak <= MAX_CONCURRENT_DOWNLOADS
    assert sum(len(data) for group in results for data, _ in group) <= MAX_MEDIA_BYTES
    assert sum(map(len, results)) == 3


def test_download_rejects_untrusted_hosts_redirects_and_oversize_stream():
    class Response:
        def __init__(self, status, chunks):
            self.status = status
            self.content_length = None
            self.content = self
            self.chunks = chunks

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        async def iter_chunked(self, _size):
            for chunk in self.chunks:
                yield chunk

    class Session:
        def __init__(self, response):
            self.response = response
            self.calls = []

        def get(self, url, **options):
            self.calls.append((url, options))
            return self.response

    downloads = Downloads()
    downloads.session = Session(Response(200, [b"ok"]))
    assert run(downloads.get("https://127.0.0.1/private")) is None
    assert run(downloads.get("https://cdn.discordapp.com.evil.test/img")) is None
    assert downloads.session.calls == []
    assert run(downloads.get("https://cdn.discordapp.com/img")) == b"ok"
    assert downloads.session.calls[0][1]["allow_redirects"] is False

    downloads.session = Session(Response(302, [b"redirect"]))
    assert run(downloads.get("https://cdn.discordapp.com/redirect")) is None
    downloads.session = Session(Response(200, [b"x" * (12 * 1024 * 1024), b"y"]))
    assert run(downloads.get("https://cdn.discordapp.com/large")) is None


def test_picture_skips_image_with_too_many_pixels():
    large = png_bytes((4000, 3100), "red")
    part = jobs.Part(b"", "text", "name", "handle", [(large, False)])
    image, attachment = jobs.picture(part)
    assert image.size == (256, 256)
    assert not attachment


def test_picture_ignores_decompression_bomb():
    part = jobs.Part(b"", "text", "name", "handle", [(bomb_bytes((30000, 30000)), False)])
    image, attachment = jobs.picture(part)
    assert image.size == (256, 256)
    assert not attachment


def test_square_dialogue_is_rejected():
    speaker = Speaker(AVATAR, "本文")
    with pytest.raises(ValueError, match="横か縦"):
        compose_dialogue(speaker, speaker, orientation="square")


def test_catalog_renders_when_layouts_first_font_is_unavailable(monkeypatch):
    original_installed = FontFamily.installed
    monkeypatch.setattr(
        FontFamily,
        "installed",
        lambda self: self.key != "gothic" and original_installed(self),
    )
    monkeypatch.setattr(
        miq_catalog.miq, "REGISTRY", {"classic": miq.REGISTRY["classic"]}
    )
    assert miq_catalog._font(20)
    tiles = miq_catalog.thumbnails("landscape")
    assert len(tiles) == 1 and tiles[0][2].size == miq.default_size()


def test_bots_are_never_quoted_or_paired():
    # The bot's own quote images used to become the "other person" of miq vs.
    b = msg(2, 0, "b")
    a = msg(1, 1, "a", reply=b)
    image = msg(99, 2, "-# #5 ノワール", bot=True)
    a2 = msg(1, 3, "a2")
    history, replied_to = channel(b, a, image, a2)
    assert not gather.usable(image)
    theirs, mine = run(gather.conversation(a2, history, replied_to))
    assert contents(theirs) == ["b"] and contents(mine) == ["a", "a2"]


def test_a_command_without_a_reply_quotes_the_latest_message():
    b = msg(2, 0, "b")
    old = msg(3, -200, "old")
    image = msg(99, 1, "-# #1", bot=True)
    other_command = msg(3, 2, "miq")
    command = msg(1, 3, "miq vs")
    history, _ = channel(old, b, image, other_command, command)
    assert run(gather.latest(command, history)).content == "b"
    history, _ = channel(old, command)
    assert run(gather.latest(command, history)) is None


def test_a_broken_render_pool_is_rebuilt():
    """A worker killed by the OOM killer used to break every later request."""

    import os
    import signal

    from bot.example_bot import QuoteBot

    bot = QuoteBot.__new__(QuoteBot)
    bot.workers = 1
    bot.pool = bot._pool()

    async def scenario():
        assert await bot.job(jobs.ready)
        for pid in list(bot.pool._processes):
            os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.3)
        return await bot.job(jobs.ready)

    try:
        assert run(scenario())
    finally:
        bot.pool.shutdown(cancel_futures=True)
