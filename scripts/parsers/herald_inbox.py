"""Буфер захвата herald: то, что бот вычитал из рабочих групп.

Машинный источник, и в одном отношении лучший из всех: Bot API отдаёт
**настоящего автора пересланного сообщения**, а не того, кто его переслал.
Копипаста из Telegram этого не умеет и подписывает пересланное пересылающим —
на живом чате так вышло с 16 сообщениями из 21.

Надёжность здесь **разная внутри одной пачки**, и это единственный формат, где
так. Прямое сообщение и пересылка от опознанного автора — `reliable`; пересылка
от того, кто скрыл себя настройками, — `forwarder-shown`, потому что известно
только показанное имя. Поэтому парсер проставляет надёжность каждому сообщению
отдельно, а материал за день получает самую слабую из вошедших в него.

Источник — файл JSON, который отдаёт команда `inbox_fetch`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Message, ParseResult, Parser

# §4а.3: при `forwarder-shown` приписывать материал показанному имени запрещено.
# Допустимая формулировка — «переслано <кем>, автор не установлен».
UNKNOWN_AUTHOR = "автор не установлен"

# По этим полям формат опознаётся. Их отсутствие — отказ, а не тихая порча:
# без `author_name` каждое сообщение стало бы «неизвестно», и импорт прошёл бы.
REQUIRED_KEYS = {"chat_id", "message_id", "chat_slug", "date", "author_name"}
# Поля, которых нет ни у одного другого формата: по ним отличаем «похоже на
# herald, но неполное» от «это вообще не herald».
HERALD_MARKERS = {"chat_slug", "origin_type", "media_note", "author_name", "local_path"}

# Как вид вложения из Bot API называется в зонах RAW стандарта (§2).
MEDIA_KIND = {
    "voice": "voice",
    "video_note": "voice",
    "audio": "voice",
    "photo": "photo",
    "sticker": "photo",
    "document": "document",
    "video": "document",
    "animation": "document",
}


class HeraldInboxParser(Parser):
    name = "herald-inbox"
    label = "буфер захвата herald"
    max_fidelity = "verbatim"
    attribution = "reliable"

    @classmethod
    def _rows(cls, source: Path) -> list[dict] | None:
        path = source
        if path.is_dir():
            candidate = path / "inbox.json"
            if not candidate.is_file():
                return None
            path = candidate
        if not path.is_file() or path.suffix.lower() != ".json":
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict):
            data = data.get("messages")
        if not isinstance(data, list) or not data:
            return None
        head = data[0]
        if not isinstance(head, dict):
            return None
        # Отличаем от других JSON по набору полей, а не по имени файла: имя
        # человек даёт какое захочет.
        return data if REQUIRED_KEYS <= set(head) else None

    @classmethod
    def missing_keys(cls, source: Path) -> set[str]:
        """Каких обязательных полей нет — чтобы отказ был диагнозом, а не «нет»."""
        path = source / "inbox.json" if source.is_dir() else source
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if isinstance(data, dict):
            data = data.get("messages")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return set()
        head = set(data[0])
        # Подсказывать про herald имеет смысл, только если источник на него
        # похож. Иначе любой нераспознанный JSON — включая выгрузку Telegram
        # под переименованным файлом — получал совет запустить inbox_export,
        # а настоящее сообщение о неизвестном формате пряталось.
        if not (head & HERALD_MARKERS) or len(REQUIRED_KEYS & head) < 2:
            return set()
        return REQUIRED_KEYS - head

    @classmethod
    def detect(cls, source: Path) -> bool:
        return cls._rows(source) is not None

    def parse(self, source: Path) -> ParseResult:
        rows = self._rows(source)
        if rows is None:
            raise ValueError(f"не похоже на выгрузку буфера herald: {source}")
        anchor = source / "inbox.json" if source.is_dir() else source
        slugs = sorted({str(row.get("chat_slug") or "") for row in rows} - {""})

        result = ParseResult(
            attribution=self.attribution,
            title=slugs[0] if len(slugs) == 1 else "herald inbox",
            anchor=anchor,
            source_id=f"herald:{slugs[0]}" if len(slugs) == 1 else "herald",
        )
        if len(slugs) > 1:
            # Не примечание, а отказ: `message_id` уникален внутри чата, и при
            # смешении часть сообщений молча отсеивалась как повтор.
            raise ValueError(
                "в выгрузке несколько чатов: " + ", ".join(slugs)
                + ". Выгружай по одному: один архив — одна тема, а номера "
                "сообщений в разных чатах повторяются"
            )

        skipped_media = 0
        for row in rows:
            author, via, attribution = _authorship(row)
            shown = str(row.get("origin_name") or "").strip()
            media = row.get("local_path")
            note = None
            if row.get("file_id") and not media:
                skipped_media += 1
                # §3.5 и §5: у материала, который существует, но не добыт,
                # заводится запись `missing` — это задача, а не факт утраты.
                # Для этого нужен ожидаемый путь: без него сообщение с
                # недоехавшим голосовым не попадает в хвосты вовсе, и ни
                # `gaps`, ни сводка о нём не узнают.
                kind = str(row.get("media_kind") or "file")
                name = str(row.get("file_name") or f"{kind}-{row.get('message_id')}")
                media = f"files/{row.get('chat_slug')}/{row.get('message_id')}_{name}"
                note = (
                    f"в буфер не попало: {row.get('media_note') or 'файла нет'}"
                )
            result.messages.append(Message(
                date=str(row.get("date") or ""),
                author=author,
                text=str(row.get("text") or ""),
                via=via,
                media=media,
                media_kind=MEDIA_KIND.get(str(row.get("media_kind") or "")),
                shown_as=shown if author == UNKNOWN_AUTHOR and shown else None,
                note=note,
                msg_id=f"{row.get('chat_slug')}:{row.get('message_id')}",
                reply_to=str(row.get("reply_to") or "") or None,
                attribution=attribution,
            ))
            if row.get("media_note"):
                result.notes.append(
                    f"сообщение {row.get('message_id')}: {row['media_note']}"
                )
        if skipped_media:
            result.notes.append(
                f"вложений без файла: {skipped_media} — оригиналы остались в Telegram"
            )
        result.participants = sorted({m.author for m in result.messages if m.author})
        return result


def _authorship(row: dict) -> tuple[str, str | None, str]:
    """Кто автор, кто переслал и насколько этому можно верить.

    `origin_type` приходит прямо из Bot API:

    - его нет — сообщение написал тот, кто его отправил, `reliable`;
    - `user` — переслано, настоящий автор известен, `reliable`, а отправитель
      записывается как переславший;
    - `hidden_user` — автор скрыл себя, известно только показанное имя;
      приписывать материал ему **запрещено** (§4а), поэтому `forwarder-shown`;
    - `chat` / `channel` — источником выступает чат или канал, автор внутри него
      неизвестен.
    """
    sender = str(row.get("author_name") or row.get("author_username") or "неизвестно")
    origin = str(row.get("origin_type") or "")
    shown = str(row.get("origin_name") or "").strip()

    if not origin:
        return sender, None, "reliable"
    if origin == "user" and shown:
        return shown, sender, "reliable"
    # Всё остальное — пересылка, автора которой мы не установили. Раньше такие
    # подписывались отправителем, и информация о пересылке терялась вовсе: ровно
    # та ошибка, ради предотвращения которой формат и выбран. Имя, если оно
    # есть, сохраняем показанным, но автором не объявляем — §4а.3.
    return UNKNOWN_AUTHOR, sender, "forwarder-shown"
