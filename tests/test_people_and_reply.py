"""Проверки, которые нельзя доверить линтеру.

Линтер проверяет **готовый архив**, самопроверка — согласованность плагина. Ни
то, ни другое не ловит поведение команд: что реестр дополняется, а не
затирается, и что снимок родителя не выдумывает автора. Обе дыры нашлись при
живом использовании, и обе стоили порчи данных.

Только стандартная библиотека — как и всё остальное в проекте:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from parsers.herald_inbox import reply_authorship  # noqa: E402
from mnemo_import import render_reply  # noqa: E402


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "mnemo_manifest.py"), *args],
        input=stdin, text=True, capture_output=True,
    )


class PeopleUpdate(unittest.TestCase):
    """Реестр наполняется по мере знакомства с проектом.

    Настоящее имя человека всплывает позже рабочего прозвища. Пока правки не
    было, оставалось завести дубль — и `whois` начинал возвращать первого
    попавшегося — либо лезть в манифест руками, что запрещено §7.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.export = self.dir / "e"
        run("init", "--dir", str(self.export), "--slug", "priyomka", "--title", "Приёмка")
        run("people", "--export", str(self.export), "--add",
            "--display", "Пётр Работа", "--role", "client")

    def people(self) -> list[dict]:
        data = json.loads((self.export / "MANIFEST.json").read_text(encoding="utf-8"))
        return data["people"]

    def test_alias_is_merged_and_the_rest_survives(self) -> None:
        done = run("people", "--export", str(self.export), "--update",
                   "--id", "petr-rabota", "--aliases", "Пётр Иванов")
        self.assertEqual(done.returncode, 0, done.stderr)
        person = self.people()[0]
        self.assertEqual(person["display"], "Пётр Работа")
        self.assertEqual(person["role"], "client")
        self.assertIn("Пётр Иванов", person["aliases"])

    def test_aliases_accumulate(self) -> None:
        run("people", "--export", str(self.export), "--update",
            "--id", "petr-rabota", "--aliases", "Пётр Иванов")
        run("people", "--export", str(self.export), "--update",
            "--id", "petr-rabota", "--aliases", "petr_i")
        self.assertEqual(self.people()[0]["aliases"], ["Пётр Иванов", "petr_i"])

    def test_repeating_the_same_update_changes_nothing(self) -> None:
        run("people", "--export", str(self.export), "--update",
            "--id", "petr-rabota", "--aliases", "Пётр Иванов")
        before = self.people()
        run("people", "--export", str(self.export), "--update",
            "--id", "petr-rabota", "--aliases", "Пётр Иванов")
        self.assertEqual(self.people(), before)

    def test_unknown_id_is_refused_and_creates_nobody(self) -> None:
        done = run("people", "--export", str(self.export), "--update",
                   "--id", "net-takogo", "--aliases", "Кто-то")
        self.assertEqual(done.returncode, 1)
        self.assertIn("нет человека", done.stderr)
        self.assertEqual(len(self.people()), 1)

    def test_an_alias_of_another_person_is_refused(self) -> None:
        run("people", "--export", str(self.export), "--add",
            "--display", "Анна Смирнова", "--role", "colleague")
        done = run("people", "--export", str(self.export), "--update",
                   "--id", "petr-rabota", "--aliases", "Анна Смирнова")
        self.assertEqual(done.returncode, 1)
        self.assertIn("уже указывает", done.stderr)
        self.assertEqual(self.people()[0]["aliases"], [])

    def test_role_is_not_silently_reset(self) -> None:
        """У --role было значение по умолчанию, и клиент становился other."""
        run("people", "--export", str(self.export), "--update",
            "--id", "petr-rabota", "--aliases", "Пётр Иванов")
        self.assertEqual(self.people()[0]["role"], "client")

    def test_update_without_id_does_not_create_implicitly(self) -> None:
        done = run("people", "--export", str(self.export), "--update",
                   "--display", "Некто")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(len(self.people()), 1)


class ReplyAuthorship(unittest.TestCase):
    """§4а.3: показанное имя объявляется автором только с подтверждением."""

    def test_message_kind_names_the_author(self) -> None:
        self.assertEqual(
            reply_authorship({"kind": "message", "author_name": "Пётр Иванов"}),
            ("Пётр Иванов", True),
        )

    def test_external_from_an_identified_user_is_confirmed(self) -> None:
        self.assertEqual(
            reply_authorship({"kind": "external", "origin_type": "user",
                              "origin_name": "Пётр Иванов"}),
            ("Пётр Иванов", True),
        )

    def test_external_from_a_hidden_sender_is_shown_but_not_claimed(self) -> None:
        name, confirmed = reply_authorship(
            {"kind": "external", "origin_type": "hidden_user", "origin_name": "Кто-то"}
        )
        self.assertEqual(name, "Кто-то")
        self.assertFalse(confirmed)

    def test_quote_only_has_no_author_at_all(self) -> None:
        self.assertEqual(reply_authorship({"kind": "quote"}), (None, False))


class ReplyRendering(unittest.TestCase):
    def test_the_quote_is_shown_verbatim(self) -> None:
        lines = render_reply(
            {"kind": "message", "message_id": 40, "date": "2026-08-14T10:30:00+00:00",
             "author_name": "Пётр Иванов", "text": "Полный текст родителя.",
             "quote": {"text": "согласовать смету"}},
            present={"40"},
        )
        body = "\n".join(lines)
        self.assertIn("«согласовать смету»", body)
        self.assertNotIn("Полный текст родителя", body)

    def test_a_parent_outside_the_batch_is_snapshotted(self) -> None:
        lines = render_reply(
            {"kind": "message", "message_id": 7, "date": "2026-08-13T09:00:00+00:00",
             "author_name": "Пётр Иванов", "text": "Старое сообщение до бота."},
            present=set(),
        )
        self.assertIn("Старое сообщение до бота.", "\n".join(lines))

    def test_a_parent_inside_the_batch_is_not_repeated(self) -> None:
        lines = render_reply(
            {"kind": "message", "message_id": 40, "author_name": "Пётр Иванов",
             "text": "Родитель лежит рядом."},
            present={"40"},
        )
        body = "\n".join(lines)
        self.assertNotIn("Родитель лежит рядом.", body)
        self.assertIn("ниже в этом же дне", body)

    def test_external_invents_neither_text_nor_author(self) -> None:
        body = "\n".join(render_reply(
            {"kind": "external", "message_id": 9, "origin_type": "hidden_user",
             "origin_name": "Кто-то"},
            present=set(),
        ))
        self.assertIn("показано имя: Кто-то", body)
        self.assertIn("вне захвата", body)

    def test_quote_only_stays_anonymous(self) -> None:
        body = "\n".join(render_reply(
            {"kind": "quote", "quote": {"text": "видимая цитата"}}, present=set()
        ))
        self.assertIn("«видимая цитата»", body)
        self.assertNotIn("автор", body.lower())

    def test_parent_text_is_escaped_like_any_foreign_text(self) -> None:
        body = "\n".join(render_reply(
            {"kind": "message", "message_id": 7, "text": "# не заголовок"},
            present=set(),
        ))
        self.assertNotIn("> # не заголовок", body)


if __name__ == "__main__":
    unittest.main()
