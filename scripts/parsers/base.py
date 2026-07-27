"""Контракт парсера источника.

Парсеры зависят от этого контракта; ядро о конкретных парсерах не знает и
обращается к ним только через реестр. Новый источник — новый файл, ничего
существующего не трогается.

Обязанность парсера — не только достать текст, но и **честно объявить, насколько
надёжно авторство** в его формате. Это не деталь реализации: выгрузка Telegram
знает, кто автор пересланного сообщения, а копипаста из того же Telegram — нет,
и приписывает всё пересылающему. Формат, который этого не сообщает, тихо
портит архив.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# Насколько можно доверять имени рядом с текстом.
ATTRIBUTION = ("reliable", "forwarder-shown", "unknown")


@dataclass
class Message:
    """Одно сообщение в нормализованном виде."""

    date: str                      # ISO 8601, `YYYY-MM-DDTHH:MM:SS`
    author: str                    # кто написал на самом деле
    text: str = ""
    via: str | None = None         # кто переслал, если это не автор
    media: str | None = None       # путь к файлу относительно корня источника
    media_kind: str | None = None  # voice | photo | document
    msg_id: str | None = None
    reply_to: str | None = None

    @property
    def day(self) -> str:
        return self.date[:10]

    def key(self, source_id: str = "") -> str:
        """Устойчивый идентификатор сообщения — основа инкрементального импорта.

        Если у источника есть свой номер сообщения, берём его: он не меняется
        между выгрузками, и повторный импорт того же чата ничего не задвоит.
        Иначе считаем отпечаток от даты, автора и текста — для копипасты, где
        номеров нет, этого достаточно, чтобы узнать уже виденное.
        """
        if self.msg_id:
            return f"{source_id or 'src'}:{self.msg_id}"
        digest = hashlib.sha256(
            f"{self.date}|{self.author}|{self.text}|{self.media or ''}".encode()
        ).hexdigest()[:16]
        return f"{source_id or 'txt'}:{digest}"


    def content_key(self) -> str | None:
        """Ключ по содержимому — для узнавания одного сообщения из разных источников.

        Номера сообщений у форматов свои: выгрузка даёт `tg<чат>:<id>`, копипаста
        номеров не имеет вовсе. Поэтому текст, скопированный руками, а потом
        пришедший в выгрузке, основным ключом не ловится и заходит дважды.

        Считаем от минуты и текста, но **не от автора**: копипаста подписывает
        пересланное сообщение пересылающим, и по автору те же слова из двух
        источников не совпадут.

        Короткие реплики («ок», «да») пропускаем: они законно повторяются, и
        схлопывать их значило бы терять материал.
        """
        normalized = " ".join(self.text.split())
        if len(normalized) < 12:
            return None
        digest = hashlib.sha256(f"{self.date[:16]}|{normalized}".encode()).hexdigest()[:16]
        return f"txt:{digest}"


@dataclass
class ParseResult:
    """Что парсер вернул из источника."""

    messages: list[Message] = field(default_factory=list)
    attribution: str = "unknown"
    title: str = ""
    participants: list[str] = field(default_factory=list)
    # Машинный первоисточник, который нужно сохранить в RAW целиком: по нему
    # можно переразобрать всё заново, если в парсере найдётся ошибка.
    anchor: Path | None = None
    source_id: str = ""
    notes: list[str] = field(default_factory=list)


class Parser:
    """Базовый парсер. Наследники обязаны переопределить всё перечисленное."""

    name = "base"
    label = "источник"
    #: достоверность, которую формат может обеспечить в лучшем случае
    max_fidelity = "verbatim"
    #: надёжность авторства, которую формат может обеспечить в лучшем случае
    attribution = "unknown"

    @classmethod
    def detect(cls, source: Path) -> bool:
        """Похоже ли, что `source` — это наш формат."""
        raise NotImplementedError

    def parse(self, source: Path) -> ParseResult:
        raise NotImplementedError


def flatten_text(value) -> str:
    """Текст из поля, которое бывает строкой, а бывает списком сущностей.

    Telegram отдаёт `text` строкой для простых сообщений и списком объектов,
    когда внутри есть ссылка, упоминание или код. Наивная обработка кладёт в
    архив `[{'type': 'link', ...}]` вместо самого текста.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for chunk in value:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                parts.append(str(chunk.get("text", "")))
        return "".join(parts)
    return str(value)
