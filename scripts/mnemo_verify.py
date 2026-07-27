#!/usr/bin/env python3
"""Линтер стандарта: правила V01–V14 из SPEC/STANDARD.md.

Детерминированный, без участия модели. Линтер, работающий «на усмотрение», —
не линтер: он не может подтвердить, что архив цел, а именно это от него нужно.

Выход: 0 — чисто или только предупреждения; 1 — есть ошибки.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    ALLOWED_TOP, ATTRIBUTIONS, CONTOURS, FIDELITIES, INDEX_NAME, MANIFEST_NAME, REQUIRED_FILES,
    SOURCES, STATUSES, MnemoError, all_tracked_paths, find_export, iter_raw_files,
    load_manifest, parse_day, rel, sha256_file, unknown_names,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_manifest import is_git_ignored, git_root  # noqa: E402

# Ссылки вида [текст](путь) — только относительные и локальные.
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class Report:
    def __init__(self) -> None:
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def error(self, code: str, message: str, where: str = "") -> None:
        self.errors.append({"code": code, "message": message, "where": where})

    def warn(self, code: str, message: str, where: str = "") -> None:
        self.warnings.append({"code": code, "message": message, "where": where})

    @property
    def ok(self) -> bool:
        return not self.errors


def check(export: Path) -> Report:
    report = Report()
    manifest = load_manifest(export)
    items = manifest["items"]
    redactions = {r["id"] for r in manifest["redactions"]}

    # V01 / V02 — материал на месте и не изменился
    for item in items:
        path_str = item.get("raw_path")
        if item["status"] == "present":
            if not path_str:
                report.error("V01", "status=present без raw_path", item["id"])
                continue
            path = export / path_str
            if not path.is_file():
                report.error("V01", f"нет файла {path_str}", item["id"])
                continue
            actual = sha256_file(path)
            if item.get("sha256") != actual:
                report.error(
                    "V02",
                    f"хеш не совпал — RAW изменён или заменён без rehash ({path_str})",
                    item["id"],
                )

    # V03 — каждый файл в raw/ учтён (сам или как производное)
    tracked = all_tracked_paths(manifest)
    for path in iter_raw_files(export):
        relative = rel(export, path)
        if relative not in tracked:
            report.error("V03", f"файл не учтён в манифесте: {relative}")

    # V04 — ссылки в человекочитаемом слое ведут куда-то
    for doc in [export / INDEX_NAME, *sorted((export / "summaries").glob("*.md"))]:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        for target in LINK_RE.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # Пробелы в путях markdown принято кодировать как %20 — на диске
            # такого файла нет, сравнивать надо раскодированное.
            target = unquote(target)
            resolved = (doc.parent / target).resolve()
            if not resolved.exists():
                report.error("V04", f"битая ссылка → {target}", rel(export, doc))

    # V05 — недословное объяснено
    for item in items:
        if item["fidelity"] != "verbatim" and not (item.get("fidelity_note") or "").strip():
            report.error(
                "V05", f"fidelity={item['fidelity']} без fidelity_note", item["id"]
            )

    # V06 — пропуски выражены согласованно
    for item in items:
        if item["status"] == "missing":
            if not item.get("raw_path"):
                report.error("V06", "status=missing без ожидаемого raw_path", item["id"])
            elif (export / item["raw_path"]).is_file():
                report.warn(
                    "V06",
                    f"файл {item['raw_path']} появился — выполни /mnemo:sync",
                    item["id"],
                )
        elif item["status"] == "unrecoverable":
            if item.get("raw_path") is not None:
                report.error("V06", "status=unrecoverable требует raw_path=null", item["id"])
            if item["fidelity"] != "placeholder":
                report.error(
                    "V06", "status=unrecoverable требует fidelity=placeholder", item["id"]
                )

    # V07 — обязательный состав
    for required in REQUIRED_FILES:
        if not (export / required).exists():
            report.error("V07", f"нет обязательного файла {required}")

    # V08 — производное не отстало от источника
    index_path, manifest_path = export / INDEX_NAME, export / MANIFEST_NAME
    if index_path.is_file() and manifest_path.is_file():
        if index_path.stat().st_mtime < manifest_path.stat().st_mtime:
            report.warn("V08", "INDEX.md старше MANIFEST.json — выполни /mnemo:sync")

    # V09 — ссылки на изъятия существуют
    for item in items:
        for rid in item.get("redactions") or []:
            if rid not in redactions:
                report.error("V09", f"ссылка на несуществующее изъятие {rid}", item["id"])

    # V10 — идентификаторы и даты
    seen: set[str] = set()
    for item in items:
        if item["id"] in seen:
            report.error("V10", "дублирующийся id", item["id"])
        seen.add(item["id"])
        try:
            parse_day(item["date"])
        except MnemoError as exc:
            report.error("V10", str(exc), item["id"])
        for field, allowed in (
            ("source", SOURCES), ("fidelity", FIDELITIES),
            ("status", STATUSES), ("contour", CONTOURS),
        ):
            if item.get(field) not in allowed:
                report.error("V10", f"{field}={item.get(field)!r} вне допустимых", item["id"])

    # V13 — ненадёжное авторство объявлено и объяснено
    for item in items:
        value = item.get("attribution", "reliable")
        if value not in ATTRIBUTIONS:
            report.error("V13", f"attribution={value!r} вне допустимых", item["id"])
        elif value != "reliable" and not (item.get("fidelity_note") or "").strip():
            report.error(
                "V13",
                f"attribution={value} без объяснения — непонятно, чьё авторство под вопросом",
                item["id"],
            )

    # V14 — участники опознаны. Предупреждение, а не ошибка: реестр наполняется
    # по мере знакомства с проектом, и пустой реестр не делает архив негодным.
    everyone = [name for item in items for name in item.get("participants", [])]
    strangers = unknown_names(manifest, everyone)
    if strangers:
        report.warn(
            "V14",
            "нет в реестре людей: " + ", ".join(strangers[:8])
            + (f" и ещё {len(strangers) - 8}" if len(strangers) > 8 else "")
            + " — заведи через people --add, иначе поиск по человеку неполон",
        )

    # V11 — ничего лишнего в корне
    for entry in sorted(export.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.name not in ALLOWED_TOP:
            report.error("V11", f"файл вне разрешённых зон: {entry.name}")

    # V12 — данные не уедут в чужую репу
    if git_root(export) is None:
        report.warn("V12", "хост-проект не под git — исключать нечего")
    elif not is_git_ignored(export):
        report.error(
            "V12",
            "каталог экспорта НЕ исключён из git — рабочие данные могут уехать в репозиторий",
        )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить экспорт на соответствие стандарту")
    parser.add_argument("--export", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="предупреждения тоже считать ошибками")
    args = parser.parse_args()

    try:
        export = find_export(Path(args.export))
        report = check(export)
    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(
            {"ok": report.ok, "errors": report.errors, "warnings": report.warnings},
            ensure_ascii=False, indent=2,
        ))
    else:
        for entry in report.errors:
            where = f" [{entry['where']}]" if entry["where"] else ""
            print(f"ОШИБКА {entry['code']}{where}: {entry['message']}")
        for entry in report.warnings:
            where = f" [{entry['where']}]" if entry["where"] else ""
            print(f"предупр. {entry['code']}{where}: {entry['message']}")
        if report.ok and not report.warnings:
            print(f"✅ {export.name}: стандарт соблюдён")
        elif report.ok:
            print(f"✅ {export.name}: ошибок нет, предупреждений {len(report.warnings)}")
        else:
            print(f"❌ {export.name}: ошибок {len(report.errors)}, "
                  f"предупреждений {len(report.warnings)}")

    if not report.ok:
        return 1
    return 1 if (args.strict and report.warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
