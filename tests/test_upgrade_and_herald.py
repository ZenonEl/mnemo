"""Регрессии обновления legacy-экспортов и handshake с Herald."""

from __future__ import annotations

import json

from tests.test_reconcile import ReconcileCase, run


class ReviewRegression(ReconcileCase):
    def _question(self, item_id: str) -> dict:
        return {
            "kind": "question", "source_items": [item_id],
            "record": {
                "text": "Какой способ оплаты выбрать?",
                "impact": "определяет сценарий оплаты",
                "self_attempt": "проверил материалы — способ не указан",
                "asked_of": "petr-ivanov",
                "based_on": [f"ctx:priyomka#{item_id}"],
            },
        }

    def test_writer_retracts_answer_as_material_unanswered_and_needs_no_upgrade(self) -> None:
        first, package_one = self.add_text("question", "Нужно выбрать способ оплаты")
        self.assertEqual(self.apply_plan(self.plan(
            scope=[package_one], creates=[self._question(first)],
        )).returncode, 0)

        second, package_two = self.add_text("answer", "Оплата будет по счёту")
        answered = self.apply_plan(self.plan(scope=[package_two], mutations=[{
            "record": "q001", "field": "answered_by",
            "value": f"ctx:priyomka#{second}", "source_items": [second],
        }]))
        self.assertEqual(answered.returncode, 0, answered.stderr)

        third, package_three = self.add_text("retraction", "Ответ про счёт отозван")
        retracted = self.apply_plan(self.plan(scope=[package_three], mutations=[{
            "record": "q001", "field": "answered_by",
            "value": None, "source_items": [third],
        }]))
        self.assertEqual(retracted.returncode, 0, retracted.stderr)
        review = self.manifest()["reviews"][-1]
        self.assertEqual(review["changes"][0]["action"], "unanswered")
        self.assertTrue(json.loads(run(
            "mnemo_audit.py", "--export", str(self.export), "--json",
        ).stdout)["reviews"][-1]["material"])
        plan = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        self.assertEqual(plan["changes"], [])

    def test_noop_mutation_is_refused_during_preview_without_writing(self) -> None:
        created = self.man("req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        item, package = self.add_text("same", "Требование остаётся заявленным")
        before = self.manifest_hash()
        shown = self.man("review", "--plan-file", str(self.plan(
            scope=[package], mutations=[{
                "record": "t001", "field": "state", "value": "stated",
                "source_items": [item],
            }], audience_waiver=False,
        )))
        self.assertEqual(shown.returncode, 1)
        self.assertIn("уже имеет это значение", shown.stderr)
        self.assertEqual(self.manifest_hash(), before)

    def test_clearing_based_on_is_updated_not_confirmed(self) -> None:
        source, _ = self.add_text("source", "Исходное основание")
        created = self.man(
            "req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov",
            "--based-on", f"ctx:priyomka#{source}",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        item, package = self.add_text("withdraw-source", "Основание снято")
        done = self.apply_plan(self.plan(scope=[package], mutations=[{
            "record": "t001", "field": "based_on", "value": [],
            "source_items": [item],
        }], audience_waiver=False))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest()["reviews"][-1]["changes"][0]["action"], "updated")

    def test_v24_rejects_unanswered_with_nonempty_after(self) -> None:
        item, package = self.add_text("answer", "Оплата будет по счёту")
        created = self.man(
            "ask", "--text", "Как платить?", "--impact", "меняет сценарий оплаты",
            "--self-attempt", "проверил пакет и нашёл ответ", "--asked-of", "petr-ivanov",
            "--based-on", f"ctx:priyomka#{item}",
            "--answered-by", f"ctx:priyomka#{item}",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["reviews"].append({
            "id": "s001", "date": "2026-09-14", "by": "operator",
            "scope": [package], "changes": [{
                "action": "unanswered", "record": "q001", "source_items": [item],
                "delta": [{"field": "answered_by", "before": None,
                           "after": f"ctx:priyomka#{item}"}], "note": None,
            }], "nonmaterial": [], "impacts": [], "feedback": [], "note": None,
        })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        checked = json.loads(run(
            "mnemo_verify.py", "--export", str(self.export), "--json",
        ).stdout)
        v24 = [row["message"] for row in checked["errors"] if row["code"] == "V24"]
        self.assertIn("unanswered не соответствует delta", v24)

    def test_material_review_without_audience_requires_explicit_waiver(self) -> None:
        item, package = self.add_text("waiver", "Нужно выпускать отчёт")
        plan = self.plan(scope=[package], creates=[{
            "kind": "requirement", "source_items": [item],
            "record": {"quote": "Нужно выпускать отчёт", "wanted_by": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{item}"], "state": "stated"},
        }])
        data = json.loads(plan.read_text(encoding="utf-8"))
        data.pop("audience_not_needed_reason")
        plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.manifest_hash()
        shown = self.man("review", "--plan-file", str(plan))
        self.assertEqual(shown.returncode, 1)
        self.assertIn("audience_not_needed_reason", shown.stderr)
        self.assertEqual(self.manifest_hash(), before)

        data["audience_not_needed_reason"] = "синхронизация ведётся вне этого архива"
        plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.apply_plan(plan).returncode, 0)
        self.assertEqual(self.manifest()["mnemo_spec"], "1.18")
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["reviews"][0].pop("audience_not_needed_reason")
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        checked = run("mnemo_verify.py", "--export", str(self.export))
        self.assertIn("V22", checked.stdout)
        audited = run("mnemo_audit.py", "--export", str(self.export))
        self.assertIn("синхронизация не проверяется", audited.stdout)

    def test_material_apply_prints_pending_feedback_command(self) -> None:
        self.add_audience()
        item, package = self.add_text("pending", "Нужно выпускать отчёт")
        done = self.apply_plan(self.plan(scope=[package], creates=[{
            "kind": "requirement", "source_items": [item],
            "record": {"quote": "Нужно выпускать отчёт", "wanted_by": "petr-ivanov",
                       "based_on": [f"ctx:priyomka#{item}"], "state": "stated"},
        }]))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("материальна; pending: руководство", done.stdout)
        self.assertIn("mnemo_manifest.py feedback", done.stdout)
        self.assertIn("--audience", done.stdout)
        self.assertIn("--includes-records t001", done.stdout)


class UpgradeCommand(ReconcileCase):
    def test_unhandled_v24_problem_is_named_in_plan(self) -> None:
        created = self.man("req", "--quote", "Нужен отчёт", "--wanted-by", "petr-ivanov")
        self.assertEqual(created.returncode, 0, created.stderr)
        item, package = self.add_text("unknown-action", "Состояние изменилось")
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["reviews"].append({
            "id": "s001", "date": "2026-09-14", "by": "operator",
            "scope": [package], "changes": [{
                "action": "legacy-action", "record": "t001", "source_items": [item],
                "delta": [{"field": "state", "before": "stated", "after": "accepted"}],
                "note": None,
            }], "nonmaterial": [], "impacts": [], "feedback": [], "note": None,
        })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        plan = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        self.assertEqual(plan["changes"], [])
        self.assertIn("V24 s001.changes[0]: неизвестный action", plan["known_unhandled"][2])

    def test_repairs_all_three_v24_shapes_from_live_archive(self) -> None:
        seed, seed_package = self.add_text("seed", "Нужно выбрать способ оплаты")
        question = {
            "kind": "question", "source_items": [seed],
            "record": {"text": "Как платить?", "impact": "меняет сценарий оплаты",
                       "self_attempt": "проверил пакет — ответа нет",
                       "asked_of": "petr-ivanov", "based_on": [f"ctx:priyomka#{seed}"]},
        }
        self.assertEqual(
            self.apply_plan(self.plan(scope=[seed_package], creates=[question])).returncode, 0
        )
        for number in range(1, 5):
            created = self.man(
                "req", "--quote", f"Требование {number}", "--wanted-by", "petr-ivanov",
                "--anyway",
            )
            self.assertEqual(created.returncode, 0, created.stderr)

        package_rows = [self.add_text(f"legacy-{number}") for number in range(8)]
        changes = [
            {"action": "updated", "record": "q001",
             "delta": [{"field": "answered_by", "before": "ctx:priyomka#d012",
                        "after": None}]},
            {"action": "updated", "record": "q001",
             "delta": [{"field": "answered_by", "before": "ctx:priyomka#i439",
                        "after": None}]},
            *[
                {"action": "confirmed", "record": f"t{number:03d}",
                 "delta": [{"field": "based_on",
                            "before": ["ctx:priyomka#i001", "ctx:priyomka#i002"],
                            "after": ["ctx:priyomka#i001", "ctx:priyomka#i002"]}]}
                for number in range(1, 5)
            ],
            {"action": "updated", "record": "q001",
             "delta": [{"field": "where", "before": None, "after": None}]},
            {"action": "updated", "record": "q001",
             "delta": [{"field": "note", "before": None, "after": None}]},
        ]
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        for offset, ((item_id, package_id), change) in enumerate(
                zip(package_rows, changes), start=2):
            change["source_items"] = [item_id]
            change["note"] = None
            manifest["reviews"].append({
                "id": f"s{offset:03d}", "date": "2026-09-14", "by": "operator",
                "scope": [package_id], "changes": [change], "nonmaterial": [],
                "impacts": [], "feedback": [], "note": None,
            })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

        broken = json.loads(run(
            "mnemo_verify.py", "--export", str(self.export), "--json",
        ).stdout)
        self.assertEqual(len([row for row in broken["errors"] if row["code"] == "V24"]), 8)
        dry = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        self.assertEqual(
            [row["kind"] for row in dry["changes"]].count("reclassify-review-action"), 2
        )
        self.assertEqual(
            [row["kind"] for row in dry["changes"]].count("drop-noop-review-change"), 6
        )
        self.assertEqual(dry["known_unhandled"][2:], [])
        applied = self.man(
            "upgrade", "--apply", "--base-manifest-sha256",
            dry["base_manifest_sha256"], "--by", "operator", "--reason",
            "исправлены восемь известных legacy-ошибок V24",
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        checked = json.loads(run(
            "mnemo_verify.py", "--export", str(self.export), "--json",
        ).stdout)
        self.assertEqual([row for row in checked["errors"] if row["code"] == "V24"], [])
        repeated = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        self.assertEqual(repeated["changes"], [])

    def test_repairs_old_action_preserves_legacy_import_and_acknowledges_sync(self) -> None:
        item, package = self.add_text("legacy", "Нужно выбрать способ оплаты")
        create = {
            "kind": "question", "source_items": [item],
            "record": {"text": "Как платить?", "impact": "меняет сценарий оплаты",
                       "self_attempt": "проверил пакет — ответа нет",
                       "asked_of": "petr-ivanov", "based_on": [f"ctx:priyomka#{item}"]},
        }
        self.assertEqual(self.apply_plan(self.plan(scope=[package], creates=[create])).returncode, 0)
        answer, answer_package = self.add_text("answer", "Оплата по счёту")
        self.assertEqual(self.apply_plan(self.plan(scope=[answer_package], mutations=[{
            "record": "q001", "field": "answered_by",
            "value": f"ctx:priyomka#{answer}", "source_items": [answer],
        }])).returncode, 0)
        retract, retract_package = self.add_text("retract", "Ответ отозван")
        self.assertEqual(self.apply_plan(self.plan(scope=[retract_package], mutations=[{
            "record": "q001", "field": "answered_by", "value": None,
            "source_items": [retract],
        }])).returncode, 0)

        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["reviews"][-1]["changes"][0]["action"] = "answered"
        manifest["imports"].append({
            "parser": "legacy", "source": "old", "imported": "2026-01-01",
            "messages": 1, "keys": ["legacy:1"],
        })
        manifest["requirements"].append({
            "id": "t001", "quote": "Нужен архив", "wanted_by": "petr-ivanov",
            "based_on": [f"ctx:priyomka#{answer}"], "state": "stated",
        })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

        broken = run("mnemo_verify.py", "--export", str(self.export))
        self.assertEqual(broken.returncode, 1)
        self.assertIn("V24", broken.stdout)

        dry = self.man("upgrade")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        plan = json.loads(dry.stdout.split("\n\n—", 1)[0])
        self.assertEqual({row["kind"] for row in plan["changes"]}, {
            "reclassify-review-action", "preserve-legacy-import",
            "acknowledge-unreviewed-change",
        })
        applied = self.man(
            "upgrade", "--apply", "--base-manifest-sha256",
            plan["base_manifest_sha256"], "--by", "operator", "--reason",
            "исправлены известные дефекты legacy-сверки",
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        saved = self.manifest()
        self.assertEqual(saved["reviews"][-1]["changes"][0]["action"], "unanswered")
        self.assertNotIn("id", saved["imports"][-1])
        self.assertNotIn("items", saved["imports"][-1])
        self.assertEqual(saved["upgrades"][0]["id"], "u001")
        self.assertEqual(saved["mnemo_spec"], "1.18")
        self.assertEqual(run("mnemo_verify.py", "--export", str(self.export)).returncode, 0)
        again = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        self.assertEqual(again["changes"], [])

        tampered = json.loads(json.dumps(saved))
        tampered["upgrades"][0]["changes"][0]["target"] = "подменённая цель"
        path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        checked = run("mnemo_verify.py", "--export", str(self.export))
        self.assertEqual(checked.returncode, 1)
        self.assertIn("V27", checked.stdout)

        saved["items"][0]["contour"] = "public"
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        public = self.tmp / "public"
        published = run(
            "mnemo_publish.py", "--export", str(self.export),
            "--out", str(public), "--apply",
        )
        self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
        public_manifest = json.loads((public / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertNotIn("upgrades", public_manifest)
        self.assertEqual(public_manifest.get("imports"), [])

    def test_apply_refuses_stale_hash(self) -> None:
        plan = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        failed = self.man(
            "upgrade", "--apply", "--base-manifest-sha256", "0" * 64,
            "--by", "operator", "--reason", "проверка устаревшего плана",
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("состояние изменилось", failed.stderr)
        self.assertNotEqual(plan["base_manifest_sha256"], "0" * 64)

    def test_apply_requires_author_and_substantive_reason(self) -> None:
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["imports"].append({
            "parser": "legacy", "source": "old", "imported": "2026-01-01",
            "messages": 1, "keys": ["legacy:1"],
        })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        plan = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        before = self.manifest_hash()
        for args in (
            ("--reason", "исправление известного legacy-дефекта"),
            ("--by", "operator", "--reason", "не"),
        ):
            with self.subTest(args=args):
                failed = self.man(
                    "upgrade", "--apply", "--base-manifest-sha256",
                    plan["base_manifest_sha256"], *args,
                )
                self.assertEqual(failed.returncode, 1)
                self.assertIn("содержательный", failed.stderr)
                self.assertEqual(self.manifest_hash(), before)

    def test_drops_legacy_noop_without_losing_package_coverage(self) -> None:
        item, package = self.add_text("question", "Нужно выбрать способ оплаты")
        create = {
            "kind": "question", "source_items": [item],
            "record": {"text": "Как платить?", "impact": "меняет сценарий оплаты",
                       "self_attempt": "проверил пакет — ответа нет",
                       "asked_of": "petr-ivanov", "based_on": [f"ctx:priyomka#{item}"]},
        }
        self.assertEqual(self.apply_plan(self.plan(scope=[package], creates=[create])).returncode, 0)
        noop_item, noop_package = self.add_text("old-noop", "Ответа по-прежнему нет")
        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["reviews"].append({
            "id": "s002", "date": "2026-09-14", "by": "operator",
            "scope": [noop_package], "changes": [{
                "action": "answered", "record": "q001", "source_items": [noop_item],
                "delta": [{"field": "answered_by", "before": None, "after": None}],
                "note": None,
            }], "nonmaterial": [], "impacts": [], "feedback": [], "note": None,
        })
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        plan = json.loads(self.man("upgrade").stdout.split("\n\n—", 1)[0])
        self.assertIn("drop-noop-review-change", {row["kind"] for row in plan["changes"]})
        done = self.man(
            "upgrade", "--apply", "--base-manifest-sha256",
            plan["base_manifest_sha256"], "--by", "operator", "--reason",
            "удалён исторический change без изменения значения",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        repaired = self.manifest()["reviews"][-1]
        self.assertEqual(repaired["changes"], [])
        self.assertEqual(repaired["nonmaterial"][0]["items"], [noop_item])
        checked = run("mnemo_verify.py", "--export", str(self.export))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


class HeraldHandshake(ReconcileCase):
    def test_import_keeps_structured_keys_prints_address_and_requires_118(self) -> None:
        source = self.tmp / "herald-bundle"
        source.mkdir()
        (source / "inbox.json").write_text(json.dumps({"messages": [{
            "chat_id": -100123, "message_id": 7, "chat_slug": "ops",
            "date": "2026-09-14T10:00:00", "author_name": "Пётр Иванов",
            "text": "Готово к приёмке",
        }]}, ensure_ascii=False), encoding="utf-8")
        done = run(
            "mnemo_import.py", "--export", str(self.export),
            "--source", str(source), "--apply",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        package = self.manifest()["imports"][-1]
        self.assertEqual(package["herald_keys"], [{"chat_id": -100123, "message_id": 7}])
        self.assertEqual(self.manifest()["mnemo_spec"], "1.18")
        self.assertIn("адрес пакета: ctx:priyomka#p001", done.stdout)
        self.assertIn('inbox_done(keys=[{"chat_id": -100123, "message_id": 7}]', done.stdout)
        self.assertIn('archive_ref="ctx:priyomka#p001"', done.stdout)
        audit = json.loads(run(
            "mnemo_audit.py", "--export", str(self.export), "--json",
        ).stdout)
        self.assertNotIn("herald_keys", audit["packages"][0])

        path = self.export / "MANIFEST.json"
        manifest = self.manifest()
        manifest["mnemo_spec"] = "1.17"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        checked = run("mnemo_verify.py", "--export", str(self.export))
        self.assertEqual(checked.returncode, 1)
        self.assertIn("V18", checked.stdout)
