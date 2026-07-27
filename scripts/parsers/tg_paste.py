"""Копипаста из Telegram: `[ДД.ММ.ГГГГ ЧЧ:ММ] Автор: текст`.

Формат, который Telegram выдаёт при копировании выделенных сообщений.

**Важное ограничение, ради которого этот парсер выделен отдельно.**
В копипасте у пересланного сообщения указан тот, кто переслал, а не тот, кто
написал. На реальном чате это оказалось не редкостью: из 21 сообщения 16 были
бы приписаны не тому человеку — причём все требования заказчика достались бы
пересылавшему их руководителю.

Поэтому парсер объявляет `attribution = "forwarder-shown"` **всегда**, и снять
эту пометку может только человек, подтвердив авторство вручную. Текст при этом
дословный — испорчено не содержание, а подпись под ним.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .base import Message, ParseResult, Parser

# Только дата и время; где кончается имя автора — решается отдельно, см. ниже.
STAMP_RE = re.compile(
    r"^\[(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<y>\d{4})[ ,]+(?P<time>\d{2}:\d{2}(?::\d{2})?)\]\s*"
    r"(?P<rest>.*)$"
)

MAX_AUTHOR = 60


def _splits(rest: str) -> list[tuple[str, str]]:
    """Все допустимые разбиения «автор: текст» для остатка строки."""
    out: list[tuple[str, str]] = []
    for match in re.finditer(r":", rest):
        index = match.start()
        author = rest[:index].strip()
        after = rest[index + 1:]
        # Разделителем считаем двоеточие, за которым пробел или конец строки:
        # внутри имени вида «Соловейко :D» двоеточие идёт вплотную к букве.
        if after and not after.startswith(" "):
            continue
        if author and len(author) <= MAX_AUTHOR:
            out.append((author, after[1:] if after.startswith(" ") else after))
    return out


def _resolve_authors(lines: list[str]) -> dict[int, tuple[str, str]]:
    """Выбрать разбиение для каждой строки по частоте имени во всём тексте.

    Имя автора в переписке повторяется, а случайный кусок текста перед
    двоеточием — нет. Поэтому из всех возможных разбиений строки берём то,
    чей «автор» чаще всего встречается как кандидат по всему файлу.

    Это чинит имена с двоеточием внутри (`Соловейко :D`), на которых наивный
    разбор по первому двоеточию срезал начало сообщения.
    """
    candidates: dict[int, list[tuple[str, str]]] = {}
    tally: Counter[str] = Counter()

    for number, line in enumerate(lines):
        match = STAMP_RE.match(line)
        if not match:
            continue
        options = _splits(match.group("rest"))
        if not options:
            continue
        candidates[number] = options
        for author, _ in options:
            tally[author] += 1

    resolved: dict[int, tuple[str, str]] = {}
    for number, options in candidates.items():
        # Частота — главный признак; при равенстве берём более короткое имя,
        # потому что лишний кусок текста всегда удлиняет кандидата.
        resolved[number] = max(options, key=lambda o: (tally[o[0]], -len(o[0])))
    return resolved


class TelegramPasteParser(Parser):
    name = "tg-paste"
    label = "копипаста из Telegram"
    # Текст дословный, но пришёл через буфер обмена, а не из машинной выгрузки:
    # порядок, вложения и авторство пересылок по дороге теряются.
    max_fidelity = "reconstructed"
    attribution = "forwarder-shown"

    @classmethod
    def detect(cls, source: Path) -> bool:
        if not source.is_file() or source.suffix.lower() not in {".txt", ".md", ""}:
            return False
        try:
            head = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        return any(STAMP_RE.match(line) for line in head[:40])

    def parse(self, source: Path) -> ParseResult:
        lines = source.read_text(encoding="utf-8").splitlines()
        resolved = _resolve_authors(lines)

        result = ParseResult(
            attribution=self.attribution,
            title=source.stem,
            anchor=source,
        )

        current: Message | None = None
        buffer: list[str] = []

        def flush() -> None:
            if current is None:
                return
            current.text = "\n".join(buffer).strip()
            # Пустое сообщение в копипасте — след медиа, которое в буфер обмена
            # не попало. Пропустить молча значило бы потерять сам факт, что в
            # разговоре что-то было.
            if not current.text:
                current.text = "<вложение или голосовое: в копипасту не попало>"
                current.media_kind = "document"
            result.messages.append(current)

        for number, line in enumerate(lines):
            if number in resolved:
                flush()
                author, text = resolved[number]
                buffer = [text]
                stamp = STAMP_RE.match(line)
                time = stamp.group("time")
                if len(time) == 5:
                    time += ":00"
                current = Message(
                    date=f"{stamp.group('y')}-{stamp.group('m')}-{stamp.group('d')}T{time}",
                    author=author,
                )
            elif current is not None:
                buffer.append(line)
        flush()

        result.participants = sorted({m.author for m in result.messages})
        result.notes.append(
            "авторство ненадёжно: Telegram в копипасте показывает того, кто "
            "переслал сообщение, а не того, кто его написал. Пересылки "
            "неотличимы от собственных реплик"
        )
        missing = sum(1 for m in result.messages if m.media_kind)
        if missing:
            result.notes.append(
                f"сообщений без содержимого (было медиа): {missing} — "
                "оригиналы в копипасту не попадают, нужна выгрузка или скрины"
            )
        return result
