"""Поведение проверяющей половины: блокировка, заслон на вопрос, версия.

Линтер проверяет готовый архив, самопроверка — согласованность плагина. Ни то,
ни другое не видит, что делает команда: что понижение до `ours` объявляется
вслух, что «подтверждено» без новой улики не принимается, что понижённый вопрос
уходит из открытых и остаётся в полной сводке.

Отдельно здесь живут две регрессии на §14, и они устроены по-разному
намеренно — см. `SpecVersionIsRaised` и `SpecVersionDriftIsCaughtByLinter`.

Только стандартная библиотека:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mnemo_core import SPEC_VERSION  # noqa: E402


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          text=True, capture_output=True)


class ExportCase(unittest.TestCase):
    """Пустой экспорт с одним человеком в реестре."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.export = self.dir / "e"
        _run("mnemo_manifest.py", "init", "--dir", str(self.export),
             "--slug", "priyomka", "--title", "Приёмка проекта")
        _run("mnemo_manifest.py", "people", "--export", str(self.export), "--add",
             "--display", "Пётр Иванов", "--role", "client")

    # -- удобства ---------------------------------------------------------

    def man(self, *args: str) -> subprocess.CompletedProcess:
        return _run("mnemo_manifest.py", *args, "--export", str(self.export))

    def manifest(self) -> dict:
        return json.loads((self.export / "MANIFEST.json").read_text(encoding="utf-8"))

    def audit(self, *args: str) -> subprocess.CompletedProcess:
        return _run("mnemo_audit.py", "--export", str(self.export), *args)

    def audit_json(self, *args: str) -> dict:
        done = self.audit("--json", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def a_requirement(self, **flags: str) -> str:
        args = ["req", "--quote", flags.pop("quote", "выгрузка остатков раз в час"),
                "--wanted-by", "petr-ivanov"]
        for key, value in flags.items():
            args += [f"--{key.replace('_', '-')}", value]
        done = self.man(*args)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.last_stdout = done.stdout
        return done.stdout.split()[0]

    def a_question(self, **flags: str) -> str:
        args = ["ask", "--text", flags.pop("text", "точно ли эта платёжка"),
                "--impact", flags.pop("impact", "какой SDK и какие вебхуки в интеграции"),
                "--asked-of", "petr-ivanov",
                "--based-on", "ctx:priyomka#i001",
                "--self-attempt", flags.pop(
                    "self_attempt",
                    "прочитал ТЗ и переписку за август — платёжка нигде не названа")]
        for key, value in flags.items():
            args += [f"--{key.replace('_', '-')}", value]
        done = self.man(*args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.split()[0]

    def record(self, ident: str) -> dict:
        bucket = "requirements" if ident.startswith("t") else "questions"
        found = next(r for r in self.manifest()[bucket] if r["id"] == ident)
        return found


class AttemptGate(ExportCase):
    """Поле попытки, заполненное отглагольным существительным, — не попытка.

    Пустое поле честно: команда запишет `ours` и скажет об этом. «Разбирался»
    выглядит как работа, которой не было, и потому отвергается — ровно как
    `gate_question` отвергает вопрос без последствий.
    """

    def test_tried_without_an_object_is_refused(self) -> None:
        done = self.man("req", "--quote", "нужен доступ к их API",
                        "--blocking", "интеграция стоит", "--tried", "разбирался")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--tried", done.stderr)
        self.assertEqual(self.manifest()["requirements"], [])

    def test_self_attempt_without_an_object_is_refused(self) -> None:
        done = self.man("ask", "--text", "точно ли эта платёжка",
                        "--impact", "какой SDK в интеграции",
                        "--asked-of", "petr-ivanov", "--based-on", "ctx:priyomka#i001",
                        "--self-attempt", "изучал")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--self-attempt", done.stderr)
        self.assertEqual(self.manifest()["questions"], [])

    def test_an_attempt_naming_a_thing_passes(self) -> None:
        ident = self.a_requirement(blocking="интеграция стоит",
                                   tried="написал в поддержку 20.08")
        self.assertEqual(self.record(ident)["tried"], "написал в поддержку 20.08")

    def test_a_new_question_without_self_attempt_is_refused(self) -> None:
        done = self.man("ask", "--text", "точно ли эта платёжка",
                        "--impact", "какой SDK в интеграции",
                        "--asked-of", "petr-ivanov", "--based-on", "ctx:priyomka#i001")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--self-attempt", done.stderr)
        self.assertEqual(self.manifest()["questions"], [])


class Escalation(ExportCase):
    """На чьей стороне следующий шаг — выводится, и понижение объявляется вслух."""

    def test_a_blocker_without_dead_end_is_ours_and_says_so(self) -> None:
        ident = self.a_requirement(blocking="интеграция стоит",
                                   tried="написал в поддержку 20.08",
                                   returned="молчат с 20.08")
        self.assertIn("ours", self.last_stdout)
        self.assertIn("--dead-end", self.last_stdout)
        row = next(r for r in self.audit_json()["requirements"] if r["id"] == ident)
        self.assertEqual(row["escalation"], "ours")

    def test_a_full_attempt_is_theirs(self) -> None:
        ident = self.a_requirement(blocking="нет ключа",
                                   tried="написал в поддержку 20.08",
                                   returned="молчат с 20.08",
                                   dead_end="их админ обязан выдать ключ")
        self.assertIn("theirs", self.last_stdout)
        row = next(r for r in self.audit_json()["requirements"] if r["id"] == ident)
        self.assertEqual(row["escalation"], "theirs")

    def test_without_blocking_there_is_no_escalation(self) -> None:
        ident = self.a_requirement()
        row = next(r for r in self.audit_json()["requirements"] if r["id"] == ident)
        self.assertIsNone(row["escalation"])

    def test_the_verdict_names_the_split_even_when_all_of_it_is_theirs(self) -> None:
        # Расклад исчезал ровно тогда, когда он ценнее всего: когда всё стоит на
        # чужой стороне и отчёт наверх состоит из него целиком. Условие стояло
        # на одной стороне вместо обеих.
        self.a_requirement(blocking="интеграция стоит",
                           tried="написал в поддержку 20.08",
                           returned="молчат с 20.08",
                           dead_end="их админ обязан выдать ключ")
        out = self.audit().stdout
        self.assertIn("ждёт чужого шага 1", out)

    def test_the_summary_puts_someone_elses_step_above_ours(self) -> None:
        self.a_requirement(quote="нужен доступ к их API", blocking="интеграция стоит")
        self.a_requirement(quote="выгрузка остатков раз в час", blocking="нет ключа",
                           tried="написал в поддержку 20.08",
                           returned="молчат с 20.08",
                           dead_end="их админ обязан выдать ключ")
        out = self.audit().stdout
        self.assertLess(out.index("ЖДЁМ ЧУЖОГО ШАГА"), out.index("СЛЕДУЮЩИЙ ШАГ НАШ"))

    def test_a_blocker_with_no_attempt_at_all_is_marked_in_the_summary(self) -> None:
        # Вторая пометка того же рода, что [НЕ СПРАШИВАЛИ]: все блокеры,
        # заведённые до 1.16, станут ours, и это обязано быть сказано вслух,
        # а не случиться молча.
        self.a_requirement(blocking="интеграция стоит")
        self.assertIn("ПОПЫТКА НЕ ПРЕДЪЯВЛЕНА", self.audit().stdout)


class SelfRefutationPass(ExportCase):
    """Исход прохода решает команда: она видит и старый текст, и новый."""

    def blocked(self) -> str:
        return self.a_requirement(blocking="нет ключа",
                                  tried="написал в поддержку 20.08",
                                  returned="молчат с 20.08",
                                  dead_end="их админ обязан выдать ключ")

    def test_confirmed_without_new_evidence_becomes_insufficient(self) -> None:
        ident = self.blocked()
        done = self.man("req", "--id", ident, "--pass-outcome", "confirmed",
                        "--returned", "молчат с 20.08")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("insufficient", done.stdout)
        record = self.record(ident)
        self.assertIsNone(record["dead_end"])
        self.assertIn("новая попытка не найдена", record["returned"])
        row = next(r for r in self.audit_json()["requirements"] if r["id"] == ident)
        self.assertEqual(row["escalation"], "ours")

    def test_confirmed_with_a_second_piece_of_evidence_stays_theirs(self) -> None:
        ident = self.blocked()
        done = self.man("req", "--id", ident, "--pass-outcome", "confirmed",
                        "--returned", "написал их админу напрямую — тот же отказ 401")
        self.assertEqual(done.returncode, 0, done.stderr)
        record = self.record(ident)
        self.assertIn("молчат с 20.08", record["returned"])
        self.assertIn("401", record["returned"])
        self.assertEqual(record["dead_end"], "их админ обязан выдать ключ")
        row = next(r for r in self.audit_json()["requirements"] if r["id"] == ident)
        self.assertEqual(row["escalation"], "theirs")

    def test_refuted_drops_the_dead_end_and_records_the_way_around(self) -> None:
        ident = self.blocked()
        done = self.man("req", "--id", ident, "--pass-outcome", "refuted",
                        "--returned", "тот же файл отдаётся через публичный фид")
        self.assertEqual(done.returncode, 0, done.stderr)
        record = self.record(ident)
        self.assertIsNone(record["dead_end"])
        self.assertIn("публичный фид", record["returned"])

    def test_confirmed_without_a_first_piece_of_evidence_is_refused(self) -> None:
        """`confirmed` — это «вернулось то же, что и в прошлый раз».

        Прошлого раза в записи нет — сравнивать не с чем, и принятая улика была
        бы первой, а не второй. Проверка «пополнилось ли returned» при пустом
        старом вырождалась в «строка непустая» и пропускала переезд
        `ours → theirs` без единого сравнения: дешёвый обход у механизма,
        заведённого ради того, чтобы дешёвого пути не было.
        """
        ident = self.a_requirement(blocking="приёмка стоит",
                                   tried="написал их админу 25.08",
                                   dead_end="их админ обязан выдать доступ")
        before = self.record(ident)
        done = self.man("req", "--id", ident, "--pass-outcome", "confirmed",
                        "--returned", "та же стена")
        self.assertEqual(done.returncode, 1)
        self.assertIn("первой улики", done.stderr)
        # Отказ обязан ничего не менять: запись осталась ровно как была.
        self.assertEqual(self.record(ident), before)
        row = next(r for r in self.audit_json()["requirements"] if r["id"] == ident)
        self.assertEqual(row["escalation"], "ours")

    def test_a_pass_on_a_record_that_blocks_nothing_is_refused(self) -> None:
        # Проход существует, чтобы попытаться снять блокер. Блокера нет —
        # снимать нечего, а дописанный «обход» засорял бы returned записи,
        # которая ничего не блокировала, и поднимал ей версию стандарта.
        ident = self.a_requirement()
        done = self.man("req", "--id", ident, "--pass-outcome", "confirmed",
                        "--returned", "та же стена")
        self.assertEqual(done.returncode, 1)
        self.assertIn("blocking", done.stderr)
        self.assertIsNone(self.record(ident)["returned"])

    def test_the_pass_never_names_a_side_the_record_does_not_have(self) -> None:
        # Строка про исход прохода была зашита константой и объявляла theirs,
        # пока соседняя строка того же вывода объявляла ours. Классификацию
        # печатает одно место — из настоящего значения после правки.
        ident = self.a_requirement(blocking="интеграция стоит",
                                   returned="отказ 401",
                                   dead_end="их админ обязан выдать ключ")
        done = self.man("req", "--id", ident, "--pass-outcome", "confirmed",
                        "--returned", "написал их админу напрямую — тот же отказ 401")
        self.assertEqual(done.returncode, 0, done.stderr)
        # --tried не заполнен, значит запись — ours, и ни одна строка вывода не
        # вправе объявить её чужой. «Предъяви попытку — и станет theirs» в
        # хвосте подсказки не в счёт: это условие, а не вердикт.
        self.assertIn("завёл как ours", done.stdout)
        self.assertNotIn("остаётся theirs", done.stdout)
        self.assertNotIn("escalation=theirs", done.stdout)

    def test_a_pass_on_a_new_record_is_refused_rather_than_ignored(self) -> None:
        # Сравнивать не с чем: старого returned у новой записи нет. Тихо
        # проглотить флаг значит отчитаться о проходе, которого не было.
        done = self.man("req", "--quote", "нужен доступ к их API",
                        "--blocking", "интеграция стоит",
                        "--pass-outcome", "confirmed", "--returned", "молчат с 20.08")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--id", done.stderr)
        self.assertEqual(self.manifest()["requirements"], [])

    def test_refuted_without_a_description_is_refused(self) -> None:
        ident = self.blocked()
        done = self.man("req", "--id", ident, "--pass-outcome", "refuted")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.record(ident)["dead_end"], "их админ обязан выдать ключ")


class DowngradeToAssumption(ExportCase):
    """Не прошедший заслон вопрос понижается, а не исчезает."""

    def test_assumed_without_a_price_is_refused(self) -> None:
        ident = self.a_question()
        done = self.man("ask", "--id", ident, "--assumed", "как в предыдущих проектах")
        self.assertEqual(done.returncode, 1)
        self.assertIn("cost-if-wrong", done.stderr)
        self.assertIsNone(self.record(ident)["assumed"])

    def test_a_downgraded_question_leaves_the_open_list(self) -> None:
        ident = self.a_question()
        self.assertIn(ident, [q["id"] for q in self.audit_json("--open-only")["questions"]])
        done = self.man("ask", "--id", ident,
                        "--assumed", "берём ту же платёжку, что в прошлых проектах",
                        "--cost-if-wrong", "переписать модуль оплаты и вебхуки, день")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(ident,
                         [q["id"] for q in self.audit_json("--open-only")["questions"]])

    def test_the_human_report_thins_the_open_list_too(self) -> None:
        """§6б п.8 требует обоих поведений сразу, а не одного из них.

        Правило «что считается открытым» нужно и сводке, и `--open-only`.
        Пока оно стояло в двух литералах, сломать можно было тот, который не
        читает ни один тест: сводка сама себе противоречила — «вопросов
        открытых: 1» над секцией допущений с тем же идентификатором.
        """
        ident = self.a_question()
        self.man("ask", "--id", ident,
                 "--assumed", "берём ту же платёжку, что в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты и вебхуки, день")
        out = self.audit().stdout
        self.assertIn("Вопросов открытых: 0", out)
        self.assertIn("ДОПУЩЕНИЯ", out)

    def test_a_downgraded_question_stays_visible_in_the_full_summary(self) -> None:
        ident = self.a_question()
        self.man("ask", "--id", ident,
                 "--assumed", "берём ту же платёжку, что в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты и вебхуки, день")
        out = self.audit().stdout
        self.assertIn("ДОПУЩЕНИЯ", out)
        self.assertIn(ident, out)
        self.assertIn("переписать модуль оплаты", out)

    def test_an_assumption_does_not_rot(self) -> None:
        """Понижённый вопрос никого не ждёт — значит и протухать ему нечем.

        Ветка держалась ни на чём: тестовые вопросы заводятся сегодняшним
        числом и до порога протухания не доживают, поэтому её отключение
        оставляло весь набор зелёным. А без неё допущение возвращается в работу
        через секцию «ПРОТУХЛО» — то есть понижение отменяется тем же способом,
        каким его запретили отменять через список открытых.
        """
        old = (date.today() - timedelta(days=60)).isoformat()
        ident = self.a_question(date=old)
        self.assertIn("ПРОТУХЛО", self.audit().stdout)

        self.man("ask", "--id", ident, "--assumed", "как в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты, день")
        out = self.audit().stdout
        self.assertNotIn("ПРОТУХЛО", out)
        self.assertIn("ДОПУЩЕНИЯ", out)

    def test_the_assumption_is_readable_without_the_tool(self) -> None:
        # §1: экспорт самодостаточен. Состояние `assumed` в INDEX без текста
        # допущения показывало бы, что вопрос не задан, и умалчивало, чем его
        # заменили и чем мы рискуем.
        ident = self.a_question()
        self.man("ask", "--id", ident,
                 "--assumed", "берём ту же платёжку, что в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты и вебхуки, день")
        _run("mnemo_render.py", "--export", str(self.export))
        index = (self.export / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Допущения", index)
        self.assertIn("берём ту же платёжку", index)
        self.assertIn("переписать модуль оплаты", index)

    def test_a_downgrade_is_not_a_drop(self) -> None:
        # «Снят» значит перестал интересовать, «допущение» значит «ответили себе
        # сами». Смешать их — потерять различие, ради которого понижение и есть.
        ident = self.a_question()
        self.man("ask", "--id", ident, "--assumed", "как в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты, день")
        record = self.record(ident)
        self.assertIsNone(record["dropped_reason"])
        row = next(q for q in self.audit_json()["questions"] if q["id"] == ident)
        self.assertEqual(row["state"], "assumed")

    def test_a_real_answer_overrides_the_assumption(self) -> None:
        ident = self.a_question()
        self.man("ask", "--id", ident, "--assumed", "как в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты, день")
        done = self.man("ask", "--id", ident, "--answered-by", "ctx:priyomka#i004")
        self.assertEqual(done.returncode, 0, done.stderr)
        row = next(q for q in self.audit_json()["questions"] if q["id"] == ident)
        self.assertEqual(row["state"], "answered")
        # Допущение при этом не стирается: сопоставление его с ответом и есть
        # «проверенное допущение», третьего хранимого поля для этого не нужно.
        self.assertEqual(row["assumed"], "как в прошлых проектах")

    def test_the_linter_refuses_an_assumption_without_a_price(self) -> None:
        ident = self.a_question()
        manifest = self.manifest()
        for question in manifest["questions"]:
            if question["id"] == ident:
                question["assumed"] = "как в прошлых проектах"
        (self.export / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report = json.loads(_run("mnemo_verify.py", "--export", str(self.export),
                                 "--json").stdout)
        self.assertIn("V20", [e["code"] for e in report["errors"]])


class BrokenRaisedMarks(ExportCase):
    """Отметка «спрошено», пришедшая из чужого или правленого руками манифеста.

    `SPEC/QUERY.md` п.3: команда не падает на одной битой записи, пряча
    остальные, — разбираться по выводу линтера. Через команды такой манифест не
    собрать, `new_question` его валидирует; но именно чужой и правленый руками
    манифест этот пункт и защищает — ровно как линтерная сторона §14.

    Дыр было две, обе старше этой ветки: `raised` не списком роняло
    `last_raised` (`'str' object has no attribute 'get'`), а отметка без ключей
    роняла сводку на `raised['at']`. Падал и линтер — то есть идти разбираться
    было некуда.
    """

    def with_broken_marks(self) -> str:
        good = self.a_question(text="живой вопрос")
        self.man("ask", "--id", good, "--raised-to", "petr-ivanov",
                 "--raised-at", "2026-08-20")
        manifest = self.manifest()
        manifest["questions"] += [
            {"id": "q002", "text": "raised строкой", "blocking": "стоит",
             "raised": "вчера"},
            {"id": "q003", "text": "отметка без ключей", "blocking": "стоит",
             "raised": [{"to": None}]},
        ]
        (self.export / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return good

    def test_the_summary_survives_and_hides_nothing(self) -> None:
        good = self.with_broken_marks()
        done = self.audit()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(good, done.stdout)          # живая запись не спряталась
        self.assertIn("q002", done.stdout)        # битая — видна
        self.assertIn("q003", done.stdout)
        self.assertIn("<без адресата>", done.stdout)   # заглушка, а не падение
        self.assertEqual(self.audit("--json").returncode, 0)
        self.assertEqual(self.audit("--json", "--open-only").returncode, 0)

    def test_the_linter_diagnoses_instead_of_crashing(self) -> None:
        # Падение линтера тут хуже прочих: §16 п.3 отправляет разбираться
        # именно к нему, а traceback — не диагноз (§13).
        self.with_broken_marks()
        done = _run("mnemo_verify.py", "--export", str(self.export), "--json")
        self.assertNotIn("Traceback", done.stderr)
        report = json.loads(done.stdout)
        broken = {e["where"]: e["message"] for e in report["errors"]
                  if e["code"] == "V20"}
        self.assertEqual(set(broken), {"q002", "q003"})
        # Диагноз обязан называть поломку, а не только запись: «что-то не так с
        # q002» отправляет человека искать заново.
        self.assertIn("не список", broken["q002"])
        self.assertIn("без адресата", broken["q003"])

    def test_the_index_survives_and_marks_the_gap(self) -> None:
        self.with_broken_marks()
        done = _run("mnemo_render.py", "--export", str(self.export))
        self.assertEqual(done.returncode, 0, done.stderr)
        index = (self.export / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("2026-08-20", index)        # настоящая отметка цела
        self.assertIn("<без даты>", index)        # битая показана заглушкой

    def test_a_stub_date_never_shadows_a_real_one(self) -> None:
        # `<без даты>` сортируется выше настоящей даты: попади заглушка в
        # `last_raised`, «протухло» считалось бы от неё, и вопрос, спрошенный
        # месяц назад, выглядел бы свежим.
        ident = self.a_question()
        self.man("ask", "--id", ident, "--raised-to", "petr-ivanov",
                 "--raised-at", (date.today() - timedelta(days=30)).isoformat())
        manifest = self.manifest()
        for question in manifest["questions"]:
            if question["id"] == ident:
                question["raised"].append({"where": "чат"})   # ни to, ни at
        (self.export / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        out = self.audit().stdout
        self.assertIn("ПРОТУХЛО", out)
        self.assertIn("30 дн. назад", out)

    def test_the_write_path_refuses_instead_of_a_traceback(self) -> None:
        # §13: непроверяемое — ошибка, а не падение. `list("вчера")` разваливало
        # строку на буквы уже внутри контракта записи.
        ident = self.a_question()
        manifest = self.manifest()
        for question in manifest["questions"]:
            if question["id"] == ident:
                question["raised"] = "вчера"
        (self.export / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        done = self.man("ask", "--id", ident, "--impact", "что-то другое")
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("Traceback", done.stderr)
        self.assertIn("raised", done.stderr)


class ReadContract(ExportCase):
    def test_the_contract_version_is_three(self) -> None:
        self.assertEqual(self.audit_json()["query_contract"], "3")

    def test_a_broken_record_does_not_hide_the_rest(self) -> None:
        """`SPEC/QUERY.md`, «Устойчивость к повреждённой записи».

        Манифест могли править руками или сторонним кодом. Сводка обязана
        показать битую запись, а не упасть на ней: падение прячет и все
        остальные, а разбираться человеку — по выводу линтера.

        Ловушка здесь конкретная: у повреждённой записи `id` подменяется
        заглушкой «?», и разделение блокирующего на требования и вопросы по
        первой букве идентификатора отправило бы требование в ветку вопроса.
        """
        good = self.a_requirement(quote="выгрузка остатков раз в час",
                                  blocking="нет ключа")
        manifest = self.manifest()
        manifest["requirements"].append({"blocking": "тоже стоит"})  # ни id, ни quote
        manifest["questions"].append({"blocking": "и это"})
        (self.export / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        done = self.audit()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(good, done.stdout)
        self.assertEqual(self.audit("--json").returncode, 0)

    def test_the_standard_version_agrees_with_the_document(self) -> None:
        header = (ROOT / "SPEC" / "STANDARD.md").read_text(encoding="utf-8")
        self.assertIn(f"**Версия стандарта:** {SPEC_VERSION}", header)
        self.assertEqual(SPEC_VERSION, "1.16")


class SpecVersionIsRaised(ExportCase):
    """§14 п.1, сторона авто-подъёма: запись ЧЕРЕЗ КОМАНДУ поднимает версию.

    Смотреть надо на содержимое манифеста, а не на код возврата: команда с
    новым полем завершится успехом и при сломанной карте версий — она просто
    запишет поле, ничего не подняв.
    """

    def test_a_new_field_written_by_the_command_raises_the_declared_version(self) -> None:
        self.a_requirement(blocking="нет ключа", tried="написал в поддержку 20.08",
                           returned="молчат с 20.08",
                           dead_end="их админ обязан выдать ключ")
        declared = self.manifest()["mnemo_spec"]
        self.assertEqual(
            tuple(int(x) for x in declared.split(".")) >= (1, 16), True,
            f"манифест объявляет {declared}, хотя несёт поля 1.16 — "
            "карта required_spec не видит полей требования")

    def test_a_question_field_raises_the_declared_version_too(self) -> None:
        ident = self.a_question()
        self.man("ask", "--id", ident, "--assumed", "как в прошлых проектах",
                 "--cost-if-wrong", "переписать модуль оплаты, день")
        declared = self.manifest()["mnemo_spec"]
        self.assertEqual(tuple(int(x) for x in declared.split(".")) >= (1, 16), True,
                         f"манифест объявляет {declared}, хотя несёт поля 1.16")


class SpecVersionDriftIsCaughtByLinter(ExportCase):
    """§14 п.2, сторона линтера: манифест В ОБХОД КОМАНДЫ обязан дать V18.

    Обход обязателен, иначе проверка подтверждает сама себя. `save_manifest`
    перед каждой записью поднимает заявленную версию до `required_spec` —
    «чинить дрейф в момент возникновения дешевле, чем ловить линтером годы
    спустя». Тест, идущий через `req`/`ask`, самозалечится и пройдёт ОДИНАКОВО
    при починенной и при сломанной карте: он проверит авто-подъём, а не V18.

    Поэтому манифест здесь собирается записью файла напрямую: поле положено,
    `mnemo_spec` оставлен 1.15.
    """

    def hand_written(self, bucket: str, record: dict) -> None:
        manifest = self.manifest()
        manifest[bucket].append(record)
        manifest["mnemo_spec"] = "1.15"
        (self.export / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def v18(self) -> list[str]:
        report = json.loads(_run("mnemo_verify.py", "--export", str(self.export),
                                 "--json").stdout)
        return [e["message"] for e in report["errors"] if e["code"] == "V18"]

    def test_a_requirement_field_left_at_the_old_version_is_an_error(self) -> None:
        self.hand_written("requirements", {
            "id": "t001", "quote": "выгрузка остатков раз в час",
            "wanted_by": "petr-ivanov", "based_on": [], "state": "stated",
            "evidence": None, "blocking": "нет ключа", "blocking_since": None,
            "tried": "написал в поддержку 20.08", "returned": "молчат с 20.08",
            "dead_end": "их админ обязан выдать ключ",
            "stage": None, "supersedes": None, "note": None,
            "redactions": [], "date": "2026-08-20",
        })
        self.assertTrue(self.v18(),
                        "манифест несёт поля 1.16 и объявляет 1.15, а V18 молчит — "
                        "карта required_spec не видит полей требования")

    def test_a_question_field_left_at_the_old_version_is_an_error(self) -> None:
        self.hand_written("questions", {
            "id": "q001", "text": "точно ли эта платёжка",
            "impact": "какой SDK в интеграции", "blocking": None,
            "blocking_since": None,
            "self_attempt": "прочитал ТЗ и переписку за август",
            "tried": None, "returned": None, "dead_end": None,
            "assumed": None, "cost_if_wrong": None,
            "asked_of": "petr-ivanov", "based_on": ["ctx:priyomka#i001"],
            "raised": [], "answered_by": None, "dropped_reason": None,
            "redactions": [], "date": "2026-08-20",
        })
        self.assertTrue(self.v18(),
                        "манифест несёт поля 1.16 и объявляет 1.15, а V18 молчит — "
                        "карта required_spec не видит полей вопроса")


if __name__ == "__main__":
    unittest.main()
