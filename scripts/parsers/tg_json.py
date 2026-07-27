"""Выгрузка Telegram Desktop / AyuGram в формате JSON.

Машинный источник: содержит `forwarded_from`, то есть знает настоящего автора
пересланного сообщения. Это единственный из известных форматов Telegram, который
даёт надёжное авторство, — остальные показывают того, кто переслал.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Message, ParseResult, Parser, flatten_text

MEDIA_KIND = {
    "voice_message": "voice",
    "audio_file": "voice",
    "video_message": "document",
    "video_file": "document",
    "animation": "document",
    "sticker": "document",
}


class TelegramJsonParser(Parser):
    name = "tg-json"
    label = "выгрузка Telegram (result.json)"
    max_fidelity = "verbatim"
    attribution = "reliable"

    @classmethod
    def _manifest(cls, source: Path) -> Path | None:
        if source.is_file() and source.name == "result.json":
            return source
        candidate = source / "result.json"
        return candidate if candidate.is_file() else None

    @classmethod
    def detect(cls, source: Path) -> bool:
        path = cls._manifest(source)
        if path is None:
            return False
        try:
            head = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(head, dict) and isinstance(head.get("messages"), list)

    def parse(self, source: Path) -> ParseResult:
        path = self._manifest(source)
        if path is None:
            raise ValueError(f"нет result.json в {source}")
        data = json.loads(path.read_text(encoding="utf-8"))

        result = ParseResult(
            attribution=self.attribution,
            title=str(data.get("name") or path.parent.name),
            anchor=path,
            source_id=f"tg{data.get('id', '')}",
        )

        forwarded = 0
        for entry in data.get("messages", []):
            # Служебные записи (создание топика, вход в чат) содержанием не
            # являются — в архив идёт материал, а не журнал событий Telegram.
            if entry.get("type") != "message":
                continue

            sender = str(entry.get("from") or entry.get("actor") or "неизвестно")
            origin_author = entry.get("forwarded_from")
            if origin_author and origin_author != sender:
                author, via = str(origin_author), sender
                forwarded += 1
            else:
                author, via = sender, None

            media = entry.get("file") or entry.get("photo")
            if media:
                kind = "photo" if entry.get("photo") else MEDIA_KIND.get(
                    str(entry.get("media_type") or ""), "document"
                )
            else:
                kind = None

            result.messages.append(Message(
                date=str(entry.get("date") or ""),
                author=author,
                via=via,
                text=flatten_text(entry.get("text")),
                media=str(media) if media else None,
                media_kind=kind,
                msg_id=str(entry.get("id")) if entry.get("id") else None,
                reply_to=str(entry["reply_to_message_id"]) if entry.get("reply_to_message_id") else None,
            ))

        result.participants = sorted({m.author for m in result.messages})
        if forwarded:
            result.notes.append(
                f"пересланных сообщений с раскрытым автором: {forwarded} — "
                "в тексте они помечены «переслал»"
            )
        return result
