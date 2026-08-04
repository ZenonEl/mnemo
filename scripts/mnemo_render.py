#!/usr/bin/env python3
"""Генерация производных: INDEX.md и summaries/redactions.md.

Оба файла целиком выводятся из MANIFEST.json и в любой момент могут быть удалены
и пересобраны. Руками их не правят — правка потеряется при следующем `sync`.

Причина, по которой INDEX перестал быть рукописным: в ручной практике он молча
расходился с содержимым — упоминал не все файлы, что рядом, и не отражал
появившиеся пропуски. Один источник плюс генератор делают такое расхождение
невозможным.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    INDEX_NAME, MnemoError, find_export, load_manifest, required_spec,
    resolve_person, save_manifest, sha256_file,
)

GENERATED_NOTE = (
    "<!-- СГЕНЕРИРОВАНО mnemo из MANIFEST.json. Правки здесь будут потеряны "
    "при следующем /mnemo:sync — правьте манифест. -->"
)

FIDELITY_LABEL = {
    "verbatim": "дословно",
    "reconstructed": "восстановлено",
    "digest": "конспект",
    "placeholder": "заглушка",
}

STATUS_LABEL = {
    "present": "есть",
    "missing": "не добыт",
    "unrecoverable": "утрачен",
}

ROLE_LABEL = {
    "self": "это я",
    "colleague": "коллега",
    "management": "начальство",
    "client": "клиент",
    "other": "—",
}

ZONE_TITLE = {
    "raw/messages": "Сообщения",
    "raw/attachments": "Вложения",
    "raw/screenshots": "Скриншоты",
    "raw/voice": "Голосовые",
}


def _zone_of(path: str | None) -> str:
    if not path:
        return "—"
    for zone in ZONE_TITLE:
        if path.startswith(zone + "/"):
            return zone
    return "raw"


def _escape(text: str) -> str:
    """Экранировать вертикальную черту, иначе она ломает разметку таблицы."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _url(path: str) -> str:
    """Путь для markdown-ссылки: пробелы и прочее кодируем.

    Имена файлов приходят из реального мира — с пробелами, запятыми и
    кириллицей. Некодированный пробел рвёт ссылку в части рендереров.
    """
    return quote(str(path), safe="/")


def render_index(manifest: dict) -> str:
    meta = manifest["export"]
    items = sorted(manifest["items"], key=lambda i: (i["date"], i["id"]))
    redactions = manifest["redactions"]

    present = [i for i in items if i["status"] == "present"]
    gaps = [i for i in items if i["status"] != "present"]

    lines: list[str] = [
        f"# INDEX — {meta['title']}",
        "",
        GENERATED_NOTE,
        "",
        f"**Slug:** `{meta['slug']}` · **Создан:** {meta['created']} · "
        f"**Контур:** {meta['contour']}"
        + (f" · **Проект:** `{meta['project']}`" if meta.get("project") else ""),
        "",
        f"Материалов: **{len(present)}** · пропусков: **{len(gaps)}** · изъятий: **{len(redactions)}**",
        "",
        "---",
        "",
        "## С чего начать",
        "",
        "1. [`summaries/attachments-summary.md`](summaries/attachments-summary.md) — "
        "что в документах: решения, acceptance, на что свериться.",
        "2. [`summaries/conventions.md`](summaries/conventions.md) — "
        "по каким правилам здесь работают и чего делать нельзя.",
        "3. [`summaries/findings-log.md`](summaries/findings-log.md) — "
        "проверенные факты и куда они уже донесены.",
        "4. [`summaries/redactions.md`](summaries/redactions.md) — что изъято и почему.",
        "",
    ]

    if meta.get("participants"):
        lines += ["**Участники:** " + ", ".join(meta["participants"]), ""]

    # --- кто есть кто ----------------------------------------------------
    people = manifest.get("people", [])
    if people:
        lines += [
            "## Кто есть кто",
            "",
            "Один человек в разных источниках выглядит по-разному. Здесь — "
            "соответствие: по какому имени искать и кто это на самом деле.",
            "",
            "| Кто | Роль | Как встречается | Аккаунты |",
            "|---|---|---|---|",
        ]
        for person in people:
            aliases = ", ".join(f"`{a}`" for a in person.get("aliases", [])) or "—"
            handles = ", ".join(f"{k}: `{v}`" for k, v in person.get("handles", {}).items()) or "—"
            lines.append(
                f"| **{_escape(person['display'])}** | {ROLE_LABEL.get(person['role'], person['role'])} "
                f"| {_escape(aliases)} | {_escape(handles)} |"
            )
        lines.append("")
        me = next((p for p in people if p["role"] == "self"), None)
        if me:
            lines += [
                f"> Оператор архива — **{me['display']}**. Реплики за этой подписью "
                "принадлежат тому, кто ведёт этот архив.",
                "",
            ]

    # --- достоверность ---------------------------------------------------
    by_fidelity: dict[str, int] = defaultdict(int)
    for item in items:
        by_fidelity[item["fidelity"]] += 1
    lines += [
        "## Достоверность",
        "",
        "Каждая цитата отсюда обязана нести свой уровень. "
        "Конспект (`digest`) приводить как прямую речь участника — запрещено. "
        "Модель целиком — `SPEC/PROVENANCE.md` в репозитории mnemo "
        "(ссылкой не даю: экспорт живёт отдельно от него).",
        "",
        "| Уровень | Что значит | Материалов |",
        "|---|---|---|",
    ]
    for level in ("verbatim", "reconstructed", "digest", "placeholder"):
        if by_fidelity.get(level):
            lines.append(f"| `{level}` | {FIDELITY_LABEL[level]} | {by_fidelity[level]} |")
    lines.append("")

    # --- хронология ------------------------------------------------------
    lines += [
        "## Хронология",
        "",
        "| Дата | Материал | Кто | Достоверность | id |",
        "|---|---|---|---|---|",
    ]
    for item in items:
        where = item["raw_path"] or "—"
        link = f"[`{where}`]({_url(where)})" if item["status"] == "present" else f"`{where}`"
        who = ", ".join(item["participants"]) or "—"
        mark = "" if item["status"] == "present" else f" ⚠️ {STATUS_LABEL[item['status']]}"
        lines.append(
            f"| {item['date']} | {link}{mark} | {_escape(who)} | "
            f"`{item['fidelity']}` | `{item['id']}` |"
        )
    if not items:
        lines.append("| — | _пусто_ | — | — | — |")
    lines.append("")

    # --- по зонам --------------------------------------------------------
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in present:
        grouped[_zone_of(item["raw_path"])].append(item)

    if grouped:
        lines += ["## Материалы", ""]
        for zone, title in ZONE_TITLE.items():
            bucket = grouped.get(zone)
            if not bucket:
                continue
            lines += [f"### {title}", ""]
            for item in bucket:
                path = item["raw_path"]
                suffix = f" · `{item['origin']}`" if item["origin"] else ""
                lines.append(f"- [`{Path(path).name}`]({_url(path)}) — {item['date']}, "
                             f"`{item['fidelity']}`{suffix}")
                for derived in item["derived_paths"]:
                    lines.append(f"  - производное: [`{derived}`]({_url(derived)})")
            lines.append("")

    # --- хвосты ----------------------------------------------------------
    lines += ["## Хвосты", ""]
    if gaps:
        lines += [
            "Чего не хватает. `не добыт` — задача, оригинал существует. "
            "`утрачен` — факт, восстановлению не подлежит.",
            "",
            "| Статус | Что | Почему | Ожидаемый путь | id |",
            "|---|---|---|---|---|",
        ]
        for item in gaps:
            lines.append(
                f"| {STATUS_LABEL[item['status']]} | {_escape(item['origin'] or '—')} | "
                f"{_escape(item['fidelity_note'] or '—')} | "
                f"`{item['raw_path'] or '—'}` | `{item['id']}` |"
            )
    else:
        lines.append("Пропусков нет.")
    lines.append("")

    # --- изъятия ---------------------------------------------------------
    if redactions:
        lines += [
            "## Изъятия",
            "",
            f"Изъято фрагментов: **{len(redactions)}**. "
            "Подробности — [`summaries/redactions.md`](summaries/redactions.md).",
            "",
        ]

    lines += [
        "---",
        "",
        f"_Стандарт: mnemo {manifest['mnemo_spec']}. "
        "Файл производный — пересобирается командой `/mnemo:sync`._",
        "",
    ]
    return "\n".join(lines)


def render_redactions(manifest: dict) -> str:
    redactions = manifest["redactions"]
    by_redaction: dict[str, list[str]] = defaultdict(list)
    for item in manifest["items"]:
        for rid in item["redactions"]:
            by_redaction[rid].append(item["id"])

    lines = [
        "# Изъятия",
        "",
        GENERATED_NOTE,
        "",
        "Что удалено из экспорта и на каком основании. Описания намеренно не "
        "раскрывают изъятое — иначе изъятие бессмысленно.",
        "",
    ]
    if not redactions:
        lines += ["Ничего не изымалось.", ""]
        return "\n".join(lines)

    lines += [
        "| id | Основание | Что | Где было | Обратимо | Затронуто |",
        "|---|---|---|---|---|---|",
    ]
    for record in redactions:
        touched = ", ".join(f"`{i}`" for i in by_redaction.get(record["id"], [])) or "—"
        reversible = "да" if record["reversible"] else "нет"
        lines.append(
            f"| `{record['id']}` | `{record['reason']}` | {_escape(record['description'])} | "
            f"`{_escape(record['scope'])}` | {reversible} | {touched} |"
        )
    lines.append("")

    recoverable = [r for r in redactions if r["reversible"]]
    if recoverable:
        lines += [
            "## Обратимые",
            "",
            "Оригиналы лежат **вне экспорта** — изъятое и экспорт не путешествуют вместе.",
            "",
        ]
        for record in recoverable:
            lines.append(f"- `{record['id']}` → `{record['vault_ref']}`")
        lines.append("")
    return "\n".join(lines)


def sync(export: Path, rehash: bool = True) -> dict:
    manifest = load_manifest(export)

    updated = 0
    if rehash:
        # Хеши освежаются только для записей, которые ждали файла. Расхождение
        # у существующего материала — это ошибка V02, и она должна остаться ошибкой.
        for item in manifest["items"]:
            if item["status"] != "missing" or not item.get("raw_path"):
                continue
            path = export / item["raw_path"]
            if path.is_file():
                item["sha256"] = sha256_file(path)
                item["status"] = "present"
                updated += 1
        # Приведение заявленной версии к фактическому содержимому — тоже
        # производная величина, и место ей здесь: sync существует затем, чтобы
        # то, что выводится из манифеста, ему соответствовало.
        drifted = manifest.get("mnemo_spec") != required_spec(manifest) and \
            tuple(int(x) for x in str(manifest.get("mnemo_spec", "1.0")).split(".")) < \
            tuple(int(x) for x in required_spec(manifest).split("."))
        if updated or drifted:
            save_manifest(export, manifest)

    (export / INDEX_NAME).write_text(render_index(manifest), encoding="utf-8")
    (export / "summaries" / "redactions.md").write_text(
        render_redactions(manifest), encoding="utf-8"
    )
    return {"items": len(manifest["items"]), "resolved": updated}


def main() -> int:
    parser = argparse.ArgumentParser(description="Пересобрать INDEX.md и redactions.md")
    parser.add_argument("--export", default=".")
    parser.add_argument("--no-rehash", action="store_true",
                        help="не подхватывать доложенные файлы")
    args = parser.parse_args()

    try:
        export = find_export(Path(args.export))
        result = sync(export, rehash=not args.no_rehash)
    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"пересобрано: INDEX.md, summaries/redactions.md ({result['items']} записей)")
    if result["resolved"]:
        print(f"доложено файлов: {result['resolved']} — записи переведены в present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
