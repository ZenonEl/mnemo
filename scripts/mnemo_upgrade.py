"""Планируемое обновление legacy-экспорта до текущего стандарта."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from mnemo_core import SPEC_VERSION, MnemoError, next_id, parse_day, today
from mnemo_reconcile import (
    is_vague, packages_contract, review_contract, sync_contract,
    review_change_problem, validate_review_change,
)


KNOWN_UNHANDLED = [
    "разъехавшиеся имена реестра (V14) требуют решения человека",
    "содержимое полей 1.16/1.17 не восстанавливается без источника",
]


def _fingerprint(change: dict) -> str:
    payload = {key: value for key, value in change.items() if key != "fingerprint"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _candidate(kind: str, target: str, before: Any, after: Any, reason: str,
               **extra: Any) -> dict:
    change = {"kind": kind, "target": target, "before": before, "after": after,
              "reason": reason, **extra}
    change["fingerprint"] = _fingerprint(change)
    return change


def applied_fingerprints(manifest: dict) -> set[str]:
    return {str(change.get("fingerprint"))
            for upgrade in manifest.get("upgrades", [])
            for change in upgrade.get("changes", []) if change.get("fingerprint")}


def build_upgrade_plan(manifest: dict, base_manifest_sha256: str) -> dict:
    candidates: list[dict] = []
    unhandled = list(KNOWN_UNHANDLED)
    for review in manifest.get("reviews", []):
        review_id = str(review.get("id"))
        for index, change in enumerate(review.get("changes", [])):
            problem = review_change_problem(manifest, change)
            if problem is None:
                continue
            problem_code, problem_message = problem
            action = change.get("action")
            delta = change.get("delta") or []
            target = f"{review_id}.changes[{index}]"
            if problem_code == "noop":
                fully_noop = delta and all(
                    row.get("before") == row.get("after") for row in delta
                )
                if fully_noop and action in (
                        "updated", "answered", "unanswered", "confirmed"):
                    candidates.append(_candidate(
                        "drop-noop-review-change", target, change, None,
                        problem_message, review=review_id, index=index,
                        source_items=list(change.get("source_items") or []),
                    ))
                else:
                    unhandled.append(f"V24 {target}: {problem_message}")
                continue

            replacement = {
                "updated-should-be-answered": "answered",
                "updated-should-be-unanswered": "unanswered",
                "updated-should-be-confirmed": "confirmed",
            }.get(problem_code)
            if replacement is None and problem_code in (
                    "answered-mismatch", "confirmed-mismatch"):
                expected_field = (
                    "answered_by" if problem_code == "answered-mismatch" else "based_on"
                )
                semantic = next((row for row in delta
                                 if row.get("field") == expected_field), None)
                if semantic is not None and not semantic.get("after"):
                    replacement = (
                        "unanswered" if expected_field == "answered_by"
                        and semantic.get("before") else "updated"
                    )
            if replacement is not None:
                candidates.append(_candidate(
                    "reclassify-review-action", target, action, replacement,
                    problem_message, review=review_id, index=index,
                ))
            else:
                unhandled.append(f"V24 {target}: {problem_message}")

    for index, imported in enumerate(manifest.get("imports", [])):
        if imported.get("id") or "items" in imported:
            continue
        summary = {key: imported.get(key) for key in ("imported", "messages", "parser")}
        candidates.append(_candidate(
            "preserve-legacy-import", f"imports[{index}]", summary,
            {"classification": "legacy", "items_invented": False},
            "точный состав старого внесения неизвестен", index=index,
        ))

    packages = packages_contract(manifest)
    reviews = [review_contract(manifest, row) for row in manifest.get("reviews", [])]
    sync = sync_contract(manifest, packages, reviews)
    for row in sync.get("unreviewed_changes", []):
        record, item = str(row.get("record")), str(row.get("item"))
        candidates.append(_candidate(
            "acknowledge-unreviewed-change", f"{record}@{item}",
            {"reviewed": False}, {"classification": "legacy", "reviewed": True},
            "запись создана вне пакетной сверки", record=record, item=item,
        ))

    applied = applied_fingerprints(manifest)
    pending = [change for change in candidates if change["fingerprint"] not in applied]
    return {
        "base_manifest_sha256": base_manifest_sha256,
        "from_spec": str(manifest.get("mnemo_spec", "0")),
        "to_spec": SPEC_VERSION,
        "changes": pending,
        "known_unhandled": unhandled,
    }


def apply_upgrade(manifest: dict, plan: dict, by: str, reason: str) -> dict | None:
    if not isinstance(by, str) or not by.strip() or is_vague(reason):
        raise MnemoError("upgrade требует непустой --by и содержательный --reason")
    changes = list(plan.get("changes") or [])
    if not changes:
        return None
    upgrade_id = next_id(manifest, "upgrade")

    for change in (row for row in changes if row.get("kind") != "drop-noop-review-change"):
        kind = change.get("kind")
        if kind == "reclassify-review-action":
            review = next((row for row in manifest.get("reviews", [])
                           if row.get("id") == change.get("review")), None)
            index = change.get("index")
            if review is None or not isinstance(index, int) or not 0 <= index < len(review.get("changes", [])):
                raise MnemoError(f"upgrade target исчез: {change.get('target')}")
            current = review["changes"][index]
            if current.get("action") != change.get("before"):
                raise MnemoError(f"upgrade target изменился: {change.get('target')}")
            current["action"] = change["after"]
        elif kind not in ("preserve-legacy-import", "acknowledge-unreviewed-change"):
            raise MnemoError(f"неизвестная операция upgrade: {kind!r}")

    # Индексы записаны в плане на одном снимке. Удаляем справа налево, иначе
    # первый pop сдвинет последующие цели и план перестанет быть воспроизводимым.
    drops = sorted(
        (row for row in changes if row.get("kind") == "drop-noop-review-change"),
        key=lambda row: (str(row.get("review")), int(row.get("index", -1))),
        reverse=True,
    )
    for change in drops:
        review = next((row for row in manifest.get("reviews", [])
                       if row.get("id") == change.get("review")), None)
        index = change.get("index")
        if review is None or not isinstance(index, int) or not 0 <= index < len(review.get("changes", [])):
            raise MnemoError(f"upgrade target исчез: {change.get('target')}")
        if review["changes"][index] != change.get("before"):
            raise MnemoError(f"upgrade target изменился: {change.get('target')}")
        removed = review["changes"].pop(index)
        covered = {item for row in review.get("changes", [])
                   for item in row.get("source_items") or []}
        covered.update(item for group in review.get("nonmaterial", [])
                       for item in group.get("items") or [])
        missing = [item for item in removed.get("source_items") or [] if item not in covered]
        if missing:
            review.setdefault("nonmaterial", []).append({
                "items": missing,
                "reason": f"legacy no-op снят обновлением {upgrade_id}",
            })

    touched = {str(row.get("review")) for row in changes if row.get("review")}
    for review in manifest.get("reviews", []):
        if review.get("id") in touched:
            for change in review.get("changes", []):
                validate_review_change(manifest, change)

    record = {
        "id": upgrade_id, "date": today(), "by": str(by).strip(),
        "reason": str(reason).strip(), "from_spec": plan.get("from_spec"),
        "to_spec": plan.get("to_spec"),
        "base_manifest_sha256": plan.get("base_manifest_sha256"),
        "changes": changes,
    }
    manifest.setdefault("upgrades", []).append(record)
    return record


def validate_upgrades(manifest: dict) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for upgrade in manifest.get("upgrades", []):
        uid = str(upgrade.get("id") or "?")
        if uid in seen_ids or not uid.startswith("u") or not uid[1:].isdigit():
            errors.append((uid, "id upgrade неверен или повторён"))
        seen_ids.add(uid)
        try:
            parse_day(str(upgrade.get("date") or ""))
        except MnemoError as exc:
            errors.append((uid, str(exc)))
        if not str(upgrade.get("by") or "").strip() or is_vague(upgrade.get("reason")):
            errors.append((uid, "upgrade без автора или содержательной причины"))
        if not upgrade.get("changes"):
            errors.append((uid, "upgrade без changes"))
        if not re.fullmatch(r"[0-9a-f]{64}", str(upgrade.get("base_manifest_sha256") or "")):
            errors.append((uid, "upgrade без base_manifest_sha256"))
        if not re.fullmatch(r"\d+\.\d+", str(upgrade.get("from_spec") or "")) \
                or not re.fullmatch(r"\d+\.\d+", str(upgrade.get("to_spec") or "")):
            errors.append((uid, "upgrade без корректных from_spec/to_spec"))
        for change in upgrade.get("changes", []):
            fingerprint = change.get("fingerprint")
            if fingerprint != _fingerprint(change):
                errors.append((uid, "fingerprint upgrade change не совпадает"))
            if fingerprint in seen_fingerprints:
                errors.append((uid, "upgrade change применён повторно"))
            seen_fingerprints.add(str(fingerprint))
            if not all(key in change for key in ("kind", "target", "before", "after")):
                errors.append((uid, "upgrade change без kind/target/before/after"))
    return errors
