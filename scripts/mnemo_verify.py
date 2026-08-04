#!/usr/bin/env python3
"""Линтер стандарта: правила V01–V20 из SPEC/STANDARD.md §13.

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
    ALLOWED_TOP, ATTRIBUTIONS, CONTOURS, FIDELITIES, INDEX_NAME, MANIFEST_NAME,
    PERSON_ROLES, REDACTION_REASONS, REQUIRED_FILES, REQUIREMENT_STATES,
    SOURCES, STATUSES, MnemoError, all_tracked_paths, find_export, iter_raw_files,
    load_manifest, parse_day, question_state, rel, required_spec, sha256_file,
    supersede_cycles, unknown_names,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_manifest import git_status, is_git_ignored  # noqa: E402

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
    # §6б типизирует `wanted_by` и `asked_of` как человека из реестра — проверка
    # смотрела только на участников материалов, и несуществующий заказчик
    # проходил молча, а сводка печатала его как опознанного.
    everyone += [r.get("wanted_by") for r in manifest.get("requirements", [])]
    everyone += [q.get("asked_of") for q in manifest.get("questions", [])]
    everyone += [m.get("to") for q in manifest.get("questions", [])
                 for m in (q.get("raised") or [])]
    everyone = [n for n in everyone if n]
    strangers = unknown_names(manifest, everyone)
    if strangers:
        report.warn(
            "V14",
            "нет в реестре людей: " + ", ".join(strangers[:8])
            + (f" и ещё {len(strangers) - 8}" if len(strangers) > 8 else "")
            + " — заведи через people --add, иначе поиск по человеку неполон",
        )

    # V15 — контракт изъятия (§6). Проверяется и при записи, и здесь: правку
    # манифеста руками или баг в стороннем коде ловит только линтер.
    seen_redactions: set[str] = set()
    for record in manifest.get("redactions", []):
        rid = record.get("id", "?")
        if rid in seen_redactions:
            report.error("V15", "дублирующийся id изъятия", rid)
        seen_redactions.add(rid)
        if record.get("reason") not in REDACTION_REASONS:
            report.error("V15", f"reason={record.get('reason')!r} вне допустимых", rid)
        if not str(record.get("description") or "").strip():
            report.error("V15", "пустое description — непонятно, что изъято", rid)
        if record.get("reversible") and not record.get("vault_ref"):
            report.error(
                "V15", "обратимое изъятие без vault_ref — оригинал негде взять", rid)
        vault = str(record.get("vault_ref") or "")
        if vault and (export / vault).resolve().is_relative_to(export.resolve()):
            # §11.2: изъятое и экспорт не путешествуют вместе. Хранилище внутри
            # архива отменяет смысл изъятия — материал уедет вместе с ним.
            report.error(
                "V15",
                "vault_ref указывает ВНУТРЬ экспорта — изъятое и архив не хранятся вместе",
                rid,
            )

    # V16 — контракт реестра людей (§4б)
    seen_people: set[str] = set()
    selves = [p for p in manifest.get("people", []) if p.get("role") == "self"]
    if len(selves) > 1:
        report.error(
            "V16",
            "ролей self несколько: " + ", ".join(p.get("id", "?") for p in selves)
            + " — непонятно, кто ведёт архив",
        )
    claimed: dict[str, str] = {}
    handles_seen: dict[tuple[str, str], str] = {}
    for person in manifest.get("people", []):
        pid = person.get("id", "?")
        if pid in seen_people:
            report.error("V16", "дублирующийся id человека", pid)
        seen_people.add(pid)
        if person.get("role") not in PERSON_ROLES:
            report.error("V16", f"role={person.get('role')!r} вне допустимых", pid)
        if not str(person.get("display") or "").strip():
            report.error("V16", "пустое display", pid)
        for name in [person.get("display"), pid, *(person.get("aliases") or [])]:
            if not name:
                continue
            key = str(name).strip().lower()
            if key in claimed and claimed[key] != pid:
                report.error(
                    "V16",
                    f"«{name}» заявлен и у {claimed[key]} — опознание вернёт произвольного",
                    pid,
                )
            claimed[key] = pid
        # Один и тот же аккаунт у двух записей означает, что человек заведён
        # дважды. Совпадения алиасов при этом может не быть вовсе — как у пары
        # «Пётр Иванов» и его github-логина, — и без этой проверки дубль
        # остаётся невидимым.
        for kind, value in (person.get("handles") or {}).items():
            key2 = (str(kind).lower(), str(value).strip().lower())
            if not key2[1]:
                continue
            if key2 in handles_seen and handles_seen[key2] != pid:
                report.error(
                    "V16",
                    f"{kind}: {value} заявлен и у {handles_seen[key2]} — "
                    "один аккаунт не может принадлежать двум людям",
                    pid,
                )
            handles_seen[key2] = pid

    # V19 — контракт требования. «Сделано» без доказательства — мнение, а не
    # отчёт; именно на этом вопрос «всё ли мы сделали» и разваливается.
    req_ids = {r.get("id") for r in manifest.get("requirements", [])}
    seen_req: set[str] = set()
    for record in manifest.get("requirements", []):
        rid = record.get("id", "?")
        if rid in seen_req:
            report.error("V19", "дублирующийся id требования", rid)
        seen_req.add(rid)
        if not str(record.get("quote") or "").strip():
            report.error("V19", "пустая цитата — требование без формулировки непроверяемо", rid)
        if record.get("state") not in REQUIREMENT_STATES:
            report.error("V19", f"state={record.get('state')!r} вне допустимых", rid)
        if record.get("state") in ("done", "verified") and not str(record.get("evidence") or "").strip():
            report.error("V19", f"state={record.get('state')} без evidence", rid)
        sup = record.get("supersedes")
        if sup:
            if sup not in req_ids:
                report.error("V19", f"supersedes указывает на несуществующее {sup}", rid)
            if sup == rid:
                report.error("V19", "требование отменяет само себя", rid)
    for cycle in supersede_cycles(manifest):
        # Взаимная отмена помечает отменёнными обе записи, и обе исчезают из
        # живых — содержательное требование пропадает из отчёта без ошибки.
        report.error(
            "V19",
            "цикл отмен: " + " → ".join(cycle + [cycle[0]])
            + " — все участники выпадут из сводки как отменённые",
        )

    # V20 — контракт вопроса. Состояние выводится, поэтому проверяем то, из чего
    # оно выводится: ссылку на ответ и отметки «спрашивали».
    all_ids = req_ids | {i.get("id") for i in items} | {q.get("id") for q in manifest.get("questions", [])}
    seen_q: set[str] = set()
    for record in manifest.get("questions", []):
        qid = record.get("id", "?")
        if qid in seen_q:
            report.error("V20", "дублирующийся id вопроса", qid)
        seen_q.add(qid)
        if not str(record.get("text") or "").strip():
            report.error("V20", "пустой текст вопроса", qid)
        answer = record.get("answered_by")
        if answer and answer == qid:
            report.error("V20", "вопрос отвечает сам на себя — это не ответ", qid)
        elif answer and str(answer).startswith("ctx:"):
            # Внешняя ссылка не проверяется по содержимому — архива под рукой
            # может не быть, — но форму проверить обязаны: иначе `ctx:` работал
            # лазейкой, через которую проходила любая строка.
            if not re.fullmatch(r"ctx:[a-z0-9][a-z0-9-]*#[a-z]\d{3,}", str(answer)):
                report.error(
                    "V20",
                    f"answered_by={answer} не похоже на ссылку ctx:<slug>#<id>",
                    qid,
                )
        elif answer and answer not in all_ids:
            report.error(
                "V20",
                f"answered_by={answer} не ведёт ни в одну запись — "
                "вопрос закрывается ссылкой на доказательство, а не словом",
                qid,
            )
        for raised in record.get("raised") or []:
            if not raised.get("to") or not raised.get("at"):
                report.error("V20", "отметка «спрошено» без адресата или даты", qid)
        if question_state(record) == "open" and record.get("blocking"):
            report.warn(
                "V20",
                f"блокирующий вопрос ни разу не задан: {str(record.get('text'))[:48]}",
                qid,
            )

    # V18 — заявленная версия не отстаёт от содержимого. Манифест, объявляющий
    # 1.0 и содержащий разделы из 1.5, вводит в заблуждение любого читателя:
    # он не ждёт того, что там лежит.
    declared, needed = str(manifest.get("mnemo_spec", "1.0")), required_spec(manifest)
    try:
        older = tuple(int(x) for x in declared.split(".")) < \
            tuple(int(x) for x in needed.split("."))
    except ValueError:
        # Нечитаемая версия — это «проверить невозможно», а §13 требует, чтобы
        # такое было ошибкой, а не падением: traceback не диагноз.
        report.error("V18", f"mnemo_spec={declared!r} — не версия вида X.Y")
        older = False
    if older:
        report.error(
            "V18",
            f"заявлено mnemo_spec={declared}, но содержимое требует {needed} — "
            "любая запись через инструмент поднимет версию",
        )

    # V17 — на один файл ровно одна запись. Две записи на один путь означают,
    # что материалов в архиве меньше, чем он показывает: содержимое одного
    # было затёрто другим, а `rehash` сделал бы это расхождение невидимым.
    owners: dict[str, str] = {}
    for item in items:
        path_str = item.get("raw_path")
        if not path_str:
            continue
        if path_str in owners:
            report.error(
                "V17",
                f"на {path_str} претендует и {owners[path_str]} — "
                "один файл не может быть двумя материалами",
                item["id"],
            )
        owners[path_str] = item["id"]

    # V11 — ничего лишнего в корне
    for entry in sorted(export.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.name not in ALLOWED_TOP:
            report.error("V11", f"файл вне разрешённых зон: {entry.name}")

    # V12 — данные не уедут в чужую репу
    # Публичный срез существует ровно затем, чтобы его показывать: держать его
    # вне git бессмысленно, а правило, требующее этого, делает валидный срез
    # непроходящим. Отбор в срез гарантирует `publish`, а не V12.
    if manifest["export"].get("contour") == "public":
        state = "public-slice"
    else:
        state, _ = git_status(export)
    if state == "public-slice":
        pass
    elif state == "none":
        report.warn("V12", "хост-проект не под git — исключать нечего")
    elif state == "unknown":
        # Незнание — не разрешение. Раньше любой сбой git засчитывался как
        # «репозитория нет», и проверка приватности молча выключалась.
        report.error(
            "V12",
            "не удалось проверить git (недоступен, dubious ownership и т.п.) — "
            "нельзя подтвердить, что данные не уедут в репозиторий",
        )
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
        elif report.ok and args.strict:
            # В строгом режиме предупреждение — повод для ненулевого кода, и
            # значок обязан это отражать: раньше печаталось «✅», а возвращалась
            # единица, то есть вывод противоречил коду выхода.
            print(f"⚠️ {export.name}: предупреждений {len(report.warnings)} "
                  "(строгий режим — считаются ошибками)")
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
