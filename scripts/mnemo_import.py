#!/usr/bin/env python3
"""Импорт источника целиком: выгрузка Telegram, копипаста и т.п.

Формат опознаётся автоматически через реестр парсеров. Ядро о конкретных
форматах не знает — знает только контракт `parsers.base.Parser`.

Два режима, и по умолчанию действует безопасный:
    --plan (по умолчанию)  показать, что будет сделано, ничего не менять
    --apply                выполнить

Использование:
    mnemo_import.py --export <dir> --source <path>
    mnemo_import.py --export <dir> --source <path> --apply
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parsers  # noqa: E402
from mnemo_core import (  # noqa: E402
    RAW_ZONES, MnemoError, claim_path, find_export, imported_keys, load_manifest,
    message_filename, new_item, next_id, save_manifest, sha256_file, slugify,
    today, unknown_names,
)
from parsers.base import Message, ParseResult  # noqa: E402

# Начало строки, которое markdown прочитает как разметку, а не как текст.
BLOCK_PREFIX = re.compile(r"^(\s*)([#>*+\-=|]|\d+[.)]|```|~~~)")


def escape_md(text: str) -> str:
    r"""Обезвредить текст сообщения перед вставкой в markdown.

    Люди пишут в мессенджер `<b>`, `#1`, `- пункт`, тройные кавычки и таблицы.
    Без экранирования такой текст превращается в заголовок, список или сырой
    HTML — то есть архив показывает не то, что человек написал.

    Экранирование меняет байты, но не вид: `&lt;` отображается как `<`,
    `\#` — как `#`. Дословный первоисточник при этом лежит рядом в RAW
    целиком, так что проверить оригинал всегда можно.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = []
    for line in text.split("\n"):
        match = BLOCK_PREFIX.match(line)
        if match:
            line = f"{match.group(1)}\\{line.lstrip()}"
        out.append(line)
    return "\n".join(out)


# Куда кладётся медиа в зависимости от того, чем оно является.
ZONE_BY_KIND = {"voice": "voice", "photo": "screenshot", "document": "attachment"}
SOURCE_BY_KIND = {"voice": "voice", "photo": "screenshot", "document": "other"}


# --------------------------------------------------------------------------
# Рендер сообщений
# --------------------------------------------------------------------------

def render_day(title: str, day: str, messages: list[Message], result: ParseResult,
               parser_label: str, media_paths: dict[str, str]) -> str:
    """Дневной транскрипт в markdown.

    Пересылки подписываются настоящим автором, а пересылающий указывается
    отдельно. Это главное, ради чего импорт вообще делается машиной: глазами
    и копипастой авторство пересланного теряется.
    """
    lines = [
        f"# {title} — {day}",
        "",
        f"> Источник: {parser_label}. Надёжность авторства — `{result.attribution}`.",
        "",
    ]
    if result.attribution != "reliable":
        lines += [
            "> ⚠️ Имя над сообщением может принадлежать не автору, а тому, кто "
            "переслал. Цитировать по автору нельзя.",
            "",
        ]
    lines.append("---")
    lines.append("")

    for message in messages:
        time = message.date[11:16] or "??:??"
        who = message.author
        if message.via:
            who += f" _(переслал: {message.via})_"
        lines.append(f"### {time} · {who}")
        if message.text:
            lines.append("")
            lines.append(escape_md(message.text))
        if message.media:
            kind = {"voice": "🎤 голосовое", "photo": "🖼 изображение"}.get(
                message.media_kind or "", "📎 файл"
            )
            lines.append("")
            stored = media_paths.get(message.media)
            if stored:
                # Ссылка от raw/messages/ к зоне медиа — чтобы из транскрипта
                # можно было открыть сам файл, а линтер проверил, что он на месте.
                lines.append(f"{kind}: [`{Path(stored).name}`](../../{stored})")
            else:
                lines.append(f"{kind}: `{Path(message.media).name}` — файл не найден")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------

def split_new(result: ParseResult, known: set[str]) -> tuple[list[Message], int]:
    """Отделить непринятое от уже принятого.

    Из-за этого повторная выгрузка того же чата безопасна: она приносит и
    старое, и новое, а в архив попадает только новое. Иначе пришлось бы
    вручную нарезать выгрузку по датам или довносить свежее копипастой —
    и то и другое теряет вложения и авторство пересылок.
    """
    fresh, seen = [], set()
    duplicates = 0
    for message in result.messages:
        keys = {message.key(result.source_id)}
        content = message.content_key()
        if content:
            keys.add(content)
        if keys & known or keys & seen:
            duplicates += 1
            continue
        seen |= keys
        fresh.append(message)
    return fresh, duplicates


def build_plan(result: ParseResult, source: Path, messages: list[Message],
               duplicates: int = 0) -> dict:
    by_day: dict[str, list[Message]] = defaultdict(list)
    for message in messages:
        if message.date:
            by_day[message.day].append(message)

    media = [m for m in messages if m.media]
    missing = [m for m in media if not (source / m.media).is_file()
               and not (source.parent / m.media).is_file()]

    return {
        "days": dict(sorted(by_day.items())),
        "messages": messages,
        "duplicates": duplicates,
        "authors": Counter(m.author for m in messages),
        "forwarded": sum(1 for m in messages if m.via),
        "media": Counter(m.media_kind for m in media),
        "media_missing": missing,
    }


def print_plan(parser_obj, result: ParseResult, plan: dict, source: Path,
               export: Path, strangers: list[str] | None = None) -> None:
    print(f"источник:  {source}")
    print(f"формат:    {parser_obj.label}  ({parser_obj.name})")
    print(f"экспорт:   {export}")
    print()
    print(f"чат:              {result.title}")
    print(f"сообщений новых:  {len(plan['messages'])} из {len(result.messages)} в источнике")
    if plan["duplicates"]:
        print(f"уже в архиве:     {plan['duplicates']} — повторно не заводятся")
    print(f"дней:             {len(plan['days'])}  "
          f"({min(plan['days'], default='—')} … {max(plan['days'], default='—')})")
    print(f"достоверность:    {parser_obj.max_fidelity}")
    print(f"авторство:        {result.attribution}")
    print()
    print("авторы (после раскрытия пересылок):")
    for name, count in plan["authors"].most_common():
        print(f"  {count:>4}  {name}")
    if plan["forwarded"]:
        print(f"\n  из них пересланных: {plan['forwarded']} — "
              "подписаны настоящим автором, пересылающий указан отдельно")
    if plan["media"]:
        print("\nмедиа:")
        for kind, count in plan["media"].items():
            print(f"  {count:>4}  {kind} → raw/{RAW_ZONES[ZONE_BY_KIND[kind]].split('/')[-1]}/")
    if plan["media_missing"]:
        print(f"\n  ⚠️ файлов не найдено на диске: {len(plan['media_missing'])} — "
              "будут заведены как хвосты (status=missing)")
    if strangers:
        print("\n  ⚠️ нет в реестре людей: " + ", ".join(strangers))
        print("     заведи их: mnemo_manifest.py people --add --display «...» --role ...")
        print("     иначе поиск по человеку неполон, и непонятно, кто из них кто")
    if result.anchor:
        print(f"\nмашинный первоисточник → raw/attachments/{result.anchor.name}")
        print("  (сохраняется целиком: по нему можно переразобрать всё заново)")
    for note in result.notes:
        print(f"\n  ⚠️ {note}")


# --------------------------------------------------------------------------
# Выполнение
# --------------------------------------------------------------------------

def resolve_media(source: Path, relative: str) -> Path | None:
    for base in (source, source.parent):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def apply(export: Path, source: Path, parser_obj, result: ParseResult, plan: dict) -> dict:
    manifest = load_manifest(export)
    stats = Counter()
    chat_slug = slugify(result.title)
    fresh = plan["messages"]
    stamp = today()

    fidelity = parser_obj.max_fidelity
    note_parts = [f"импортировано из «{parser_obj.label}»"]
    note_parts += result.notes
    fidelity_note = "; ".join(note_parts) if fidelity != "verbatim" or \
        result.attribution != "reliable" else None

    # --- машинный первоисточник ---
    if result.anchor and result.anchor.is_file():
        target = export / RAW_ZONES["attachment"] / result.anchor.name
        if target.exists():
            # Повторный импорт приносит новый снимок источника: старый не
            # трогаем (RAW неизменен), новый различаем датой импорта.
            target = claim_path(target.with_name(f"{target.stem}_{stamp}{target.suffix}"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.anchor, target)
        manifest["items"].append(new_item(
            id=next_id(manifest), source="telegram", fidelity="verbatim",
            attribution="reliable",
            origin=f"машинная выгрузка «{result.title}», первоисточник импорта",
            date=min(plan["days"], default=today()),
            participants=sorted({m.author for m in fresh}),
            raw_path=target.relative_to(export).as_posix(),
            sha256=sha256_file(target), status="present",
        ))
        stats["anchor"] += 1

    # Медиа раскладывается до рендера: транскрипт ссылается на конечные пути,
    # а не на имена из чужой раскладки.
    media_paths: dict[str, str] = {}
    claimed: set[str] = set()
    for message in fresh:
        if not message.media or message.media in media_paths:
            continue
        zone = RAW_ZONES[ZONE_BY_KIND[message.media_kind or "document"]]
        prefix = f"{message.day}_{slugify(message.author)}"
        wanted = export / zone / f"{prefix}_{Path(message.media).name}"
        # Разные файлы приходят под одинаковыми именами: у Telegram это обычное
        # дело для фото. Резервируем путь заранее, включая ещё не записанные,
        # иначе второй файл затрёт первый, а обе записи будут указывать на один.
        chosen = claim_path(wanted)
        while chosen.relative_to(export).as_posix() in claimed:
            chosen = claim_path(chosen.with_name(f"{chosen.stem}-x{chosen.suffix}"))
        claimed.add(chosen.relative_to(export).as_posix())
        media_paths[message.media] = chosen.relative_to(export).as_posix()

    # --- дневные транскрипты ---
    for day, messages in plan["days"].items():
        body = render_day(result.title, day, messages, result, parser_obj.label, media_paths)
        name = message_filename(day, chat_slug, "chat")
        target = export / RAW_ZONES["message"] / name
        if target.exists():
            # За этот день транскрипт уже есть. Дописывать в него нельзя —
            # RAW неизменен; новый материал становится отдельным артефактом,
            # помеченным датой импорта. Если и такой есть (несколько импортов
            # за сутки) — claim_path подберёт свободное имя, а не затрёт.
            target = claim_path(export / RAW_ZONES["message"] / message_filename(
                day, chat_slug, f"chat-{stamp}"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        manifest["items"].append(new_item(
            id=next_id(manifest), source="telegram", fidelity=fidelity,
            fidelity_note=fidelity_note, attribution=result.attribution,
            origin=f"«{result.title}», {parser_obj.label}",
            date=day,
            participants=sorted({m.author for m in messages}),
            raw_path=target.relative_to(export).as_posix(),
            sha256=sha256_file(target), status="present",
        ))
        stats["days"] += 1

    # --- медиа ---
    filed: set[str] = set()
    for message in fresh:
        if not message.media:
            continue
        # На один и тот же файл может ссылаться несколько сообщений — в выгрузке
        # это обычное дело. Файл хранится один раз и учитывается одной записью:
        # две записи на один путь означали бы, что материалов больше, чем есть.
        if media_paths.get(message.media) in filed:
            continue
        kind = message.media_kind or "document"
        found = resolve_media(source, message.media)
        rel_path = media_paths[message.media]

        if found is None:
            manifest["items"].append(new_item(
                id=next_id(manifest), source=SOURCE_BY_KIND[kind],
                fidelity="placeholder", attribution=result.attribution,
                fidelity_note=(
                    f"файл {Path(message.media).name} упомянут в выгрузке, "
                    "но на диске отсутствует — возможно, не выгрузился"
                ),
                origin=f"«{result.title}», сообщение {message.msg_id or '?'}",
                date=message.day, participants=[message.author],
                raw_path=rel_path, sha256=None, status="missing",
            ))
            filed.add(rel_path)
            stats["missing"] += 1
            continue

        target = export / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, target)
        manifest["items"].append(new_item(
            id=next_id(manifest), source=SOURCE_BY_KIND[kind],
            fidelity="verbatim", attribution=result.attribution,
            fidelity_note=(
                None if result.attribution == "reliable"
                else f"оригинал файла дословен; авторство: {result.notes[0] if result.notes else 'под вопросом'}"
            ),
            origin=f"«{result.title}», сообщение {message.msg_id or '?'}",
            date=message.day, participants=[message.author],
            raw_path=rel_path, sha256=sha256_file(target), status="present",
        ))
        filed.add(rel_path)
        stats[kind] += 1

    manifest.setdefault("imports", []).append({
        "parser": parser_obj.name,
        "source": str(source),
        "imported": stamp,
        "messages": len(fresh),
        "keys": sorted({
            key
            for m in fresh
            for key in (m.key(result.source_id), m.content_key())
            if key
        }),
    })
    save_manifest(export, manifest)
    return stats


def trash(path: Path) -> str:
    """Убрать источник в корзину — обратимо через `trash-restore`.

    Никогда не удаляем безвозвратно: пока архив не проверен человеком, источник
    остаётся единственной полной копией.
    """
    if shutil.which("trash-put") is None:
        return "trash-cli не найден — источник оставлен на месте, убери вручную"
    try:
        subprocess.run(["trash-put", str(path)], check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"не удалось убрать в корзину: {exc}"
    return f"источник убран в корзину (вернуть: trash-restore): {path}"


# --------------------------------------------------------------------------

def main() -> int:
    argp = argparse.ArgumentParser(description="Импортировать источник в экспорт mnemo")
    argp.add_argument("--export", default=".")
    argp.add_argument("--source", required=True, help="каталог выгрузки или файл")
    argp.add_argument("--apply", action="store_true", help="выполнить (иначе только план)")
    argp.add_argument("--trash-source", action="store_true",
                      help="после успешного импорта убрать источник в корзину")
    argp.add_argument("--list-parsers", action="store_true")
    args = argp.parse_args()

    if args.list_parsers:
        for name, label in parsers.available():
            print(f"{name:12} {label}")
        return 0

    try:
        export = find_export(Path(args.export))
        source = Path(args.source).expanduser()
        if not source.exists():
            raise MnemoError(f"источник не найден: {source}")

        parser_obj = parsers.detect(source)
        if parser_obj is None:
            known = ", ".join(n for n, _ in parsers.available())
            raise MnemoError(
                f"формат не опознан: {source}. Известные: {known}. "
                "Добавь материал вручную через add-text / add-files."
            )

        result = parser_obj.parse(source)
        if not result.messages:
            raise MnemoError("в источнике нет сообщений — нечего импортировать")

        manifest = load_manifest(export)
        known = imported_keys(manifest)
        fresh, duplicates = split_new(result, known)
        plan = build_plan(result, source, fresh, duplicates)
        strangers = unknown_names(manifest, [m.author for m in fresh]
                                  + [m.via for m in fresh if m.via])

        print_plan(parser_obj, result, plan, source, export, strangers)

        if not fresh:
            print("\n— всё содержимое источника уже в архиве. Делать нечего.")
            return 0

        if not args.apply:
            print("\n— это план. Ничего не изменено. Повтори с --apply, чтобы выполнить.")
            return 0

        print("\n--- импорт ---")
        stats = apply(export, source, parser_obj, result, plan)
        from mnemo_render import sync
        sync(export)
        for key, count in sorted(stats.items()):
            print(f"  {key}: {count}")

        from mnemo_verify import check
        report = check(export)
        if report.ok:
            print("  линтер: ✅ стандарт соблюдён")
        else:
            print(f"  линтер: ❌ ошибок {len(report.errors)} — источник НЕ трогаю")
            for entry in report.errors[:10]:
                print(f"    {entry['code']}: {entry['message']}")
            return 1

        if args.trash_source:
            print(f"  {trash(source)}")
        else:
            print(f"\nисточник оставлен: {source}")
            print("  (убрать в корзину: повтори с --trash-source)")
        return 0

    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
