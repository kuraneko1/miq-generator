#!/usr/bin/env python3
"""Discord bot for miq-generator (discord.py 2.x).

    pip install -r requirements.txt -r bot/requirements.txt
    DISCORD_TOKEN=... python bot/example_bot.py

Reply to a message with:
- ``miq`` — quote it; ``miqmiq`` / ``miqmiqmiq`` … also take that person's
  previous messages (others in between are skipped) as one quote.
- ``miq12`` — layout number 12 (``miq台帳`` posts the numbered list).
- ``miq 縦`` / ``miq 正方形`` / ``miq cute`` … — ratio and style.
- ``miq vs`` / ``miq love`` / ``miq talk`` — the target and the person it
  replies to, each with their run of consecutive messages.
- ``miq 切り抜き`` — the character cut out in front of the quote.
- ``miq help`` — usage (and the running version).

Sent without replying, a command quotes the newest message before it.

Pictures, stickers and GIF links in the messages are drawn (animated ones as
their first frame). Downloads run concurrently and are cached; rendering runs
in a warmed process pool so the event loop is never blocked. A render worker
that dies (out of memory, a crash in a native library) used to break the
pool for good, so every later request failed silently; the pool is now
rebuilt and the request retried, and any failure is answered in the channel.
"""

from __future__ import annotations

import asyncio
import io
import logging
import multiprocessing as mp
import os
import random
import re
import subprocess
import sys
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import discord
from discord import app_commands

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miq  # noqa: E402
from bot import gather, jobs  # noqa: E402
from bot.commands import USAGE, Command, parse  # noqa: E402
from miq_layouts import by_number  # noqa: E402
from miq_words import CUSTOM  # noqa: E402

log = logging.getLogger("miq")
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
CATEGORIES = {
    "all": "すべて",
    "neta": "ネタ",
    "cool": "カッコいい",
    "cute": "かわいい",
    "classic": "定番",
}
NEXT_ORIENTATION = {
    "landscape": "portrait",
    "portrait": "square",
    "square": "landscape",
}
# Requests one person may have in flight; more get ⏳ (none are dropped silently).
MAX_PENDING = int(os.getenv("MIQ_MAX_PENDING", "2"))
FAILED = "ごめん、うまく作れませんでした。もう一度送ってみてください。"
NOT_FOUND = "引用するメッセージが見つかりませんでした。引用したいメッセージに返信して送ってください。"
MAX_DOWNLOAD = 12 * 1024 * 1024
MAX_MEDIA = 4
MAX_MEDIA_BYTES = 24 * 1024 * 1024
MAX_EMOJIS = 16
MAX_CONCURRENT_DOWNLOADS = 3
ALLOWED_MEDIA_HOSTS = {
    "cdn.discordapp.com",
    "media.discordapp.net",
    "images-ext-1.discordapp.net",
    "images-ext-2.discordapp.net",
}
URL = re.compile(r"https?://\S+")


class MediaBudget:
    """Limits shared by every message in one quote request."""

    def __init__(self):
        self.count = 0
        self.bytes = 0
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async def get(self, downloads, url, size=None, *, media=True):
        if (media and self.count >= MAX_MEDIA) or (
            size is not None and size > MAX_DOWNLOAD
        ):
            return None
        if media:
            self.count += 1
        async with self.semaphore:
            data = await downloads.get(url)
            if not data or self.bytes + len(data) > MAX_MEDIA_BYTES:
                return None
            self.bytes += len(data)
            return data


class Downloads:
    """Bytes by URL. Avatar and emoji URLs carry a content hash, so reuse is safe."""

    def __init__(self, limit=48 * 1024 * 1024):
        self.items: OrderedDict[str, bytes] = OrderedDict()
        self.limit = limit
        self.size = 0
        self.session: aiohttp.ClientSession | None = None

    async def get(self, url: str) -> bytes | None:
        if url in self.items:
            self.items.move_to_end(url)
            return self.items[url]
        try:
            data = await self._fetch(url)
        except (
            discord.HTTPException,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
        ):
            log.warning("download failed: %s", url)
            return None
        if data and len(data) <= MAX_DOWNLOAD:
            self.items[url] = data
            self.size += len(data)
            while self.size > self.limit and self.items:
                _, old = self.items.popitem(last=False)
                self.size -= len(old)
        return data

    async def _fetch(self, url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_MEDIA_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise ValueError("unexpected media URL")
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        async with self.session.get(url, allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise ValueError("redirected media URL")
            response.raise_for_status()
            if (response.content_length or 0) > MAX_DOWNLOAD:
                raise ValueError("too large")
            data = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                if len(data) + len(chunk) > MAX_DOWNLOAD:
                    raise ValueError("too large")
                data.extend(chunk)
            return bytes(data)


def version():
    """The commit being run, so ``miq help`` shows whether a deploy is current."""

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def avatar_asset(user: discord.abc.User):
    return user.display_avatar.replace(size=512, static_format="png")


async def media_of(message: discord.Message, downloads: Downloads, budget: MediaBudget):
    """``[(bytes, spoiler)]`` for attachments, stickers and image/GIF embeds."""

    pending = []
    for a in message.attachments:
        if (a.content_type or "").split(";")[0] in IMAGE_TYPES:
            pending.append((a.url, a.size, a.is_spoiler()))
    for sticker in message.stickers:
        if sticker.format is not discord.StickerFormatType.lottie:
            pending.append((sticker.url, None, False))
    for embed in message.embeds:
        if embed.type in ("gifv", "image"):
            # The proxy URL is on Discord's own media host; the original
            # (Tenor, Giphy …) is not downloaded directly.
            url = (
                embed.thumbnail and (embed.thumbnail.proxy_url or embed.thumbnail.url)
            ) or (embed.image and (embed.image.proxy_url or embed.image.url))
            if url:
                pending.append((url, None, False))
    results = await asyncio.gather(
        *(budget.get(downloads, url, size) for url, size, _ in pending)
    )
    return [(data, hidden) for data, (*_, hidden) in zip(results, pending) if data]


def text_of(message: discord.Message) -> str:
    """The content, minus links that Discord already shows as a picture/GIF."""

    text = message.content
    shown = {e.url for e in message.embeds if e.type in ("gifv", "image") and e.url}
    if shown:
        text = URL.sub(lambda m: "" if m.group() in shown else m.group(), text)
    if not text.strip() and message.stickers:
        text = message.stickers[0].name
    return text.strip()


async def emojis_of(messages, downloads: Downloads, budget: MediaBudget):
    found = {}
    for message in messages:
        for match in CUSTOM.finditer(message.content):
            if match[2] not in found and len(found) >= MAX_EMOJIS:
                break
            found[match[2]] = (match[1], match[0].startswith("<a:"), message)

    async def one(emoji_id, name, animated, message):
        emoji = discord.PartialEmoji.with_state(
            message._state, name=name, id=int(emoji_id), animated=animated
        )
        return emoji_id, await budget.get(downloads, emoji.url, media=False)

    pairs = await asyncio.gather(*(one(i, *rest) for i, rest in found.items()))
    return {emoji_id: data for emoji_id, data in pairs if data}


def mentions_of(messages):
    names = {}
    for message in messages:
        names.update({str(m.id): m.display_name for m in message.mentions})
        names.update({str(r.id): r.name for r in message.role_mentions})
        names.update({str(c.id): c.name for c in message.channel_mentions})
    return names


async def part_of(messages, downloads: Downloads, budget: MediaBudget) -> jobs.Part:
    """Merge one person's messages (oldest first) into one quote part."""

    author = messages[-1].author
    asset = avatar_asset(author)
    avatar, *media = await asyncio.gather(
        budget.get(downloads, asset.url, media=False),
        *(media_of(m, downloads, budget) for m in messages),
    )
    text = "\n".join(t for t in (text_of(m) for m in messages) if t)
    return jobs.Part(
        avatar or b"",
        text,
        author.display_name,
        author.name,
        [item for group in media for item in group],
        messages[-1].created_at.astimezone(),
    )


async def build(groups, command: Command, downloads: Downloads) -> jobs.Request:
    everything = [m for group in groups for m in group]
    budget = MediaBudget()
    parts, emojis = await asyncio.gather(
        asyncio.gather(*(part_of(group, downloads, budget) for group in groups)),
        emojis_of(everything, downloads, budget),
    )
    return jobs.Request(
        list(parts),
        mood=command.mood,
        layout=command.layout,
        orientation=command.orientation,
        category=command.category,
        seed=random.getrandbits(32),
        emojis=emojis,
        mentions=mentions_of(everything),
    )


async def replied_to(message: discord.Message):
    ref = message.reference
    if ref is None or ref.message_id is None or ref.channel_id != message.channel.id:
        return None
    if isinstance(ref.resolved, discord.Message):
        return ref.resolved
    try:
        return await message.channel.fetch_message(ref.message_id)
    except discord.HTTPException:
        return None


class QuoteBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.workers = max(1, (os.cpu_count() or 2) - 1)
        self.pool = self._pool()
        self.downloads = Downloads()
        self.pending: dict[int, int] = {}
        self.catalog: asyncio.Future | None = None
        self.version = version()

    def _pool(self):
        # Fresh interpreters (not fork) so workers never inherit the event loop.
        method = "forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn"
        return ProcessPoolExecutor(
            max_workers=self.workers,
            initializer=miq.warmup,
            mp_context=mp.get_context(method),
        )

    async def job(self, function, *args):
        """Run ``function`` in the render pool, rebuilding a broken pool once.

        A worker killed by the OOM killer or a native crash marks the whole
        ``ProcessPoolExecutor`` as broken, and it never recovers by itself.
        """

        loop = asyncio.get_running_loop()
        for attempt in (1, 2):
            pool = self.pool
            try:
                return await loop.run_in_executor(pool, function, *args)
            except BrokenProcessPool:
                if attempt == 2:
                    raise
                log.error("render pool broke; starting a new one")
                if self.pool is pool:
                    self.pool = self._pool()
                    pool.shutdown(wait=False, cancel_futures=True)

    async def setup_hook(self):
        self.tree.add_command(
            app_commands.ContextMenu(name="Make it a Quote", callback=quote_menu)
        )
        await self.tree.sync()
        # Start and warm every worker now, not on the first users' requests.
        start = time.perf_counter()
        await asyncio.gather(*(self.job(jobs.ready) for _ in range(self.workers)))
        log.info(
            "miq %s: %d render workers ready in %.0fms",
            self.version,
            self.workers,
            (time.perf_counter() - start) * 1000,
        )
        self.catalog = asyncio.ensure_future(self.job(jobs.catalog))

    async def close(self):
        if self.downloads.session:
            await self.downloads.session.close()
        self.pool.shutdown(cancel_futures=True)
        await super().close()

    async def render(self, request: jobs.Request):
        start = time.perf_counter()
        png, caption = await self.job(jobs.render, request)
        log.info("render %.0fms (%s)", (time.perf_counter() - start) * 1000, caption)
        return discord.File(io.BytesIO(png), "quote.png"), f"-# {caption}"

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        command = parse(message.content)
        if command is None:
            return
        try:
            await self.answer(message, command)
        except Exception:
            # Never fail silently: the person sees that something went wrong.
            log.exception("miq failed for %r", message.content)
            await say(message, FAILED)

    async def answer(self, message: discord.Message, command: Command):
        if command.kind == "help":
            await message.reply(f"{USAGE}\n-# miq {self.version}", mention_author=False)
            return
        if command.kind == "catalog":
            await self.send_catalog(message)
            return
        if command.unknown:
            await message.reply(
                f"`{' '.join(command.unknown)}` はわかりませんでした。\n{USAGE}",
                mention_author=False,
            )
            return
        if command.layout is not None and by_number(command.layout) is None:
            numbers = "・".join(
                str(spec.number)
                for spec in sorted(miq.REGISTRY.values(), key=lambda s: s.number)
            )
            await message.reply(
                f"構図 {command.layout} 番はありません（{numbers}、`miq台帳` で一覧）。",
                mention_author=False,
            )
            return
        if command.mood and command.orientation == "square":
            await message.reply(
                "掛け合いは横か縦だけに対応しています。",
                mention_author=False,
            )
            return

        def history(m):
            return message.channel.history(limit=gather.SCAN_LIMIT, before=m)

        target = await replied_to(message)
        if target is None:
            # Not a reply: quote the newest message before the command.
            try:
                target = await gather.latest(message, history)
            except discord.HTTPException:
                target = None
        if target is None:
            await message.reply(NOT_FOUND, mention_author=False)
            return
        user = message.author.id
        if self.pending.get(user, 0) >= MAX_PENDING:
            try:
                await message.add_reaction("⏳")
            except discord.HTTPException:
                pass
            return
        self.pending[user] = self.pending.get(user, 0) + 1
        try:
            async with message.channel.typing():
                start = time.perf_counter()
                groups = await self.gather(target, command, history)
                if len(groups) == 1:
                    command = replace(command, mood=None)
                request = await build(groups, command, self.downloads)
                fetched = time.perf_counter()
                file, caption = await self.render(request)
            sent = time.perf_counter()
            await message.reply(
                caption, file=file, view=Controls(self, request), mention_author=False
            )
        finally:
            self.pending[user] -= 1
            if not self.pending[user]:
                del self.pending[user]
        log.info(
            "miq fetch=%.0fms render=%.0fms upload=%.0fms",
            (fetched - start) * 1000,
            (sent - fetched) * 1000,
            (time.perf_counter() - sent) * 1000,
        )

    async def gather(self, target, command: Command, history):
        """The message groups to draw; degrades to the target alone when the
        bot may not read the channel history (no 「メッセージ履歴を読む」)."""

        try:
            if command.mood:
                groups = await gather.conversation(target, history, replied_to)
                if groups is not None:
                    return list(groups)
            return [await gather.same_author(target, command.count, history)]
        except discord.Forbidden:
            log.warning("no permission to read history in #%s", target.channel)
            return [[target]]

    async def send_catalog(self, message: discord.Message):
        async with message.channel.typing():
            try:
                pages = await asyncio.shield(self.catalog)
            except Exception:
                log.exception("catalog failed")
                self.catalog = asyncio.ensure_future(self.job(jobs.catalog))
                pages = await asyncio.shield(self.catalog)
        files = [discord.File(io.BytesIO(png), name) for name, png in pages]
        await message.reply(files=files, mention_author=False)


async def say(message: discord.Message, text: str):
    try:
        await message.reply(text, mention_author=False)
    except discord.HTTPException:
        log.warning("could not reply in #%s", message.channel)


class Controls(discord.ui.View):
    """🎲 redraw, 📐 ratio and a style menu; all edit the same message."""

    def __init__(self, bot: QuoteBot, request: jobs.Request):
        super().__init__(timeout=900)
        self.bot = bot
        self.request = request
        if request.mood:
            self.remove_item(self.category)

    async def redraw(self, interaction: discord.Interaction, request: jobs.Request):
        await interaction.response.defer()
        try:
            file, caption = await self.bot.render(request)
        except Exception:
            log.exception("redraw failed")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        self.request = request
        await interaction.edit_original_response(
            content=caption, attachments=[file], view=self
        )

    @discord.ui.button(
        emoji="🎲", label="別の構図", style=discord.ButtonStyle.secondary
    )
    async def reroll(self, interaction: discord.Interaction, _button):
        await self.redraw(
            interaction,
            replace(self.request, layout=None, seed=random.getrandbits(32)),
        )

    @discord.ui.button(emoji="📐", label="縦横", style=discord.ButtonStyle.secondary)
    async def ratio(self, interaction: discord.Interaction, _button):
        current = self.request.orientation or "landscape"
        following = NEXT_ORIENTATION[current]
        if self.request.mood and following == "square":
            following = "landscape"
        # A layout that draws the next orientation too (e.g. 切り抜き) stays.
        spec = by_number(self.request.layout) if self.request.layout else None
        keep = spec is not None and following in spec.orientations
        await self.redraw(
            interaction,
            replace(
                self.request,
                orientation=following,
                layout=self.request.layout if keep else None,
                seed=random.getrandbits(32),
            ),
        )

    @discord.ui.select(
        placeholder="系統をしぼる",
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in CATEGORIES.items()
        ],
    )
    async def category(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        choice = select.values[0]
        await self.redraw(
            interaction,
            replace(
                self.request,
                category=None if choice == "all" else choice,
                layout=None,
                seed=random.getrandbits(32),
            ),
        )


async def quote_menu(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer(thinking=True)
    bot: QuoteBot = interaction.client
    try:
        request = await build([[message]], Command("quote"), bot.downloads)
        file, caption = await bot.render(request)
    except Exception:
        log.exception("quote menu failed")
        await interaction.followup.send(FAILED)
        return
    await interaction.followup.send(caption, file=file, view=Controls(bot, request))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    QuoteBot().run(os.environ["DISCORD_TOKEN"], log_handler=None)
