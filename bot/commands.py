"""Parse the text people type to the bot (``miq``, ``miqmiq3 縦``, ``miq vs`` …).

Pure functions only, so every form is unit-tested without Discord.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Repeating "miq" adds messages: miqmiqmiq = the target and two before it.
MAX_COUNT = 10
COMMAND = re.compile(r"(?P<rep>(?:miq)+)(?P<num>\d+)?(?:\s+(?P<opts>.*))?", re.S)
CATALOG = {"miq台帳", "miq一覧", "miqlist", "miq list", "miq 台帳", "miq 一覧"}
HELP = {"miq help", "miq ヘルプ", "miqhelp", "miq使い方", "miq 使い方"}
OPTIONS = {
    "orientation": {
        "portrait": ("縦", "たて", "tall", "portrait", "9:16", "縦長"),
        "landscape": ("横", "よこ", "wide", "landscape", "16:9", "横長"),
        "square": ("正方形", "四角", "square", "1:1"),
    },
    "mood": {
        "vs": ("vs", "対戦", "バトル", "battle", "ビリビリ"),
        "love": ("love", "恋", "ラブ", "らぶ", "♡", "♥", "❤", "❤️", "💕", "ハート"),
        "talk": ("talk", "会話", "chat", "トーク", "line", "ライン"),
    },
    # On-request layouts, by number (40 = 切り抜き: the character steps out).
    "layout": {
        40: (
            "cutout",
            "切り抜き",
            "切りぬき",
            "きりぬき",
            "切り抜く",
            "透過",
            "飛び出し",
            "とびだし",
        ),
    },
    "category": {
        "cool": ("cool", "かっこいい", "カッコいい", "カッコイイ"),
        "cute": ("cute", "かわいい", "可愛い", "カワイイ"),
        "classic": ("classic", "定番"),
        "neta": ("neta", "ネタ", "ねた", "おもしろ", "面白", "ボケ"),
    },
}
LOOKUP = {
    word.lower(): (field, value)
    for field, values in OPTIONS.items()
    for value, words in values.items()
    for word in words
}
USAGE = """**miq の使い方**（引用したいメッセージに返信して送信）
`miq` 1件 ／ `miqmiq` `miqmiqmiq`… その人の直前の発言もまとめて1つに（最大10件・間の他人の発言は飛ばす）
`miq12` 構図を番号で指定（番号は `miq台帳` で一覧）
`miq 縦` `miq 正方形` `miq 横` 比率 ／ `miq ネタ` `miq cool` `miq cute` `miq classic` 系統
`miq vs` `miq love` `miq talk`(LINE) 返信相手との掛け合い（各自の連続した発言をまとめて描画）
`miq 切り抜き` アイコンの背景を消してキャラを前に出し、後ろに本文を大きく
返信しないで送ると直前のメッセージを引用します
組み合わせ例: `miqmiq 縦 ネタ`、`miq vs 縦`、`miq 切り抜き 縦`、`miq38`"""


@dataclass(frozen=True)
class Command:
    kind: str  # "quote", "catalog" or "help"
    count: int = 1
    layout: int | None = None
    orientation: str | None = None
    category: str | None = None
    mood: str | None = None
    unknown: tuple[str, ...] = ()


def normalize(content: str) -> str:
    """Full-width letters and spacing variants become plain ASCII-ish text."""

    text = unicodedata.normalize("NFKC", content or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def parse(content: str) -> Command | None:
    """``None`` when the message is not addressed to the bot."""

    text = normalize(content)
    if text in CATALOG:
        return Command("catalog")
    if text in HELP:
        return Command("help")
    matched = COMMAND.fullmatch(text)
    if not matched:
        return None
    found = {"orientation": None, "mood": None, "category": None}
    layout = int(matched["num"]) if matched["num"] else None
    unknown = []
    for word in (matched["opts"] or "").split():
        if word.isdigit() and layout is None:
            layout = int(word)
        elif word in LOOKUP:
            field, value = LOOKUP[word]
            if field == "layout":
                layout = value
            else:
                found[field] = value
        else:
            unknown.append(word)
    count = min(MAX_COUNT, len(matched["rep"]) // 3)
    return Command("quote", count, layout, unknown=tuple(unknown), **found)


def is_command(content: str) -> bool:
    return parse(content) is not None
