#!/usr/bin/env python3
"""Ответ на вопрос «всё ли мы сделали так, как хотели».

Не поиск и не список всего подряд. Сводка, устроенная так, чтобы по ней можно
было **отчитаться**: каждая строка ведёт к дословной цитате и к доказательству,
а порядок задан тем, что блокирует работу.

Три вещи, ради которых это отдельная команда, а не чтение файлов:

1. **Ранжирование по блокирующести.** Сорок пунктов вперемешку — это шум.
   Поднято из живого `open-questions.md`, где вопросы сгруппированы по тому,
   стоит ли без них работа.
2. **Отсечение уже озвученного.** Вопрос, который спрашивали 30 июля, не
   поднимается снова как новый — видно, кому и когда его задавали.
3. **Отделение подтверждённого от заявленного.** «Сделано» без доказательства
   в сводку как сделанное не попадает.

Использование:
    mnemo_audit.py --export <dir>
    mnemo_audit.py --export <dir> --open-only     только незакрытое
    mnemo_audit.py --export <dir> --json          контракт чтения, SPEC/QUERY.md

`--json` — единственная поддерживаемая точка входа для сторонних инструментов.
Форма вывода нормативна и версионируется (`SPEC/QUERY.md`); всё остальное —
внутреннее устройство, на которое опираться нельзя.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    SPEC_VERSION, STALE_AFTER_DAYS, MnemoError, blocked_since, days_blocked,
    find_export, load_manifest, question_state, resolve_person, stale_reason,
    superseded_ids,
)

# Версия контракта чтения — своя, не версия стандарта: формат вывода может
# устояться раньше, чем формат манифеста, и наоборот. Нормативное описание —
# SPEC/QUERY.md, правила совместимости — STANDARD.md §14.
QUERY_CONTRACT = "2"

STATE_MARK = {
    "verified": "✅", "done": "☑️", "accepted": "⏳",
    "stated": "❓", "dropped": "✖️",
}
Q_MARK = {"open": "❓", "raised": "📨", "answered": "✅", "dropped": "✖️"}


MASK = "‹изъято›"


def visible(record: dict, field: str) -> str:
    """Текст записи или заглушка, если он изъят.

    Изъятие вырезает данные из материала, но дословная цитата требования несёт
    те же самые данные и печатается сводкой. Пока цитату нечем было изъять,
    механизм закрывал сообщение и оставлял его копию рядом.
    """
    if record.get("redactions"):
        return MASK
    return str(record.get(field) or "")


def cut(text: str, limit: int) -> str:
    """Обрезать по границе слова: «всё в »» с висящей кавычкой читается плохо."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    return (head or text[:limit]) + "…"


def who(manifest: dict, ident: str | None) -> str:
    if not ident:
        return "—"
    person = resolve_person(manifest, ident)
    return person["display"] if person else ident


REQ_DEFAULTS = {"quote": "<без формулировки>", "state": "stated", "date": "1970-01-01",
                "based_on": [], "blocking": None, "evidence": None, "stage": None,
                "note": None, "wanted_by": None, "supersedes": None, "id": "?"}
Q_DEFAULTS = {"text": "<без текста>", "date": "1970-01-01", "raised": [], "blocking": None,
              "impact": None, "answered_by": None, "asked_of": None, "id": "?"}


def derived(record: dict, kind: str) -> dict:
    """Выведенное состояние — рядом с записью, а не только в порядке сортировки.

    Раньше «висит третий день» существовало лишь как ключ сортировки, и любой
    сторонний потребитель был вынужден заново написать `blocked_since` вместе с
    его правилом отката на дату записи. Два места, где вычисляется одно и то же,
    расходятся — поэтому вывод отдаётся готовым.
    """
    return {"blocked_since": blocked_since(record), "days_blocked": days_blocked(record),
            "stale_reason": stale_reason(record, kind)}


# Поля, которые в контракт чтения не выходят: рядом с выведенным соседом они —
# ловушка, отличающаяся парой букв.
#
# `superseded` (булево, «меня отменили») стояло вплотную к хранимому
# `supersedes` («я отменяю вот это»). Одно слово, две буквы разницы и
# **противоположные направления**: прочитавший не то показал бы отменённое
# требование живым, а живое спрятал. Наружу идёт `superseded_by` — не флаг, а
# идентификатор того, чем заменено; направление читается из имени.
SHADOWED = {"blocking_since": "blocked_since", "superseded": "superseded_by"}


def for_contract(record: dict) -> dict:
    """Запись в том виде, в каком её отдают наружу.

    `blocking_since` — хранимое и чаще всего `null`: блокировка обычно
    появляется вместе с записью, и тогда дата берётся из `date`. Выведенное
    `blocked_since` даёт настоящий ответ. Имена различались двумя буквами и
    лежали в одном объекте, так что `blocking_since: null` соседствовал с
    `blocked_since: "2026-08-04"`.

    Потребитель, прочитавший хранимое, получал «не блокирует» и **молча**
    терял все блокеры — результат выглядел как «блокеров сегодня нет». Ровно
    тот класс ошибки, ради которого контракт и заводился: два поля про одно и
    то же, расходящиеся незаметно.

    Поэтому наружу идёт только выведенное. В манифесте хранимое остаётся: там
    оно единственное и ни с чем не соседствует.
    """
    return {k: v for k, v in record.items() if k not in SHADOWED}


def collect(manifest: dict) -> dict:
    # Манифест могли править руками или сторонним кодом. Сводка обязана
    # показать повреждённую запись, а не упасть на ней: падение прячет и все
    # остальные, а разбираться человеку — по выводу линтера.
    dead = superseded_ids(manifest)
    # Кем именно отменено — обратная сторона `supersedes`. Флага «отменено» мало:
    # он не говорит, чем заменено, и потребителю пришлось бы строить эту карту
    # самому, разбирая манифест.
    replaced_by = {r["supersedes"]: r.get("id") for r in manifest.get("requirements", [])
                   if r.get("supersedes")}
    reqs = []
    for record in manifest.get("requirements", []):
        full = {**REQ_DEFAULTS, **record}
        if full["state"] not in ("stated", "accepted", "done", "verified", "dropped"):
            full["state"] = "stated"
        reqs.append({**full, "superseded": full["id"] in dead,
                     "superseded_by": replaced_by.get(full["id"]),
                     **derived(full, "requirement")})
    questions = [{**Q_DEFAULTS, **q, "state": question_state(q), **derived(q, "question")}
                 for q in manifest.get("questions", [])]

    # Порядок: блокирующее вперёд, среди блокирующего — давнее вперёд, затем
    # незакрытое, затем по дате. «Висит третий день» должно быть видно сверху. Ровно то,
    # чего не хватало, когда сводка возвращала важное вперемешку с неважным.
    def req_key(r):
        return (0 if r.get("blocking") else 1,
                -(days_blocked(r) or 0),
                {"stated": 0, "accepted": 1, "done": 2, "verified": 3, "dropped": 4}[r["state"]],
                r["date"])

    def q_key(q):
        return (0 if q.get("blocking") else 1,
                -(days_blocked(q) or 0),
                {"open": 0, "raised": 1, "answered": 2, "dropped": 3}[q["state"]],
                q["date"])

    return {"requirements": sorted(reqs, key=req_key),
            "questions": sorted(questions, key=q_key)}


def report(manifest: dict, data: dict, open_only: bool) -> list[str]:
    meta = manifest["export"]
    reqs, questions = data["requirements"], data["questions"]
    live = [r for r in reqs if not r["superseded"] and r["state"] != "dropped"]
    confirmed = [r for r in live if r["state"] == "verified"]
    claimed = [r for r in live if r["state"] == "done"]
    pending = [r for r in live if r["state"] in ("stated", "accepted")]
    open_q = [q for q in questions if q["state"] in ("open", "raised")]
    # Протухшее — отдельно от открытого. Список открытых создаёт видимость
    # работы, пока в нём вперемешку лежат вчерашние и те, что спросили две
    # недели назад и не дождались ответа. Вторые требуют действия другого рода:
    # переспросить или снять, а не ждать дальше.
    stale = [r for r in live if r.get("stale_reason")] + \
            [q for q in questions if q.get("stale_reason")]

    out = [f"АУДИТ — {meta['title']}", ""]
    # Прежняя формулировка «заявлено сделанным» читалась как «без доказательства»,
    # хотя `done` без доказательства линтер не пропускает вовсе. Разница между
    # done и verified — не в наличии доказательства, а в том, принял ли его тот,
    # кто требование выдвинул.
    out.append(f"Требований живых: {len(live)} — "
               f"принято заказчиком {len(confirmed)}, "
               f"сделано и ждёт приёмки {len(claimed)}, "
               f"в работе {len(pending)}")
    out.append(f"Вопросов открытых: {len(open_q)} из {len(questions)}")
    superseded = [r for r in reqs if r["superseded"]]
    if superseded:
        out.append(f"Отменено более поздними: {len(superseded)} — "
                   "проверь, что зависевшее от них пересмотрено")
    out.append("")

    blocking_r = [r for r in pending if r.get("blocking")]
    blocking_q = [q for q in open_q if q.get("blocking")]
    if blocking_r or blocking_q:
        out += ["━━━ БЕЗ ЭТОГО РАБОТА СТОИТ ━━━", ""]
        for r in blocking_r:
            age = days_blocked(r)
            tail = f"  ({age} дн.)" if age is not None and age > 0 else ""
            out.append(f"  {r['id']}  «{cut(visible(r, 'quote'), 70)}»{tail}")
            out.append(f"       стоит: {r['blocking']}")
            out.append(f"       хочет: {who(manifest, r.get('wanted_by'))}"
                       + (f" · {', '.join(r['based_on'])}" if r.get("based_on") else ""))
        for q in blocking_q:
            mark = "уже спрашивали" if q["raised"] else "НЕ СПРАШИВАЛИ"
            age = days_blocked(q)
            tail = f"  ({age} дн.)" if age is not None and age > 0 else ""
            out.append(f"  {q['id']}  {cut(visible(q, 'text'), 70)}{tail}")
            out.append(f"       стоит: {q['blocking']}   [{mark}]")
            if not q["raised"] and q.get("asked_of"):
                # «Не спрашивали» без указания, у кого спрашивать, — половина
                # ответа. Именно эта строка объявлена самой ценной в выводе.
                out.append(f"       спросить у: {who(manifest, q['asked_of'])}")
            if q.get("impact"):
                out.append(f"       от ответа зависит: {cut(q['impact'], 70)}")
            for raised in q["raised"]:
                out.append(f"       спрошено {raised['at']} у {who(manifest, raised['to'])}"
                           + (f" ({raised['where']})" if raised.get("where") else ""))
        out.append("")

    rest_q = [q for q in open_q if not q.get("blocking")]
    if rest_q:
        out += ["━━━ ОТКРЫТЫЕ ВОПРОСЫ ━━━", ""]
        for q in rest_q:
            tail = ""
            if q["raised"]:
                last = q["raised"][-1]
                tail = f"  ← спрошено {last['at']} у {who(manifest, last['to'])}"
            out.append(f"  {Q_MARK[q['state']]} {q['id']}  {cut(visible(q, 'text'), 66)}{tail}")
        out.append("")

    if stale:
        out += [f"━━━ ПРОТУХЛО (больше {STALE_AFTER_DAYS} дн. без движения) ━━━", ""]
        for record in stale:
            field = "quote" if str(record["id"]).startswith("t") else "text"
            out.append(f"  {record['id']}  {cut(visible(record, field), 62)}")
            out.append(f"       {record['stale_reason']}")
        out.append("")

    if not open_only:
        out += ["━━━ ТРЕБОВАНИЯ ━━━", ""]
        for r in [x for x in reqs if not x["superseded"]]:
            mark = STATE_MARK[r["state"]]
            out.append(f"  {mark} {r['id']}  «{cut(visible(r, 'quote'), 64)}»")
            detail = []
            if r.get("evidence"):
                detail.append(f"подтверждено: {r['evidence']}")
            elif r["state"] in ("stated", "accepted"):
                detail.append("доказательства нет")
            if r.get("stage"):
                detail.append(f"этап: {r['stage']}")
            if r.get("note"):
                detail.append(f"⚠️ {r['note']}")
            if detail:
                out.append(f"       {' · '.join(detail)}")
        out.append("")

    # Итог формулируем как ответ, а не как статистику: именно он и нужен.
    out.append("━━━ ОТВЕТ ━━━")
    if stale:
        out.append(f"Протухло без движения: {len(stale)} — их не ждут, "
                   "а переспрашивают или снимают.")
    if not live:
        out.append("Требования не заведены — отвечать не на чем.")
    elif claimed or pending:
        parts = []
        if pending:
            parts.append(f"{len(pending)} не закрыто")
        if claimed:
            parts.append(f"{len(claimed)} сделано, но не проверено")
        out.append("Нет, не всё: " + ", ".join(parts) + ".")
        if blocking_r or blocking_q:
            out.append(f"Из них блокирует работу: {len(blocking_r) + len(blocking_q)}.")
    else:
        out.append(f"Да: все {len(confirmed)} требований подтверждены доказательством.")
    if open_q:
        never = [q for q in open_q if not q["raised"]]
        never_blocking = [q for q in never if q.get("blocking")]
        if never_blocking:
            out.append(f"Блокирует и ни разу не спрошено: {len(never_blocking)} — "
                       "задать в первую очередь.")
        rest_never = len(never) - len(never_blocking)
        if rest_never:
            out.append(f"Не спрошено ни разу, но работу не блокирует: {rest_never}.")
    return out


def main() -> int:
    argp = argparse.ArgumentParser(description="Сводка: всё ли сделано как хотели")
    argp.add_argument("--export", default=".")
    argp.add_argument("--open-only", action="store_true", help="только незакрытое")
    argp.add_argument("--json", action="store_true")
    args = argp.parse_args()

    try:
        export = find_export(Path(args.export))
        manifest = load_manifest(export)
        data = collect(manifest)
    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1

    if args.json:
        if args.open_only:
            data = {
                "requirements": [r for r in data["requirements"]
                                 if not r["superseded"] and r["state"] in ("stated", "accepted")],
                "questions": [q for q in data["questions"] if q["state"] in ("open", "raised")],
            }
        meta = manifest["export"]
        print(json.dumps({
            "query_contract": QUERY_CONTRACT,
            "mnemo_spec": manifest.get("mnemo_spec", SPEC_VERSION),
            # slug нужен потребителю, чтобы собрать `ctx:<slug>#<id>`. Без него
            # ссылку не построить, и он полез бы за ней в манифест — ровно то,
            # чего контракт чтения должен избавить.
            "export": {"slug": meta.get("slug"), "title": meta.get("title")},
            **{bucket: [for_contract(r) for r in records]
               for bucket, records in data.items()},
        }, ensure_ascii=False, indent=2))
    else:
        print("\n".join(report(manifest, data, args.open_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
