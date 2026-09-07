"""Сверка нового пакета с действующим состоянием проекта.

Тесты идут через CLI там, где человек действительно вызывает команду. Формат
манифеста проверяется рядом: иначе зелёный текст команды мог бы скрыть, что
состояние записано не тем адресатом или без состава пакета.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run(script: str, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=input_text, text=True, capture_output=True,
    )


class ReconcileCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.export = self.tmp / "export"
        done = run(
            "mnemo_manifest.py", "init", "--dir", str(self.export),
            "--slug", "priyomka", "--title", "Приёмка проекта",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.man("people", "--add", "--display", "Оператор", "--id", "operator",
                 "--role", "self")
        self.man("people", "--add", "--display", "Пётр Иванов",
                 "--id", "petr-ivanov", "--role", "management")

    def man(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        return run("mnemo_manifest.py", *args, "--export", str(self.export), input_text=input_text)

    def manifest(self) -> dict:
        return json.loads((self.export / "MANIFEST.json").read_text(encoding="utf-8"))

    def manifest_hash(self) -> str:
        return hashlib.sha256((self.export / "MANIFEST.json").read_bytes()).hexdigest()

    def add_text(self, label: str, body: str = "Нужна выгрузка раз в час") -> tuple[str, str]:
        done = self.man(
            "add-text", "--author", "Пётр Иванов", "--label", label,
            "--source", "other", "--fidelity", "verbatim",
            input_text=body,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("пакет: p", done.stdout)
        self.assertIn("reconcile/review", done.stdout)
        manifest = self.manifest()
        return manifest["items"][-1]["id"], manifest["imports"][-1]["id"]

    def add_audience(self) -> None:
        done = self.man(
            "audiences", "--add", "--name", "руководство", "--kind", "business",
            "--people", "Пётр Иванов",
        )
        self.assertEqual(done.returncode, 0, done.stderr)

    def plan(self, *, scope: list[str], creates: list[dict] | None = None,
             mutations: list[dict] | None = None, nonmaterial: list[dict] | None = None,
             impacts: list[dict] | None = None) -> Path:
        path = self.tmp / "review-plan.json"
        path.write_text(json.dumps({
            "base_manifest_sha256": self.manifest_hash(),
            "by": "operator",
            "scope": scope,
            "creates": creates or [],
            "mutations": mutations or [],
            "nonmaterial": nonmaterial or [],
            "impacts": impacts or [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def apply_plan(self, path: Path) -> subprocess.CompletedProcess:
        return self.man("review", "--plan-file", str(path), "--apply")


class PackageIngest(ReconcileCase):
    def test_add_text_creates_exact_package_membership(self) -> None:
        item_id, package_id = self.add_text("one")
        package = self.manifest()["imports"][-1]
        self.assertEqual(package_id, "p001")
        self.assertEqual(package["kind"], "add-text")
        self.assertEqual(package["items"], [item_id])
        self.assertNotIn("messages", package)

    def test_add_file_creates_package_for_the_created_item(self) -> None:
        source = self.tmp / "brief.txt"
        source.write_text("Условия проекта\n", encoding="utf-8")
        done = self.man(
            "add-file", "--kind", "attachment", "--file", str(source),
            "--source", "other", "--fidelity", "verbatim",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest["imports"][-1]["kind"], "add-file")
        self.assertEqual(manifest["imports"][-1]["items"], [manifest["items"][-1]["id"]])

    def test_import_creates_one_exact_package_and_zero_repeat_creates_none(self) -> None:
        source = self.tmp / "chat.txt"
        source.write_text("[01.09.2026 10:00] Пётр Иванов: Согласован порядок\n",
                          encoding="utf-8")
        first = run("mnemo_import.py", "--export", str(self.export),
                    "--source", str(source), "--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("пакет: p001", first.stdout)
        self.assertIn("reconcile/review", first.stdout)
        manifest = self.manifest()
        package = manifest["imports"][-1]
        self.assertEqual(package["kind"], "import")
        self.assertEqual(package["items"], [item["id"] for item in manifest["items"]])
        count = len(manifest["imports"])
        second = run("mnemo_import.py", "--export", str(self.export),
                     "--source", str(source), "--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.manifest()["imports"]), count)

    def test_legacy_import_is_not_given_an_invented_scope(self) -> None:
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["imports"].append({
            "parser": "legacy", "source": "archive", "imported": "2026-09-01",
            "messages": 1, "keys": ["legacy-key"],
        })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        plan = self.plan(scope=["p001"], nonmaterial=[])
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 1)
        self.assertIn("неизвестные пакеты", done.stderr)
        self.assertNotIn("id", self.manifest()["imports"][0])

    def test_new_package_raises_manifest_version(self) -> None:
        self.add_text("version")
        self.assertEqual(self.manifest()["mnemo_spec"], "1.17")


class AudienceBaseline(ReconcileCase):
    def test_empty_audience_list_is_explicit(self) -> None:
        shown = self.man("audiences")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("аудиторий нет", shown.stdout)

    def test_name_is_resolved_but_manifest_keeps_person_id(self) -> None:
        self.add_audience()
        audience = self.manifest()["export"]["audiences"][0]
        self.assertEqual(audience, {
            "name": "руководство", "kind": "business",
            "people": ["petr-ivanov"], "since_s": "s001",
        })

    def test_unknown_person_is_refused_instead_of_stored_as_free_text(self) -> None:
        done = self.man(
            "audiences", "--add", "--name", "руководство", "--kind", "business",
            "--people", "Неизвестный",
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("реестр", done.stderr)

    def test_unknown_person_can_be_confirmed_in_same_command(self) -> None:
        done = self.man(
            "audiences", "--add", "--name", "отдел", "--kind", "business",
            "--people", "Новый Участник", "--confirm-create-people",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = self.manifest()
        created = next(p for p in manifest["people"] if p["display"] == "Новый Участник")
        self.assertEqual(created["role"], "management")
        self.assertEqual(manifest["export"]["audiences"][0]["people"], [created["id"]])

    def test_new_audience_does_not_backfill_previous_review(self) -> None:
        item_id, package_id = self.add_text("before-audience")
        done = self.apply_plan(self.plan(
            scope=[package_id], creates=[{
                "kind": "requirement", "source_items": [item_id],
                "record": {"quote": "Нужен отчёт", "wanted_by": "petr-ivanov",
                           "based_on": [f"ctx:priyomka#{item_id}"], "state": "stated"},
            }],
        ))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.add_audience()
        manifest = self.manifest()
        self.assertEqual(manifest["export"]["audiences"][0]["since_s"], "s002")
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertEqual(data["reviews"][0]["audiences"]["руководство"]["status"],
                         "out_of_scope")
        self.assertEqual(data["sync"]["pending_for"]["руководство"], [])

    def test_audience_raises_version_and_refuses_unknown_backfill(self) -> None:
        failed = self.man(
            "audiences", "--add", "--name", "руководство", "--kind", "business",
            "--people", "Пётр Иванов", "--backfill-from", "s009",
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("нет сверки s009", failed.stderr)
        self.add_audience()
        self.assertEqual(self.manifest()["mnemo_spec"], "1.17")


class FrozenReview(ReconcileCase):
    def requirement_create(self, item_id: str) -> dict:
        return {
            "kind": "requirement",
            "source_items": [item_id],
            "record": {
                "quote": "Выгрузка должна обновляться раз в час",
                "wanted_by": "petr-ivanov",
                "based_on": [f"ctx:priyomka#{item_id}"],
                "state": "stated",
            },
        }

    def test_review_creates_record_and_frozen_change_atomically(self) -> None:
        item_id, package_id = self.add_text("review")
        plan = self.plan(scope=[package_id], creates=[self.requirement_create(item_id)])
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = self.manifest()
        self.assertEqual([r["id"] for r in manifest["requirements"]], ["t001"])
        review = manifest["reviews"][0]
        self.assertEqual(review["scope"], [package_id])
        self.assertEqual(review["changes"][0]["source_items"], [item_id])
        self.assertEqual(review["changes"][0]["delta"], [
            {"field": "id", "before": None, "after": "t001"},
        ])

    def test_dry_run_validates_and_does_not_write(self) -> None:
        item_id, package_id = self.add_text("dry-run")
        before = self.manifest_hash()
        shown = self.man("review", "--plan-file", str(self.plan(
            scope=[package_id], creates=[self.requirement_create(item_id)]
        )))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        preview = json.loads(shown.stdout.split("\n\n—", 1)[0])
        self.assertTrue(preview["validated"])
        self.assertEqual(preview["coverage"], {"covered": 1, "total": 1})
        self.assertEqual(preview["suggested_feedback_form"], "confirmation")
        self.assertEqual(self.manifest_hash(), before)
        self.assertEqual(self.manifest()["reviews"], [])

    def test_dry_run_rejects_invalid_plan_without_writing(self) -> None:
        _, package_id = self.add_text("bad-dry-run")
        before = self.manifest_hash()
        shown = self.man("review", "--plan-file", str(self.plan(scope=[package_id])))
        self.assertEqual(shown.returncode, 1)
        self.assertIn("не разобраны", shown.stderr)
        self.assertEqual(self.manifest_hash(), before)

    def test_incomplete_coverage_refuses_everything(self) -> None:
        first, package_one = self.add_text("first")
        second, package_two = self.add_text("second", "Встреча перенесена")
        plan = self.plan(
            scope=[package_one, package_two],
            creates=[self.requirement_create(first)],
        )
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 1)
        self.assertIn(second, done.stderr)
        self.assertEqual(self.manifest().get("reviews", []), [])
        self.assertEqual(self.manifest()["requirements"], [])

    def test_item_cannot_be_change_and_nonmaterial_at_once(self) -> None:
        item_id, package_id = self.add_text("overlap")
        plan = self.plan(
            scope=[package_id], creates=[self.requirement_create(item_id)],
            nonmaterial=[{"items": [item_id], "reason": "повторяет действующее требование"}],
        )
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 1)
        self.assertIn("одновременно", done.stderr)

    def test_retired_item_is_skipped_by_review_and_verifier(self) -> None:
        item_id, package_id = self.add_text("retired")
        removed = self.man("remove", "--id", item_id, "--reason", "дубликат материала",
                           "--confirm")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        done = self.apply_plan(self.plan(scope=[package_id]))
        self.assertEqual(done.returncode, 0, done.stderr)
        checked = run("mnemo_verify.py", "--export", str(self.export))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_stale_plan_refuses_before_creating_any_record(self) -> None:
        item_id, package_id = self.add_text("stale")
        plan = self.plan(scope=[package_id], creates=[self.requirement_create(item_id)])
        self.add_text("later", "Появилось новое сообщение")
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 1)
        self.assertIn("состояние изменилось после плана", done.stderr)
        self.assertEqual(self.manifest().get("reviews", []), [])
        self.assertEqual(self.manifest()["requirements"], [])

    def test_impact_alone_makes_review_material(self) -> None:
        created = self.man(
            "req", "--quote", "Срок запуска согласуется отдельно",
            "--wanted-by", "petr-ivanov",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        item_id, package_id = self.add_text("impact", "Изменился срок")
        plan = self.plan(
            scope=[package_id],
            nonmaterial=[{"items": [item_id], "reason": "само сообщение не создаёт записи"}],
            impacts=[{
                "n": 1, "kind": "deadline", "text": "Срок запуска изменился",
                "based_on": ["t001"], "affects": [], "area": None,
            }],
        )
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        audit = run("mnemo_audit.py", "--export", str(self.export), "--json")
        self.assertEqual(audit.returncode, 0, audit.stderr)
        self.assertTrue(json.loads(audit.stdout)["reviews"][0]["material"])

    def test_repeated_package_review_does_not_inherit_materiality(self) -> None:
        item_id, package_id = self.add_text("repeat-materiality")
        first = self.apply_plan(self.plan(
            scope=[package_id], creates=[self.requirement_create(item_id)]
        ))
        self.assertEqual(first.returncode, 0, first.stderr)
        second_plan = self.plan(
            scope=[package_id],
            nonmaterial=[{"items": [item_id], "reason": "повтор уже принятого требования"}],
        )
        previewed = self.man("review", "--plan-file", str(second_plan))
        self.assertEqual(previewed.returncode, 0, previewed.stderr)
        preview = json.loads(previewed.stdout.split("\n\n—", 1)[0])
        self.assertIn("уже разбирались", preview["warnings"][0])
        second = self.apply_plan(second_plan)
        self.assertEqual(second.returncode, 0, second.stderr)
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertTrue(data["reviews"][0]["material"])
        self.assertFalse(data["reviews"][1]["material"])

    def test_impact_rejects_vague_text_kind_and_unknown_links(self) -> None:
        created = self.man("req", "--quote", "Срок запуска согласуется отдельно",
                           "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        item_id, package_id = self.add_text("bad-impact")
        base = [{"items": [item_id], "reason": "сообщение учтено как последствие"}]
        cases = [
            {"n": 1, "kind": "deadline", "text": "надо", "based_on": ["t001"]},
            {"n": 1, "kind": "bogus", "text": "Срок запуска изменился", "based_on": ["t001"]},
            {"n": 1, "kind": "deadline", "text": "Срок запуска изменился", "based_on": ["t999"]},
        ]
        for impact in cases:
            with self.subTest(impact=impact):
                failed = self.apply_plan(self.plan(
                    scope=[package_id], nonmaterial=base, impacts=[impact]
                ))
                self.assertEqual(failed.returncode, 1)
                self.assertEqual(self.manifest()["reviews"], [])

    def test_change_requires_source_items_even_when_nonmaterial_covers_scope(self) -> None:
        item_id, package_id = self.add_text("empty-source")
        create = self.requirement_create(item_id)
        create["source_items"] = []
        failed = self.apply_plan(self.plan(
            scope=[package_id], creates=[create],
            nonmaterial=[{"items": [item_id], "reason": "материал отдельно покрыт"}],
        ))
        self.assertEqual(failed.returncode, 1)
        self.assertIn("source_items", failed.stderr)

    def test_same_field_cannot_be_mutated_twice_in_one_review(self) -> None:
        created = self.man("req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        item_id, package_id = self.add_text("double-mutation")
        mutations = [
            {"record": "t001", "field": "state", "value": value,
             "source_items": [item_id]}
            for value in ("accepted", "stated")
        ]
        failed = self.apply_plan(self.plan(scope=[package_id], mutations=mutations))
        self.assertEqual(failed.returncode, 1)
        self.assertIn("больше одного раза", failed.stderr)
        self.assertEqual(self.manifest()["requirements"][0]["state"], "stated")

    def test_self_question_alone_is_not_material(self) -> None:
        item_id, package_id = self.add_text("self-question", "Нужно проверить формат")
        create = {
            "kind": "question", "source_items": [item_id],
            "record": {
                "text": "Какой формат используется?", "self_attempt": "проверил описание",
                "impact": "определяет формат результата",
                "asked_of": "operator", "based_on": [f"ctx:priyomka#{item_id}"],
            },
        }
        done = self.apply_plan(self.plan(scope=[package_id], creates=[create]))
        self.assertEqual(done.returncode, 0, done.stderr)
        audit = run("mnemo_audit.py", "--export", str(self.export), "--json")
        self.assertFalse(json.loads(audit.stdout)["reviews"][0]["material"])

    def test_external_question_is_material_even_without_audiences(self) -> None:
        item_id, package_id = self.add_text("external-question", "Нужно выбрать формат")
        create = {
            "kind": "question", "source_items": [item_id],
            "record": {
                "text": "Какой формат выбрать?", "self_attempt": "проверил описание форматов",
                "impact": "определяет вид результата", "asked_of": "petr-ivanov",
                "based_on": [f"ctx:priyomka#{item_id}"],
            },
        }
        plan = self.plan(scope=[package_id], creates=[create])
        previewed = self.man("review", "--plan-file", str(plan))
        self.assertEqual(previewed.returncode, 0, previewed.stderr)
        preview = json.loads(previewed.stdout.split("\n\n—", 1)[0])
        self.assertEqual(preview["suggested_feedback_form"], "clarification")
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertTrue(data["reviews"][0]["material"])

    def test_review_cannot_bypass_question_self_attempt_gate(self) -> None:
        item_id, package_id = self.add_text("weak-question", "Какой формат выбрать?")
        create = {
            "kind": "question", "source_items": [item_id],
            "record": {"text": "Какой формат выбрать?", "impact": "меняет результат",
                       "asked_of": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{item_id}"],
                       "self_attempt": "разбирался"},
        }
        done = self.apply_plan(self.plan(scope=[package_id], creates=[create]))
        self.assertEqual(done.returncode, 1)
        self.assertIn("self_attempt", done.stderr)
        self.assertEqual(self.manifest()["questions"], [])

    def test_confirmed_based_on_change_is_not_material_by_itself(self) -> None:
        created = self.man("req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        item_id, package_id = self.add_text("confirmation", "Да, нужен тот же отчёт")
        plan = self.plan(scope=[package_id], mutations=[{
            "record": "t001", "field": "based_on",
            "value": [f"ctx:priyomka#{item_id}"], "source_items": [item_id],
        }])
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertEqual(data["reviews"][0]["changes"][0]["action"], "confirmed")
        self.assertFalse(data["reviews"][0]["material"])

    def test_impact_may_reference_record_created_in_same_review(self) -> None:
        item_id, package_id = self.add_text("impact-new-record", "Добавился новый срок")
        plan = self.plan(
            scope=[package_id], creates=[self.requirement_create(item_id)],
            impacts=[{"n": 1, "kind": "deadline", "text": "Срок изменился",
                      "based_on": ["t001"], "affects": ["t001"], "area": None}],
        )
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_packaged_lifecycle_change_requires_review(self) -> None:
        created = self.man("req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        item_id, package_id = self.add_text("answer", "Отчёт подтверждён")
        changed = self.man(
            "req", "--id", "t001", "--state", "done", "--evidence", item_id,
        )
        self.assertEqual(changed.returncode, 1)
        self.assertIn(package_id, changed.stderr)
        self.assertEqual(self.manifest()["requirements"][0]["state"], "stated")


class FeedbackDelivery(ReconcileCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_audience()
        self.item_id, self.package_id = self.add_text("question", "Какой способ оплаты выбрать?")
        create = {
            "kind": "question", "source_items": [self.item_id],
            "record": {
                "text": "Как покупатель должен оплачивать заказ?",
                "impact": "определяет сценарий оплаты",
                "blocking": "нельзя завершить сценарий оплаты",
                "self_attempt": "прочитал пакет — способ оплаты не выбран",
                "asked_of": "petr-ivanov",
                "based_on": [f"ctx:priyomka#{self.item_id}"],
            },
        }
        done = self.apply_plan(self.plan(scope=[self.package_id], creates=[create]))
        self.assertEqual(done.returncode, 0, done.stderr)

    def create_feedback(self, text: str = "Оплата не выбрана. Как покупатель должен оплачивать заказ?") -> int:
        brief = self.tmp / "brief.txt"
        brief.write_text(text, encoding="utf-8")
        done = self.man(
            "feedback", "s001", "--audience", "руководство", "--needed",
            "--form", "clarification", "--includes-records", "q001",
            "--selected-question", "q001", "--text-file", str(brief),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.manifest()["reviews"][0]["feedback"][-1]["n"]

    def test_delivery_marks_actual_person_and_is_idempotent(self) -> None:
        number = self.create_feedback()
        first = self.man(
            "deliver", "s001", "--feedback", str(number),
            "--where", "staging", "--ref", "msg/4471",
        )
        second = self.man(
            "deliver", "s001", "--feedback", str(number),
            "--where", "staging", "--ref", "msg/4471",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        manifest = self.manifest()
        feedback = manifest["reviews"][0]["feedback"][0]
        question = manifest["questions"][0]
        self.assertEqual(len(feedback["deliveries"]), 1)
        self.assertEqual(feedback["rendered_text"],
                         "Оплата не выбрана. Как покупатель должен оплачивать заказ?")
        self.assertEqual(feedback["style"], "brief")
        self.assertEqual(question["raised"], [{
            "to": "petr-ivanov", "at": feedback["deliveries"][0]["date"],
            "where": "staging msg/4471",
        }])

    def test_new_feedback_revision_is_pending_again(self) -> None:
        first_number = self.create_feedback()
        sent = self.man(
            "deliver", "s001", "--feedback", str(first_number),
            "--where", "staging", "--ref", "msg/4471",
        )
        self.assertEqual(sent.returncode, 0, sent.stderr)
        brief = self.tmp / "revised.txt"
        brief.write_text("Оплата не выбрана. Нужен способ оплаты.", encoding="utf-8")
        revised = self.man(
            "feedback", "s001", "--audience", "руководство", "--needed",
            "--form", "clarification", "--includes-records", "q001",
            "--selected-question", "q001", "--resend",
            "--reason", "уточнена формулировка вопроса", "--text-file", str(brief),
            "--supersedes-n", str(first_number),
        )
        self.assertEqual(revised.returncode, 0, revised.stderr)
        audit = run("mnemo_audit.py", "--export", str(self.export), "--json")
        data = json.loads(audit.stdout)
        self.assertEqual(data["reviews"][0]["audiences"]["руководство"]["status"], "pending")

    def test_different_delivery_ref_requires_explicit_resend(self) -> None:
        number = self.create_feedback()
        self.man("deliver", "s001", "--feedback", str(number),
                 "--where", "staging", "--ref", "msg/4471")
        again = self.man(
            "deliver", "s001", "--feedback", str(number),
            "--where", "staging", "--ref", "msg/4472",
        )
        self.assertEqual(again.returncode, 1)
        self.assertIn("--resend", again.stderr)

    def test_resend_keeps_its_reason_in_delivery_history(self) -> None:
        number = self.create_feedback()
        self.man("deliver", "s001", "--feedback", str(number),
                 "--where", "staging", "--ref", "msg/4471")
        resent = self.man(
            "deliver", "s001", "--feedback", str(number),
            "--where", "staging", "--ref", "msg/4472", "--resend",
            "--reason", "первая доставка не дошла до адресата",
        )
        self.assertEqual(resent.returncode, 0, resent.stderr)
        delivery = self.manifest()["reviews"][0]["feedback"][0]["deliveries"][-1]
        self.assertEqual(delivery["reason"], "первая доставка не дошла до адресата")

    def test_superseded_feedback_cannot_be_delivered(self) -> None:
        first_number = self.create_feedback()
        brief = self.tmp / "new.txt"
        brief.write_text("Нужно выбрать способ оплаты.", encoding="utf-8")
        revised = self.man(
            "feedback", "s001", "--audience", "руководство", "--needed",
            "--form", "clarification", "--includes-records", "q001",
            "--selected-question", "q001", "--text-file", str(brief),
            "--supersedes-n", str(first_number), "--resend",
        )
        self.assertEqual(revised.returncode, 0, revised.stderr)
        old = self.man(
            "deliver", "s001", "--feedback", str(first_number),
            "--where", "staging", "--ref", "msg/4471",
        )
        self.assertEqual(old.returncode, 1)
        self.assertIn("активную", old.stderr)

    def test_missing_feedback_file_is_a_concise_cli_error(self) -> None:
        missing = self.tmp / "missing.txt"
        failed = self.man(
            "feedback", "s001", "--audience", "руководство", "--needed",
            "--form", "clarification", "--includes-records", "q001",
            "--selected-question", "q001", "--text-file", str(missing),
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("не удалось прочитать текст обратной связи", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

    def test_verifier_detects_edited_feedback_content(self) -> None:
        self.create_feedback()
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["reviews"][0]["feedback"][0]["rendered_text"] = "Подменённый текст"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        checked = run("mnemo_verify.py", "--export", str(self.export))
        self.assertEqual(checked.returncode, 1)
        self.assertIn("содержимое изменено", checked.stdout)


class NonmaterialFeedback(ReconcileCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_audience()
        item_id, package_id = self.add_text("noise", "Обсудили время встречи")
        done = self.apply_plan(self.plan(
            scope=[package_id], nonmaterial=[{
                "items": [item_id], "reason": "обсуждение не меняет состояние проекта",
            }],
        ))
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_not_needed_requires_substantive_reason(self) -> None:
        for args in ((), ("--reason", "не")):
            with self.subTest(args=args):
                failed = self.man(
                    "feedback", "s001", "--audience", "руководство", "--not-needed",
                    *args,
                )
                self.assertEqual(failed.returncode, 1)
                self.assertIn("содержательную причину", failed.stderr)
        saved = self.man(
            "feedback", "s001", "--audience", "руководство", "--not-needed",
            "--reason", "пакет не меняет договорённости и не требует решения",
        )
        self.assertEqual(saved.returncode, 0, saved.stderr)


class DecisionsAndFacts(ReconcileCase):
    def test_review_creates_addressable_decision_and_verified_fact(self) -> None:
        first, package_one = self.add_text("decision", "Используем один формат")
        second, package_two = self.add_text("fact", "Проверка завершилась успешно")
        plan = self.plan(scope=[package_one, package_two], creates=[
            {
                "kind": "decision", "source_items": [first],
                "record": {
                    "text": "Использовать единый формат", "decided_by": "operator",
                    "reason": "так результат проверяется одинаково",
                    "based_on": [f"ctx:priyomka#{first}"], "facet": "scope",
                },
            },
            {
                "kind": "fact", "source_items": [second],
                "record": {
                    "text": "Проверка завершилась успешно", "stated_by": "petr-ivanov",
                    "based_on": [f"ctx:priyomka#{second}"],
                    "verification": "вывод команды проверки", "verified_by": "operator",
                },
            },
        ])
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertEqual(data["decisions"][0]["id"], "d001")
        self.assertEqual(data["facts"][0]["standing"], "verified")
        self.assertTrue(data["reviews"][0]["material"])

    def test_decision_update_normalizes_person_alias_to_registry_id(self) -> None:
        item_id, package_id = self.add_text("decision", "Используем один формат")
        create = {
            "kind": "decision", "source_items": [item_id],
            "record": {
                "text": "Использовать один формат",
                "decided_by": "petr-ivanov",
                "reason": "единый формат упрощает согласование",
                "based_on": [f"ctx:priyomka#{item_id}"],
            },
        }
        created = self.apply_plan(self.plan(scope=[package_id], creates=[create]))
        self.assertEqual(created.returncode, 0, created.stderr)

        updated = self.man("decide", "--id", "d001", "--decided-by", "Пётр Иванов")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(self.manifest()["decisions"][0]["decided_by"], "petr-ivanov")

    def test_unverified_fact_remains_claim_and_vague_verification_is_refused(self) -> None:
        item_id, package_id = self.add_text("claim", "Шлюз уже доступен")
        claim = {
            "kind": "fact", "source_items": [item_id],
            "record": {"text": "Шлюз уже доступен", "stated_by": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{item_id}"]},
        }
        done = self.apply_plan(self.plan(scope=[package_id], creates=[claim]))
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertEqual(data["facts"][0]["standing"], "claim")

        second, package_two = self.add_text("vague-proof", "Шлюз проверен")
        vague = {
            "kind": "fact", "source_items": [second],
            "record": {"text": "Шлюз проверен", "stated_by": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{second}"], "verification": "проверял"},
        }
        failed = self.apply_plan(self.plan(scope=[package_two], creates=[vague]))
        self.assertEqual(failed.returncode, 1)
        self.assertIn("verification", failed.stderr)

    def test_fact_cannot_supersede_requirement(self) -> None:
        created = self.man("req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        failed = self.man(
            "fact", "--text", "Отчёт существует", "--stated-by", "petr-ivanov",
            "--based-on", "t001", "--supersedes", "t001",
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("недопустимый supersedes", failed.stderr)

    def test_provenance_cycle_refuses_whole_review(self) -> None:
        item_id, package_id = self.add_text("cycle", "Два связанных решения")
        plan = self.plan(scope=[package_id], creates=[
            {
                "kind": "decision", "source_items": [item_id],
                "record": {"text": "Первое решение", "decided_by": "operator",
                           "reason": "нужно для проверки цикла", "based_on": ["d002"]},
            },
            {
                "kind": "decision", "source_items": [item_id],
                "record": {"text": "Второе решение", "decided_by": "operator",
                           "reason": "нужно для проверки цикла", "based_on": ["d001"]},
            },
        ])
        done = self.apply_plan(plan)
        self.assertEqual(done.returncode, 1)
        self.assertIn("цикл", done.stderr)
        self.assertEqual(self.manifest()["decisions"], [])
        self.assertEqual(self.manifest()["reviews"], [])


class ReadAndProjection(ReconcileCase):
    def test_reconcile_is_implicit_for_codex_and_has_claude_commands(self) -> None:
        metadata = (ROOT / "skills" / "reconcile" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("allow_implicit_invocation: true", metadata)
        for name in ("review", "feedback", "deliver", "decide", "fact", "audiences"):
            command = ROOT / "commands" / f"{name}.md"
            self.assertTrue(command.is_file(), name)
            self.assertIn("mnemo:reconcile", command.read_text(encoding="utf-8"))

    def test_query_contract_stays_three_and_declares_capabilities(self) -> None:
        data = json.loads(run("mnemo_audit.py", "--export", str(self.export), "--json").stdout)
        self.assertEqual(data["query_contract"], "3")
        self.assertEqual(data["capabilities"],
                         ["packages", "decisions", "facts", "reviews", "audiences", "sync"])
        self.assertEqual(data["requirements"], [])
        self.assertEqual(data["questions"], [])

    def test_human_audit_surfaces_unreviewed_and_pending_sync(self) -> None:
        self.add_audience()
        _, uncovered_package = self.add_text("unreviewed")
        item_id, reviewed_package = self.add_text("reviewed", "Нужен итоговый отчёт")
        created = {
            "kind": "requirement", "source_items": [item_id],
            "record": {"quote": "Нужен итоговый отчёт", "wanted_by": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{item_id}"], "state": "stated"},
        }
        done = self.apply_plan(self.plan(scope=[reviewed_package], creates=[created]))
        self.assertEqual(done.returncode, 0, done.stderr)
        shown = run("mnemo_audit.py", "--export", str(self.export))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("НЕ РАЗОБРАНО", shown.stdout)
        self.assertIn(uncovered_package, shown.stdout)
        self.assertIn("МАТЕРИАЛЬНОЕ БЕЗ ДОСТАВКИ", shown.stdout)
        self.assertIn("СИНХРОНИЗАЦИЯ ПО АУДИТОРИЯМ", shown.stdout)

    def test_human_audit_names_material_before_packages(self) -> None:
        added = self.man(
            "add-gap", "--status", "unrecoverable", "--source", "other",
            "--fidelity", "placeholder", "--origin", "архив до пакетного учёта",
            "--note", "оригинал материала утрачен",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        shown = run("mnemo_audit.py", "--export", str(self.export))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("До пакетного учёта: 1 материал", shown.stdout)
        self.assertIn("новые внесения будут пакетами", shown.stdout)

    def test_project_state_is_generated_without_package_chronicle(self) -> None:
        item_id, package_id = self.add_text("goal", "Цель согласована")
        plan = self.plan(scope=[package_id], creates=[{
            "kind": "decision", "source_items": [item_id],
            "record": {"text": "Согласовать единый результат", "decided_by": "operator",
                       "reason": "результат должен быть однозначно проверяем",
                       "based_on": [f"ctx:priyomka#{item_id}"], "facet": "goal"},
        }])
        self.assertEqual(self.apply_plan(plan).returncode, 0)
        synced = run("mnemo_render.py", "--export", str(self.export))
        self.assertEqual(synced.returncode, 0, synced.stderr)
        text = (self.export / "summaries" / "project-state.md").read_text(encoding="utf-8")
        self.assertIn("## Цель", text)
        self.assertIn("Согласовать единый результат", text)
        self.assertNotIn("Рабочие пакеты", text)

    def test_public_slice_excludes_reconcile_state_and_rendered_feedback(self) -> None:
        self.add_audience()
        item_id, package_id = self.add_text("public", "Публичное описание")
        plan = self.plan(scope=[package_id], creates=[{
            "kind": "requirement", "source_items": [item_id],
            "record": {"quote": "Подготовить описание", "wanted_by": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{item_id}"], "state": "stated"},
        }])
        self.assertEqual(self.apply_plan(plan).returncode, 0)
        brief = self.tmp / "private-brief.txt"
        brief.write_text("Внутренний текст для руководства", encoding="utf-8")
        feedback = self.man(
            "feedback", "s001", "--audience", "руководство", "--needed",
            "--form", "confirmation", "--includes-records", "t001",
            "--text-file", str(brief),
        )
        self.assertEqual(feedback.returncode, 0, feedback.stderr)
        # Публикуем сам item явно; приватность проверяется для метаданных сверки.
        manifest_path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["items"][0]["contour"] = "public"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        out = self.tmp / "public-slice"
        published = run("mnemo_publish.py", "--export", str(self.export),
                        "--out", str(out), "--apply")
        self.assertEqual(published.returncode, 0, published.stderr)
        slim = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertNotIn("audiences", slim["export"])
        for private in ("decisions", "facts", "reviews"):
            self.assertNotIn(private, slim)
        self.assertNotIn("Внутренний текст для руководства",
                         (out / "MANIFEST.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
