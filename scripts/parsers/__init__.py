"""Реестр парсеров.

Ядро спрашивает у реестра, кто умеет читать источник, и получает объект,
удовлетворяющий контракту `base.Parser`. О конкретных форматах ядро не знает.

Добавить источник = положить рядом модуль с классом-наследником `Parser`
и вписать его в `PARSERS`. Ничего существующего при этом не меняется.
"""

from __future__ import annotations

from pathlib import Path

from .base import ATTRIBUTION, Message, ParseResult, Parser, flatten_text
from .tg_json import TelegramJsonParser
from .tg_paste import TelegramPasteParser

# Порядок значим: более специфичные форматы проверяются первыми. Выгрузка
# опознаётся по структуре каталога, копипаста — по шаблону строк, и выгрузка
# не должна случайно достаться текстовому парсеру.
PARSERS: tuple[type[Parser], ...] = (
    TelegramJsonParser,
    TelegramPasteParser,
)

__all__ = [
    "ATTRIBUTION", "Message", "ParseResult", "Parser", "PARSERS",
    "flatten_text", "detect", "available",
]


def detect(source: Path) -> Parser | None:
    """Первый парсер, опознавший источник. `None`, если формат неизвестен."""
    for parser_cls in PARSERS:
        try:
            if parser_cls.detect(source):
                return parser_cls()
        except Exception:
            # Сбой распознавания одного формата не должен ронять весь перебор:
            # источник может быть битым именно для него.
            continue
    return None


def available() -> list[tuple[str, str]]:
    return [(p.name, p.label) for p in PARSERS]
