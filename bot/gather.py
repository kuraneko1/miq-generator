"""Which messages a request covers.

* ``same_author``: ``miqmiq…`` — the target plus that person's earlier
  messages, skipping anyone else in between.
* ``conversation``: ``miq vs`` / ``love`` / ``talk`` — two people, each with
  their whole run of consecutive messages.

Only ``author.id``, ``created_at``, ``content``, ``type`` and attachments are
read, so tests use plain stand-ins. ``history(message)`` must return an async
iterable of the messages before ``message``, newest first (Discord's
``channel.history(before=message)``).
"""

from __future__ import annotations

from datetime import timedelta

from bot.commands import is_command

# miqmiq…: how far back to look for the same person's messages.
SCAN_LIMIT = 100
WINDOW = timedelta(hours=1)
# A "run" of consecutive messages: gap between them and its length.
RUN_GAP = timedelta(minutes=10)
RUN_MAX = 8


def is_regular(message) -> bool:
    kind = getattr(getattr(message, "type", None), "name", "default")
    return kind in ("default", "reply")


def has_content(message) -> bool:
    return bool(
        (message.content or "").strip()
        or getattr(message, "attachments", None)
        or getattr(message, "stickers", None)
        or getattr(message, "embeds", None)
    )


def usable(message) -> bool:
    """A message worth quoting: a person's, not a system line, not a miq command,
    not empty. Bots (this one's own quote images included) never count, or
    ``miq vs`` would pair someone with the bot."""

    return (
        not getattr(message.author, "bot", False)
        and is_regular(message)
        and has_content(message)
        and not is_command(message.content)
    )


async def latest(command, history):
    """The newest usable message before ``command``: the target of a ``miq``
    sent without replying. ``None`` if nothing recent qualifies."""

    async for message in history(command):
        if command.created_at - message.created_at > WINDOW:
            break
        if usable(message):
            return message
    return None


async def same_author(target, count, history):
    """The target and up to ``count - 1`` earlier messages by the same person.

    Others' messages in between are skipped; the search stops after
    ``SCAN_LIMIT`` messages or ``WINDOW`` before the target. Oldest first.
    """

    picked = [target]
    if count > 1:
        async for message in history(target):
            if target.created_at - message.created_at > WINDOW:
                break
            if message.author.id == target.author.id and usable(message):
                picked.append(message)
                if len(picked) >= count:
                    break
    return picked[::-1]


async def run(anchor, history):
    """``anchor`` and the same author's consecutive messages just before it.

    Returns ``(messages_oldest_first, previous)`` where ``previous`` is the
    other person's message that ended the run (``None`` if the run ended for
    another reason).
    """

    picked = [anchor]
    previous = None
    async for message in history(anchor):
        if message.author.id != anchor.author.id:
            if usable(message):
                previous = message
                break
            if is_command(message.content) or getattr(message.author, "bot", False):
                continue  # miq commands and bot replies sit between the turns
            break
        if picked[-1].created_at - message.created_at > RUN_GAP:
            break
        if not usable(message):
            continue
        picked.append(message)
        if len(picked) >= RUN_MAX:
            break
    return picked[::-1], previous


async def conversation(target, history, replied_to):
    """``(earlier_run, target_run)`` for a two-person exchange, or ``None``.

    The other person is whoever the target replies to; without a reply (or a
    reply to oneself) it is whoever spoke just before the target's run.
    ``replied_to(message)`` returns the referenced message or ``None``.
    """

    mine, previous = await run(target, history)
    other = await replied_to(target)
    if other is None or other.author.id == target.author.id or not usable(other):
        other = previous
    if other is None:
        return None
    theirs, _ = await run(other, history)
    return theirs, mine
