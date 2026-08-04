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
    mnemo_audit.py --export <dir> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    MnemoError, find_export, load_manifest, question_state, resolve_person,
    superseded_ids,
)

STATE_MARK = {
    "verified": "✅", "done": "☑️", "accepted": "⏳",
    "stated": "❓", "dropped": "✖️",
}
Q_MARK = {"open": "❓", "raised": "📨", "answered": "✅", "dropped": "✖️"}


def who(manifest: dict, ident: str | None) -> str:
    if not ident:
        return "—"
    person = resolve_person(manifest, ident)
    return person["display"] if person else ident


def collect(manifest: dict) -> dict:
    dead = superseded_ids(manifest)
    reqs = []
    for record in manifest.get("requirements", []):
        reqs.append({**record, "superseded": record["id"] in dead})
    questions = [{**q, "state": question_state(q)} for q in manifest.get("questions", [])]

    # Порядок: блокирующее вперёд, затем незакрытое, затем по дате. Ровно то,
    # чего не хватало, когда сводка возвращала важное вперемешку с неважным.
    def req_key(r):
        return (0 if r.get("blocking") else 1,
                {"stated": 0, "accepted": 1, "done": 2, "verified": 3, "dropped": 4}[r["state"]],
                r["date"])

    def q_key(q):
        return (0 if q.get("blocking") else 1,
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

    out = [f"АУДИТ — {meta['title']}", ""]
    out.append(f"Требований: {len(live)} живых "
               f"(подтверждено {len(confirmed)}, заявлено сделанным {len(claimed)}, "
               f"в работе {len(pending)})")
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
            out.append(f"  {r['id']}  «{r['quote'][:70]}»")
            out.append(f"       стоит: {r['blocking']}")
            out.append(f"       хочет: {who(manifest, r.get('wanted_by'))}"
                       + (f" · {', '.join(r['based_on'])}" if r.get("based_on") else ""))
        for q in blocking_q:
            mark = "уже спрашивали" if q["raised"] else "НЕ СПРАШИВАЛИ"
            out.append(f"  {q['id']}  {q['text'][:70]}")
            out.append(f"       стоит: {q['blocking']}   [{mark}]")
            if q.get("impact"):
                out.append(f"       от ответа зависит: {q['impact'][:70]}")
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
            out.append(f"  {Q_MARK[q['state']]} {q['id']}  {q['text'][:66]}{tail}")
        out.append("")

    if not open_only:
        out += ["━━━ ТРЕБОВАНИЯ ━━━", ""]
        for r in [x for x in reqs if not x["superseded"]]:
            mark = STATE_MARK[r["state"]]
            out.append(f"  {mark} {r['id']}  «{r['quote'][:64]}»")
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
        if never:
            out.append(f"Не спрошено ни разу: {len(never)} — их стоит задать.")
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
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("\n".join(report(manifest, data, args.open_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
