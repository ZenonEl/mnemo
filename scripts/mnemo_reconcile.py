"""Атомарная модель пакетов, сверок и обратной связи Mnemo.

Модуль не содержит CLI: команды в ``mnemo_manifest.py`` лишь разбирают ввод,
а инварианты живут здесь и одинаково работают для человека и автоматизации.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from mnemo_core import (
    FACETS, MANIFEST_NAME, MnemoError, new_question, new_requirement, next_id,
    parse_day, question_state, resolve_person, today,
)

IMPACT_KINDS = ("scope", "deadline", "cost", "responsibility", "promise", "risk", "work")


def csv_values(raw: str | None) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for value in (raw or "").split(","):
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def file_sha256(export: Path) -> str:
    return hashlib.sha256((export / MANIFEST_NAME).read_bytes()).hexdigest()


def append_package(manifest: dict, kind: str, items: list[str], **extra: Any) -> dict | None:
    """Зафиксировать точный состав одного непустого внесения."""
    if not items:
        return None
    package = {"id": next_id(manifest, "package"), "kind": kind,
               "parser": extra.pop("parser", None), "source": extra.pop("source", None),
               "imported": extra.pop("imported", today()), "items": list(items)}
    package.update(extra)
    manifest.setdefault("imports", []).append(package)
    return package


def next_review_id(manifest: dict) -> str:
    return next_id(manifest, "review")


def review_number(value: Any, default: int = 0) -> int:
    match = re.fullmatch(r"s(\d+)", str(value or ""))
    return int(match.group(1)) if match else default


def find_review(manifest: dict, review_id: str) -> dict:
    review = next((r for r in manifest.get("reviews", []) if r.get("id") == review_id), None)
    if review is None:
        raise MnemoError(f"нет сверки {review_id}")
    return review


def _source_item_ids(manifest: dict, scope: list[str]) -> list[str]:
    packages = {p.get("id"): p for p in manifest.get("imports", []) if p.get("id")}
    unknown = [package_id for package_id in scope if package_id not in packages]
    if unknown:
        raise MnemoError(f"неизвестные пакеты в scope: {', '.join(unknown)}")
    retired = {r.get("id") for r in manifest.get("retired", [])}
    result: list[str] = []
    for package_id in scope:
        package_items = packages[package_id].get("items")
        if not isinstance(package_items, list):
            raise MnemoError(f"пакет {package_id} не имеет точного items")
        for item_id in package_items:
            if item_id in retired:
                continue
            if item_id not in result:
                result.append(item_id)
    return result


def _dedupe(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(str(v).strip() for v in values or [] if str(v).strip()))


VAGUE = {"это", "вопрос", "повтор", "надо", "нужно", "решили", "прочее",
         "разбирался", "изучал", "проверял", "не", "получилось"}


def is_vague(value: Any) -> bool:
    words = re.findall(r"[\w-]+", str(value or "").casefold())
    return not words or all(word in VAGUE for word in words)


def new_decision(manifest: dict, **kwargs: Any) -> dict:
    record = {
        "id": kwargs["id"], "text": kwargs.get("text"),
        "decided_by": kwargs.get("decided_by"), "reason": kwargs.get("reason"),
        "based_on": _dedupe(kwargs.get("based_on")),
        "facet": kwargs.get("facet") or "product", "area": kwargs.get("area"),
        "supersedes": kwargs.get("supersedes"), "date": kwargs.get("date") or today(),
        "note": kwargs.get("note"), "redactions": list(kwargs.get("redactions") or []),
    }
    if not str(record["text"] or "").strip():
        raise MnemoError("решение требует непустой text")
    if is_vague(record["reason"]):
        raise MnemoError("решение требует содержательный reason")
    if not record["based_on"]:
        raise MnemoError("решение требует непустой based_on")
    if record["facet"] not in FACETS:
        raise MnemoError(f"facet должен быть одним из {FACETS}")
    if resolve_person(manifest, str(record["decided_by"] or "")) is None:
        raise MnemoError("decided_by должен быть человеком из реестра")
    record["decided_by"] = resolve_person(manifest, record["decided_by"])["id"]
    parse_day(record["date"])
    return record


def new_fact(manifest: dict, **kwargs: Any) -> dict:
    record = {
        "id": kwargs["id"], "text": kwargs.get("text"),
        "stated_by": kwargs.get("stated_by"), "based_on": _dedupe(kwargs.get("based_on")),
        "verification": kwargs.get("verification"), "verified_by": kwargs.get("verified_by"),
        "facet": kwargs.get("facet") or "product", "area": kwargs.get("area"),
        "supersedes": kwargs.get("supersedes"), "date": kwargs.get("date") or today(),
        "note": kwargs.get("note"), "redactions": list(kwargs.get("redactions") or []),
    }
    if not str(record["text"] or "").strip():
        raise MnemoError("утверждение требует непустой text")
    if not record["based_on"]:
        raise MnemoError("утверждение требует непустой based_on")
    if record["facet"] not in FACETS:
        raise MnemoError(f"facet должен быть одним из {FACETS}")
    for field in ("stated_by", "verified_by"):
        if record[field] is not None:
            person = resolve_person(manifest, str(record[field]))
            if person is None:
                raise MnemoError(f"{field} должен быть человеком из реестра")
            record[field] = person["id"]
    if record["verified_by"] and not str(record["verification"] or "").strip():
        raise MnemoError("verified_by без verification не доказывает утверждение")
    if record["verification"] and is_vague(record["verification"]):
        raise MnemoError("verification должна описывать конкретное доказательство")
    parse_day(record["date"])
    return record


def _local_target(manifest: dict, ref: Any) -> str | None:
    value = str(ref or "")
    if value.startswith("ctx:"):
        head, _, target = value.partition("#")
        if head != f"ctx:{manifest.get('export', {}).get('slug')}":
            return None
        return target
    return value or None


def validate_graph(manifest: dict, strict_sources: set[str] | None = None) -> None:
    buckets = ("requirements", "questions", "decisions", "facts")
    records = {r.get("id"): r for bucket in buckets for r in manifest.get(bucket, [])}
    strict = set(records) if strict_sources is None else set(strict_sources)
    item_ids = {i.get("id") for i in manifest.get("items", [])}
    edges: dict[str, list[str]] = {record_id: [] for record_id in records}
    for source, record in records.items():
        refs = list(record.get("based_on") or [])
        for field in ("answered_by", "evidence", "verification"):
            value = record.get(field)
            if value:
                refs.append(value)
        for ref in refs:
            target = _local_target(manifest, ref)
            if target is None:  # внешняя ctx-ссылка — проверяемая только там
                continue
            if target not in records and target not in item_ids:
                # Произвольный текст evidence/verification остаётся доказательством,
                # а похожая на id ссылка обязана существовать.
                if source in strict and re.fullmatch(r"[itqdf]\d{3,}", str(target)):
                    raise MnemoError(f"{source} ссылается на несуществующее {target}")
                continue
            if target == source and source in strict:
                raise MnemoError(f"{source} не может ссылаться на себя")
            if target in records:
                edges[source].append(target)

    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            start = trail.index(node)
            raise MnemoError("цикл происхождения: " + " → ".join(trail[start:] + [node]))
        if node in visited:
            return
        visiting.add(node)
        for target in edges[node]:
            visit(target, trail + [target])
        visiting.remove(node)
        visited.add(node)
    for record_id in strict:
        if record_id not in edges:
            continue
        visit(record_id, [record_id])

    for bucket, prefix in (("requirements", "t"), ("decisions", "d"), ("facts", "f")):
        parents = {}
        for record in manifest.get(bucket, []):
            target = record.get("supersedes")
            if target:
                if target == record.get("id") or target not in records or not str(target).startswith(prefix):
                    raise MnemoError(f"{record.get('id')}: недопустимый supersedes {target}")
                parents[record["id"]] = target
        for source in parents:
            seen: set[str] = set()
            current = source
            while current in parents:
                if current in seen:
                    raise MnemoError(f"цикл замен в {bucket}: {source}")
                seen.add(current)
                current = parents[current]


def _delta_for_create(record_id: str) -> list[dict]:
    return [{"field": "id", "before": None, "after": record_id}]


def _expanded_items(raw: list[str], scoped: list[str], retired: set[str]) -> list[str]:
    """Развернуть iAAA-iBBB, не позволяя диапазону захватить чужой item."""
    out: list[str] = []
    for value in raw:
        if "-" not in value:
            values = [value]
        else:
            left, right = value.split("-", 1)
            if not (left.startswith("i") and right.startswith("i")
                    and left[1:].isdigit() and right[1:].isdigit()
                    and int(left[1:]) <= int(right[1:])):
                raise MnemoError(f"нечитаемый диапазон материалов: {value}")
            width = max(len(left) - 1, len(right) - 1, 3)
            values = [f"i{number:0{width}d}"
                      for number in range(int(left[1:]), int(right[1:]) + 1)]
        for item_id in values:
            if item_id in retired:
                continue
            if item_id not in scoped:
                raise MnemoError(f"{item_id} из nonmaterial не входит в scope")
            if item_id not in out:
                out.append(item_id)
    return out


def _material_change(manifest: dict, change: dict) -> bool:
    action, record_id = change.get("action"), str(change.get("record") or "")
    if action == "superseded" or (action == "created" and record_id.startswith(("t", "d"))):
        return True
    if action == "answered" and record_id.startswith("q"):
        return True
    if action == "created" and record_id.startswith("f"):
        fact = next((f for f in manifest.get("facts", []) if f.get("id") == record_id), {})
        return bool(fact.get("verification"))
    if action == "created" and record_id.startswith("q"):
        question = next((q for q in manifest.get("questions", []) if q.get("id") == record_id), {})
        person = next((p for p in manifest.get("people", [])
                       if p.get("id") == question.get("asked_of")), None)
        return bool(person and person.get("role") != "self")
    if action == "updated":
        return any(d.get("field") == "blocking" and d.get("after") for d in change.get("delta", []))
    return False


def review_material(manifest: dict, review: dict) -> bool:
    return bool(review.get("impacts")) or any(
        _material_change(manifest, change) for change in review.get("changes", [])
    )


def apply_review_plan(manifest: dict, plan: dict) -> dict:
    scope = list(plan.get("scope") or [])
    if not scope:
        raise MnemoError("scope сверки пуст")
    scoped_items = _source_item_ids(manifest, scope)
    creates = list(plan.get("creates") or [])
    mutations = list(plan.get("mutations") or [])
    nonmaterial = list(plan.get("nonmaterial") or [])
    impacts = list(plan.get("impacts") or [])

    retired = {record.get("id") for record in manifest.get("retired", [])}
    for group in nonmaterial:
        group["items"] = _expanded_items(
            list(group.get("items") or []), scoped_items, retired
        )
        if is_vague(group.get("reason")):
            raise MnemoError("nonmaterial требует содержательную причину")
    impact_numbers: set[int] = set()
    for impact in impacts:
        number = impact.get("n")
        if not isinstance(number, int) or number < 1 or number in impact_numbers:
            raise MnemoError("impact.n должен быть уникальным положительным целым")
        impact_numbers.add(number)
        if impact.get("kind") not in IMPACT_KINDS:
            raise MnemoError("impact.kind должен быть одним из " + ", ".join(IMPACT_KINDS))
        if is_vague(impact.get("text")) or not impact.get("based_on"):
            raise MnemoError("impact требует содержательный text и непустой based_on")

    covered: list[str] = []
    for change in creates + mutations:
        source_items = list(change.get("source_items") or [])
        if not source_items:
            raise MnemoError("каждое изменение требует непустой source_items")
        for item_id in source_items:
            if item_id not in covered:
                covered.append(item_id)
    for group in nonmaterial:
        for item_id in group.get("items") or []:
            if item_id not in covered:
                covered.append(item_id)
    missing = [item_id for item_id in scoped_items if item_id not in covered]
    outside = [item_id for item_id in covered if item_id not in scoped_items]
    changed_items = {item_id for change in creates + mutations
                     for item_id in change.get("source_items") or []}
    nonmaterial_items = {item_id for group in nonmaterial
                         for item_id in group.get("items") or []}
    overlap = changed_items & nonmaterial_items
    if missing or outside or overlap:
        parts = []
        if missing:
            parts.append("не разобраны: " + ", ".join(missing))
        if outside:
            parts.append("не входят в scope: " + ", ".join(outside))
        if overlap:
            parts.append("одновременно change и nonmaterial: " + ", ".join(sorted(overlap)))
        raise MnemoError("неполное покрытие сверки — " + "; ".join(parts))

    staged = {**manifest}
    staged["requirements"] = [dict(r) for r in manifest.get("requirements", [])]
    staged["questions"] = [dict(q) for q in manifest.get("questions", [])]
    staged["decisions"] = [dict(d) for d in manifest.get("decisions", [])]
    staged["facts"] = [dict(f) for f in manifest.get("facts", [])]
    changes: list[dict] = []
    for creation in creates:
        kind = creation.get("kind")
        source_items = list(creation.get("source_items") or [])
        raw = dict(creation.get("record") or {})
        if kind == "requirement":
            wanted = resolve_person(staged, str(raw.get("wanted_by") or ""))
            if wanted is None:
                raise MnemoError("wanted_by требования должен быть человеком из реестра")
            raw["wanted_by"] = wanted["id"]
            raw["id"] = next_id(staged, "requirement")
            record = new_requirement(**raw)
            staged["requirements"].append(record)
        elif kind == "question":
            asked = resolve_person(staged, str(raw.get("asked_of") or ""))
            if asked is None:
                raise MnemoError("asked_of вопроса должен быть человеком из реестра")
            raw["asked_of"] = asked["id"]
            if not str(raw.get("impact") or "").strip() or not raw.get("based_on"):
                raise MnemoError("вопрос требует impact, asked_of и непустой based_on")
            if is_vague(raw.get("self_attempt")):
                raise MnemoError("вопрос требует конкретный self_attempt")
            raw["id"] = next_id(staged, "question")
            record = new_question(**raw)
            staged["questions"].append(record)
        elif kind == "decision":
            raw["id"] = next_id(staged, "decision")
            record = new_decision(staged, **raw)
            staged.setdefault("decisions", []).append(record)
        elif kind == "fact":
            raw["id"] = next_id(staged, "fact")
            record = new_fact(staged, **raw)
            staged.setdefault("facts", []).append(record)
        else:
            raise MnemoError(f"неизвестный kind создаваемой записи: {kind!r}")
        changes.append({"action": "created", "record": record["id"],
                        "source_items": source_items,
                        "delta": _delta_for_create(record["id"]), "note": creation.get("note")})
        if record.get("supersedes"):
            target_bucket = {"requirement": "requirements", "decision": "decisions",
                             "fact": "facts"}.get(kind)
            old = next((r for r in staged.get(target_bucket or "", [])
                        if r.get("id") == record["supersedes"]), None)
            if old is None:
                raise MnemoError(f"нет отменяемой записи {record['supersedes']}")
            changes.append({
                "action": "superseded", "record": old["id"],
                "source_items": source_items,
                "delta": [{"field": "superseded_by", "before": None,
                           "after": record["id"]}], "note": creation.get("note"),
            })

    # Мутации входят в контракт S1: меняется только явно названное поле, а
    # замороженная delta сохраняет обе стороны изменения.
    mutated_fields: set[tuple[str, str]] = set()
    for mutation in mutations:
        record_id = str(mutation.get("record") or "")
        field = str(mutation.get("field") or "")
        bucket = ({"t": "requirements", "q": "questions", "d": "decisions",
                   "f": "facts"}.get(record_id[:1]) or "")
        record = next((r for r in staged.get(bucket, []) if r.get("id") == record_id), None)
        if record is None or not field or field == "id":
            raise MnemoError(f"неприменимая мутация: {record_id}.{field}")
        mutation_key = (record_id, field)
        if mutation_key in mutated_fields:
            raise MnemoError(f"поле {record_id}.{field} изменяется в плане больше одного раза")
        mutated_fields.add(mutation_key)
        before = record.get(field)
        record[field] = mutation.get("value")
        if bucket == "requirements":
            normalized = new_requirement(**record)
        elif bucket == "questions":
            normalized = new_question(**record)
        elif bucket == "decisions":
            normalized = new_decision(staged, **record)
        else:
            normalized = new_fact(staged, **record)
        record.clear()
        record.update(normalized)
        action = "answered" if field == "answered_by" else \
            "confirmed" if field == "based_on" else "updated"
        changes.append({"action": action, "record": record_id,
                        "source_items": list(mutation.get("source_items") or []),
                        "delta": [{"field": field, "before": before,
                                   "after": record.get(field)}],
                        "note": mutation.get("note")})

    review = {
        "id": next_review_id(staged), "date": today(), "by": plan.get("by"),
        "scope": scope, "changes": changes, "nonmaterial": nonmaterial,
        "impacts": impacts, "feedback": [], "note": plan.get("note"),
    }
    manifest["requirements"] = staged["requirements"]
    manifest["questions"] = staged["questions"]
    manifest["decisions"] = staged.get("decisions", [])
    manifest["facts"] = staged.get("facts", [])
    validate_graph(staged)
    known_records = {r.get("id") for bucket in ("requirements", "questions", "decisions", "facts")
                     for r in staged.get(bucket, [])}
    for impact in impacts:
        bad = [ref for ref in list(impact.get("based_on") or [])
               + list(impact.get("affects") or []) if ref not in known_records]
        if bad:
            raise MnemoError("impact ссылается на неизвестные записи: " + ", ".join(bad))
    manifest.setdefault("reviews", []).append(review)
    return review


def feedback_content_sha256(feedback: dict) -> str:
    immutable = {key: feedback.get(key) for key in (
        "n", "audience", "needed", "reason", "form", "rendered_text", "style",
        "includes", "selected_question", "supersedes_n", "date",
    )}
    payload = json.dumps(immutable, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_feedback_contract(manifest: dict, review: dict) -> None:
    """Проверить сохранённые редакции feedback без повторного их создания."""
    audience_map = {a.get("name"): a for a in manifest.get("export", {}).get("audiences", [])}
    change_records = {c.get("record") for c in review.get("changes", [])}
    impact_numbers = {str(i.get("n")) for i in review.get("impacts", [])}
    feedbacks = list(review.get("feedback") or [])
    by_number: dict[int, dict] = {}
    superseded: set[int] = set()
    for feedback in feedbacks:
        try:
            number = int(feedback.get("n"))
        except (TypeError, ValueError) as exc:
            raise MnemoError("feedback.n должен быть целым положительным числом") from exc
        if number < 1 or number in by_number:
            raise MnemoError(f"feedback.n повторён или недопустим: {number}")
        by_number[number] = feedback
        parse_day(str(feedback.get("date") or ""))
        audience = feedback.get("audience")
        if audience not in audience_map:
            raise MnemoError(f"feedback ссылается на неизвестную аудиторию {audience!r}")
        needed = feedback.get("needed")
        if needed is True:
            if feedback.get("form") not in ("confirmation", "clarification"):
                raise MnemoError(f"feedback {number}: неверная form")
            if not str(feedback.get("rendered_text") or "").strip():
                raise MnemoError(f"feedback {number}: пустой rendered_text")
            if feedback.get("style") != "brief":
                raise MnemoError(f"feedback {number}: style должен быть brief")
        elif needed is False:
            if is_vague(feedback.get("reason")):
                raise MnemoError(f"feedback {number}: нужна содержательная причина not-needed")
        else:
            raise MnemoError(f"feedback {number}: needed должен быть bool")
        includes = feedback.get("includes") or {}
        records = list(includes.get("records") or [])
        impacts = [str(value) for value in includes.get("impacts") or []]
        if needed and not (records or impacts):
            raise MnemoError(f"feedback {number}: includes пуст")
        bad = [value for value in records if value not in change_records]
        bad += [value for value in impacts if value not in impact_numbers]
        if bad:
            raise MnemoError(f"feedback {number}: includes вне сверки: {', '.join(bad)}")
        selected = feedback.get("selected_question") or {}
        question_id = selected.get("id") if isinstance(selected, dict) else selected
        if question_id:
            question = next((q for q in manifest.get("questions", [])
                             if q.get("id") == question_id), None)
            if question is None or question_id not in records:
                raise MnemoError(f"feedback {number}: selected_question вне includes")
            if question.get("asked_of") not in audience_map[audience].get("people", []):
                raise MnemoError(f"feedback {number}: asked_of вне audience.people")
        parent = feedback.get("supersedes_n")
        if parent is not None:
            if parent not in by_number or by_number[parent].get("audience") != audience:
                raise MnemoError(f"feedback {number}: неверный supersedes_n {parent}")
            if parent in superseded:
                raise MnemoError(f"feedback {number}: редакция {parent} уже заменена")
            superseded.add(parent)
        expected_hash = feedback_content_sha256(feedback)
        if feedback.get("content_sha256") != expected_hash:
            raise MnemoError(f"feedback {number}: содержимое изменено после создания")
        seen_deliveries: set[tuple[str, str]] = set()
        for index, delivery in enumerate(feedback.get("deliveries") or []):
            key = (str(delivery.get("where") or ""), str(delivery.get("ref") or ""))
            if not all(key) or not delivery.get("date") or key in seen_deliveries:
                raise MnemoError(f"feedback {number}: повреждена delivery")
            parse_day(str(delivery.get("date")))
            if index and is_vague(delivery.get("reason")):
                raise MnemoError(f"feedback {number}: повторная delivery без причины")
            seen_deliveries.add(key)
            if question_id:
                expected_where = f"{key[0]} {key[1]}".strip()
                if not any(
                    mark.get("to") == question.get("asked_of")
                    and mark.get("at") == delivery.get("date")
                    and mark.get("where") == expected_where
                    for mark in question.get("raised", [])
                ):
                    raise MnemoError(
                        f"feedback {number}: delivery не отражена в raised вопроса {question_id}"
                    )


def suggested_feedback_form(manifest: dict, review: dict) -> str:
    """Подсказка, не решение: неоднозначность требует более явного сообщения."""
    if any(change.get("action") == "superseded" for change in review.get("changes", [])):
        return "clarification"
    for change in review.get("changes", []):
        record_id = str(change.get("record") or "")
        if not record_id.startswith("q"):
            continue
        question = next((q for q in manifest.get("questions", [])
                         if q.get("id") == record_id), None)
        person = resolve_person(manifest, str((question or {}).get("asked_of") or ""))
        if person and person.get("role") != "self" and question_state(question) in ("open", "raised"):
            return "clarification"
    return "confirmation"


def add_feedback(manifest: dict, review: dict, *, audience: str, needed: bool, form: str | None,
                 text: str | None, reason: str | None, includes_records: list[str],
                 includes_impacts: list[str], selected_question: str | None,
                 supersedes_n: int | None, resend: bool) -> dict:
    material = review_material(manifest, review)
    if material and not needed:
        raise MnemoError("материальная сверка требует сообщения аудитории")
    if needed and form not in ("confirmation", "clarification"):
        raise MnemoError("нужен --form confirmation|clarification")
    if needed and not (text or "").strip():
        raise MnemoError("для сообщения нужен непустой --text-file")
    if not needed and is_vague(reason):
        raise MnemoError("--not-needed требует содержательную причину")
    if needed and not (includes_records or includes_impacts):
        raise MnemoError("сообщение должно включать изменения или impacts этой сверки")
    change_records = {c.get("record") for c in review.get("changes", [])}
    impact_numbers = {str(i.get("n")) for i in review.get("impacts", [])}
    bad_records = [value for value in includes_records if value not in change_records]
    bad_impacts = [value for value in includes_impacts if value not in impact_numbers]
    if bad_records or bad_impacts:
        raise MnemoError("includes выходит за пределы сверки: "
                         + ", ".join(bad_records + bad_impacts))
    existing = list(review.get("feedback") or [])
    prior = [f for f in existing if f.get("audience") == audience]
    if prior and supersedes_n is None:
        raise MnemoError("обратная связь уже существует; новая редакция требует --supersedes-n")
    if supersedes_n is not None:
        old = next((f for f in prior if f.get("n") == supersedes_n), None)
        if old is None:
            raise MnemoError(f"нет редакции feedback n={supersedes_n} для {audience}")
        if any(f.get("supersedes_n") == supersedes_n for f in prior):
            raise MnemoError(f"редакция feedback n={supersedes_n} уже заменена")
    selected = None
    if selected_question:
        question = next((q for q in manifest.get("questions", [])
                         if q.get("id") == selected_question), None)
        audience_record = next((a for a in manifest.get("export", {}).get("audiences", [])
                                if a.get("name") == audience), None)
        if question is None or selected_question not in change_records:
            raise MnemoError("selected_question должен быть вопросом из changes этой сверки")
        if question_state(question) not in ("open", "raised"):
            raise MnemoError("selected_question должен быть открытым или уже поднятым вопросом")
        if selected_question not in includes_records:
            raise MnemoError("selected_question должен входить в includes.records")
        if question.get("asked_of") not in (audience_record or {}).get("people", []):
            raise MnemoError("asked_of выбранного вопроса не входит в audience.people")
        if question.get("raised") and not resend:
            raise MnemoError("вопрос уже задавали; повтор требует --resend и --reason")
        if question.get("raised") and is_vague(reason):
            raise MnemoError("повтор вопроса требует содержательный --reason")
        selected = {"id": selected_question, "reason": reason, "resend": resend}
    number = max([int(f.get("n", 0)) for f in existing] or [0]) + 1
    feedback = {
        "n": number, "audience": audience, "needed": needed, "reason": reason,
        "form": form if needed else None, "rendered_text": text if needed else None,
        "style": "brief" if needed else None,
        "includes": {"records": includes_records, "impacts": includes_impacts},
        "selected_question": selected, "supersedes_n": supersedes_n,
        "date": today(), "deliveries": [],
    }
    feedback["content_sha256"] = feedback_content_sha256(feedback)
    review.setdefault("feedback", []).append(feedback)
    return feedback


def deliver_feedback(manifest: dict, review: dict, number: int, *, where: str,
                     ref: str, resend: bool, reason: str | None) -> tuple[dict, bool]:
    validate_feedback_contract(manifest, review)
    feedback = next((f for f in review.get("feedback", []) if f.get("n") == number), None)
    if feedback is None:
        raise MnemoError(f"нет feedback n={number} в {review['id']}")
    newer = [f for f in review.get("feedback", [])
             if f.get("audience") == feedback.get("audience")
             and f.get("supersedes_n") == number]
    if newer:
        raise MnemoError("доставлять можно только активную редакцию feedback")
    key = (where, ref)
    deliveries = feedback.setdefault("deliveries", [])
    if any((d.get("where"), d.get("ref")) == key for d in deliveries):
        return feedback, False
    if deliveries and not resend:
        shown = "; ".join(
            f"{delivery.get('date')} {delivery.get('where')} {delivery.get('ref')}"
            for delivery in deliveries
        )
        raise MnemoError(f"другая доставка уже записана: {shown}; "
                         "повтор требует --resend")
    if deliveries and is_vague(reason):
        raise MnemoError("повторная доставка требует содержательный --reason")
    delivery = {"date": today(), "where": where, "ref": ref,
                "reason": reason if resend else None}

    selected = feedback.get("selected_question") or {}
    question_id = selected.get("id") if isinstance(selected, dict) else selected
    if question_id:
        question = next((q for q in manifest.get("questions", [])
                         if q.get("id") == question_id), None)
        if question is None:
            raise MnemoError(f"нет выбранного вопроса {question_id}")
        asked_of = question.get("asked_of")
        if not asked_of:
            raise MnemoError(f"у {question_id} не указан asked_of")
        previous = [mark for mark in question.get("raised", [])
                    if mark.get("to") == asked_of]
        if previous and not resend:
            shown = "; ".join(f"{m.get('at')} {m.get('where', '')}" for m in previous)
            raise MnemoError(f"{question_id} уже задавали {asked_of}: {shown}; "
                             "повтор требует --resend и --reason")
        if previous and is_vague(reason):
            raise MnemoError("повтор вопроса требует содержательный --reason")
        mark = {"to": asked_of, "at": delivery["date"],
                "where": f"{where} {ref}".strip()}
        if mark not in question.setdefault("raised", []):
            question["raised"].append(mark)
    deliveries.append(delivery)
    return feedback, True


def review_contract(manifest: dict, review: dict) -> dict:
    material = review_material(manifest, review)
    audiences: dict[str, dict] = {}
    for audience in manifest.get("export", {}).get("audiences", []):
        current_number = review_number(review.get("id"))
        baseline_number = review_number(audience.get("since_s"), 1)
        if current_number < baseline_number:
            audiences[audience["name"]] = {
                "status": "out_of_scope", "feedback_n": None,
                "reason": None, "deliveries": [],
            }
            continue
        feedbacks = [f for f in review.get("feedback", [])
                     if f.get("audience") == audience.get("name")]
        superseded = {f.get("supersedes_n") for f in feedbacks if f.get("supersedes_n")}
        active = next((f for f in reversed(feedbacks) if f.get("n") not in superseded), None)
        if not material:
            status = "not_needed" if active and not active.get("needed") else "nonmaterial"
        elif active and active.get("deliveries"):
            status = "delivered"
        elif active:
            status = "pending"
        else:
            status = "pending"
        audiences[audience["name"]] = {
            "status": status, "feedback_n": active.get("n") if active else None,
            "reason": active.get("reason") if active else None,
            "deliveries": list(active.get("deliveries") or []) if active else [],
        }
    superseded = {f.get("supersedes_n") for f in review.get("feedback", [])
                  if f.get("supersedes_n")}
    active_numbers = [f.get("n") for f in review.get("feedback", [])
                      if f.get("n") not in superseded]
    return {**review, "material": material, "audiences": audiences,
            "active_feedback": active_numbers}


def packages_contract(manifest: dict) -> list[dict]:
    retired = {r.get("id") for r in manifest.get("retired", [])}
    reviews = manifest.get("reviews", [])
    result = []
    for package in [p for p in manifest.get("imports", []) if p.get("id")]:
        live_items = [item_id for item_id in package.get("items") or [] if item_id not in retired]
        related = [review for review in reviews if package["id"] in review.get("scope", [])]
        covered_items: set[str] = set()
        for review in related:
            for change in review.get("changes", []):
                covered_items.update(change.get("source_items") or [])
            for group in review.get("nonmaterial", []):
                covered_items.update(group.get("items") or [])
        uncovered = [item_id for item_id in live_items if item_id not in covered_items]
        result.append({**package, "covered": not uncovered,
                       "uncovered_items": uncovered,
                       "reviews": [review["id"] for review in related]})
    return result


def sync_contract(manifest: dict, packages: list[dict], reviews: list[dict]) -> dict:
    packaged = {item_id for package in packages for item_id in package.get("items") or []}
    retired = {r.get("id") for r in manifest.get("retired", [])}
    pending_for = {
        audience.get("name"): [review["id"] for review in reviews
                               if review.get("audiences", {}).get(audience.get("name"), {})
                               .get("status") == "pending"]
        for audience in manifest.get("export", {}).get("audiences", [])
    }
    reviewed_pairs = {(change.get("record"), item_id)
                      for review in manifest.get("reviews", [])
                      for change in review.get("changes", [])
                      for item_id in change.get("source_items") or []}
    unreviewed_changes = []
    for bucket in ("requirements", "questions", "decisions", "facts"):
        for record in manifest.get(bucket, []):
            refs = list(record.get("based_on") or [])
            if record.get("answered_by"):
                refs.append(record["answered_by"])
            for ref in refs:
                item_id = str(ref).rsplit("#", 1)[-1]
                if item_id in packaged and (record.get("id"), item_id) not in reviewed_pairs:
                    unreviewed_changes.append({"record": record.get("id"), "item": item_id})
    return {
        "uncovered_packages": [p["id"] for p in packages if not p["covered"]],
        "unreviewed_records": sorted({row["record"] for row in unreviewed_changes}),
        "unreviewed_changes": unreviewed_changes,
        "pending_for": pending_for,
        "material_before_packages": len([
            item for item in manifest.get("items", [])
            if item.get("id") not in packaged and item.get("id") not in retired
        ]),
    }


def packaged_refs(manifest: dict, refs: list[str]) -> list[tuple[str, str]]:
    """Вернуть пары (item, package) для ссылок на материал новых пакетов."""
    item_to_package = {item_id: package.get("id")
                       for package in manifest.get("imports", []) if package.get("id")
                       for item_id in package.get("items") or []}
    found: list[tuple[str, str]] = []
    for ref in refs:
        item_id = ref.rsplit("#", 1)[-1] if ref.startswith("ctx:") else ref
        if item_id in item_to_package:
            found.append((item_id, item_to_package[item_id]))
    return found


def guard_packaged_lifecycle(manifest: dict, refs: list[str]) -> None:
    found = packaged_refs(manifest, [ref for ref in refs if ref])
    if not found:
        return
    item_id, package_id = found[0]
    raise MnemoError(
        f"{item_id} — материал пакета {package_id}; включи изменение в план "
        f"review --scope {package_id}"
    )
