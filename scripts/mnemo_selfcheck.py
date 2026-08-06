#!/usr/bin/env python3
"""Самопроверка плагина: не разошлись ли его собственные части.

Линтер проверяет **экспорты**. Эта проверка — про сам инструмент: спека, код,
навык и команды описывают одно и то же, и между ними тоже бывает дрейф.

Повод конкретный. Правило `V18` появилось из-за того, что манифест объявлял
версию 1.0, содержа разделы из 1.5. Через несколько дней ровно то же случилось
внутри спеки: `STANDARD.md` объявлял 1.7, `CITATION.md` — 1.5, `PROVENANCE.md` —
1.0. Три документа, три версии. Правило, написанное про чужие данные, не
покрывало свои.

Отсюда принцип, который эта проверка охраняет: **версию и списки объявляет одно
место, остальные ссылаются.**

Использование:
    mnemo_selfcheck.py            из корня плагина
    mnemo_selfcheck.py --root <путь>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import SPEC_VERSION  # noqa: E402

VERSION_RE = re.compile(r"^\*\*Версия стандарта:\*\*\s*([\d.]+)", re.M)
RULE_RE = re.compile(r"\bV(\d{2})\b")


PRIVATE_TERMS = Path.home() / ".config" / "mnemo" / "private-terms.txt"


def plugin_version(root: Path) -> str:
    try:
        return json.loads((root / ".claude-plugin" / "plugin.json")
                          .read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "?"


def query_contract(root: Path) -> str:
    try:
        audit = (root / "scripts" / "mnemo_audit.py").read_text(encoding="utf-8")
    except OSError:
        return "?"
    found = re.search(r'^QUERY_CONTRACT = "([^"]+)"', audit, re.M)
    return found.group(1) if found else "?"


def check_private_terms(root: Path) -> list[str]:
    """Настоящие имена в файлах, которые уедут в публичный репозиторий.

    Смотрим рабочее дерево, а не историю: попавшее в коммит убирается только
    переписыванием истории, и смысл проверки — не доводить до этого.
    """
    source = Path(os.environ.get("MNEMO_PRIVATE_TERMS") or PRIVATE_TERMS)
    if not source.is_file():
        return []
    terms = [line.strip() for line in source.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    if not terms:
        return []

    inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True)
    if inside.returncode != 0:
        # Не репозиторий — значит это установленная копия плагина, а не рабочее
        # дерево. Публиковать оттуда нечего, и проверка не про неё. Раньше здесь
        # печаталось «git не отдал список файлов», и самопроверка на установленной
        # копии всегда падала одним и тем же ложным расхождением — а проверка,
        # которая всегда красная, читается как «всё сломано» и перестаёт читаться.
        return []
    listing = subprocess.run(["git", "-C", str(root), "ls-files"],
                             capture_output=True, text=True)
    if listing.returncode != 0:
        return ["список запрещённых слов задан, но git не отдал список файлов — "
                "проверка не выполнена"]

    problems = []
    for name in listing.stdout.splitlines():
        path = root / name
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        for term in terms:
            if term.lower() in text:
                problems.append(
                    f"{name}: настоящее имя «{term}» — репозиторий публичный, "
                    "примеры обязаны быть обезличенными (CONTRIBUTING.md)"
                )
    return problems


def check(root: Path) -> list[str]:
    problems: list[str] = []
    spec = root / "SPEC"

    # 1. Версию объявляет только STANDARD.md, и она совпадает с кодом.
    declared: dict[str, str] = {}
    for doc in sorted(spec.glob("*.md")):
        found = VERSION_RE.findall(doc.read_text(encoding="utf-8"))
        if found:
            declared[doc.name] = found[0]
    if "STANDARD.md" not in declared:
        problems.append("SPEC/STANDARD.md не объявляет версию стандарта")
    extra = [n for n in declared if n != "STANDARD.md"]
    if extra:
        problems.append(
            "версию объявляет не только STANDARD.md, а ещё " + ", ".join(extra)
            + " — она разойдётся; ссылайтесь, а не дублируйте"
        )
    if declared.get("STANDARD.md") and declared["STANDARD.md"] != SPEC_VERSION:
        problems.append(
            f"STANDARD.md объявляет {declared['STANDARD.md']}, "
            f"а SPEC_VERSION в коде — {SPEC_VERSION}"
        )

    # 1а. Версия контракта чтения — тот же класс дрейфа, только своя шкала.
    # Она версионируется отдельно от стандарта (§16), значит и разойтись может
    # отдельно: код отдаёт одну, документ обещает другую, потребитель проверяет
    # major-версию и получает неверный ответ.
    query_doc = spec / "QUERY.md"
    audit = (root / "scripts" / "mnemo_audit.py").read_text(encoding="utf-8")
    in_code = re.search(r'^QUERY_CONTRACT = "([^"]+)"', audit, re.M)
    in_doc = re.search(r"^\*\*Версия контракта чтения:\*\*\s*`([^`]+)`",
                       query_doc.read_text(encoding="utf-8"), re.M) if query_doc.is_file() else None
    if not query_doc.is_file():
        problems.append("SPEC/QUERY.md отсутствует, а §16 объявляет контракт чтения")
    elif not in_code or not in_doc:
        problems.append("версия контракта чтения не объявлена в коде или в QUERY.md")
    elif in_code.group(1) != in_doc.group(1):
        problems.append(
            f"контракт чтения: код отдаёт {in_code.group(1)}, "
            f"QUERY.md обещает {in_doc.group(1)}"
        )

    # 2. Правила линтера: объявленные в стандарте и реализованные — одно и то же.
    standard = (spec / "STANDARD.md").read_text(encoding="utf-8")
    verifier = (root / "scripts" / "mnemo_verify.py").read_text(encoding="utf-8")
    in_spec = {m for m in RULE_RE.findall(standard)}
    # Ищем ровно вызовы report.error("VNN"…) / report.warn("VNN"…), а не любое
    # упоминание кода в файле. Прежняя проверка искала строку `"VNN"` где угодно,
    # и комментарий вида «правило "V21" пока не сделано» засчитывался как
    # реализация — самопроверка давала ложное «всё согласовано».
    implemented = set(re.findall(r'report\.(?:error|warn)\(\s*"V(\d{2})"', verifier))
    for code in sorted(in_spec - implemented):
        problems.append(f"V{code} описано в стандарте, но не реализовано")
    for code in sorted(implemented - in_spec):
        problems.append(f"V{code} реализовано, но в стандарте не описано")

    # 3. Префиксы записей: всё, что выдаёт next_id, описано в CITATION.md.
    core = (root / "scripts" / "mnemo_core.py").read_text(encoding="utf-8")
    citation = (spec / "CITATION.md").read_text(encoding="utf-8")
    prefixes = set(re.findall(r'\(\s*"([a-z])"\s*,\s*"\w+"\s*\)', core))
    for prefix in sorted(prefixes):
        if f"| `{prefix}` |" not in citation:
            problems.append(
                f"префикс `{prefix}` выдаётся кодом, но не описан в CITATION.md — "
                "ссылка возможна, а нормативного описания нет"
            )
    # И обратная сторона: описанный префикс, которого никто не выдаёт, обещает
    # ссылку, которую невозможно получить, и линтер будет её отвергать.
    for declared in set(re.findall(r"^\| `([a-z])` \|", citation, re.M)):
        if declared not in prefixes:
            problems.append(
                f"префикс `{declared}` описан в CITATION.md, но код его не выдаёт — "
                "документ обещает ссылку, которой не бывает"
            )

    # И третья сторона того же: примеры ссылок в тексте. Таблица префиксов может
    # быть верной, а пример рядом с ней — показывать префикс, которого нет.
    # Так и вышло: `d` убрали из таблицы, а `ctx:priyomka#d012` осталось строкой
    # ниже — и читатель учится формату, который линтер отвергнет. Проверка
    # таблиц этого не видит, потому что смотрит только на строки таблиц.
    for source in [*sorted(spec.glob("*.md")), root / "README.md",
                   *sorted((root / "commands").glob("*.md")),
                   *sorted((root / "skills").rglob("*.md"))]:
        for sample, prefix in set(re.findall(r"ctx:[\w-]+#(([a-z])\d+)",
                                             source.read_text(encoding="utf-8"))):
            if prefix not in prefixes:
                problems.append(
                    f"{source.name}: пример `#{sample}` использует префикс "
                    f"`{prefix}`, которого нет — читатель научится ссылке, "
                    "которой не бывает"
                )

    # Команды: README перечисляет ровно то, что лежит в commands/.
    readme = (root / "README.md").read_text(encoding="utf-8")
    on_disk = {f.stem for f in (root / "commands").glob("*.md")}
    in_readme = set(re.findall(r"`/mnemo:([a-z-]+)`", readme))
    for missing in sorted(on_disk - in_readme):
        problems.append(f"команда /mnemo:{missing} существует, но не указана в README")
    for phantom in sorted(in_readme - on_disk):
        problems.append(f"README обещает /mnemo:{phantom}, а файла команды нет")

    # Ссылки на разделы стандарта ведут в существующие разделы.
    sections = set(re.findall(r"^## (\d+)[а-я]?\.", standard, re.M))
    for source in (root / "SPEC").glob("*.md"):
        for ref in re.findall(r"§(\d+)", source.read_text(encoding="utf-8")):
            if ref not in sections:
                problems.append(f"{source.name} ссылается на §{ref}, которого нет в STANDARD.md")

    # 3а. Настоящие имена в публичной репе.
    #
    # Список лежит вне репозитория намеренно: перечень имён клиентов и людей,
    # положенный рядом с кодом, сам был бы утечкой — причём той же, от которой
    # защищает. Нет файла — проверка молча пропускается, и на посторонних
    # установках ничего не меняется.
    problems += check_private_terms(root)

    # 4. Команды: файл на каждую и версия плагина совпадает с манифестом рынка.
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    market_version = market["plugins"][0]["version"]
    if plugin["version"] != market_version:
        problems.append(
            f"plugin.json {plugin['version']} против marketplace.json {market_version}"
        )

    # 5. Скрипты, упомянутые в навыке, существуют.
    skill = (root / "skills" / "chat-export" / "SKILL.md").read_text(encoding="utf-8")
    for name in re.findall(r"`(mnemo_\w+\.py)`", skill):
        if not (root / "scripts" / name).is_file():
            problems.append(f"SKILL.md ссылается на несуществующий {name}")

    # 6. Всё компилируется.
    files = [str(p) for p in sorted((root / "scripts").rglob("*.py"))]
    result = subprocess.run(
        [sys.executable, "-W", "error::SyntaxWarning", "-m", "py_compile", *files],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        problems.append("скрипты не компилируются: " + result.stderr.strip().splitlines()[-1])

    return problems


def main() -> int:
    argp = argparse.ArgumentParser(description="Проверить внутреннюю согласованность плагина")
    argp.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = argp.parse_args()

    root = Path(args.root)
    # Версии печатаем всегда, до вердикта: главный вопрос при возврате к работе —
    # «а обновилась ли установленная копия или я смотрю на старую», и ответ на
    # него не должен зависеть от того, нашлись расхождения или нет. Путь тут же:
    # он содержит версию, и по нему видно, из какого каталога всё это взято.
    print(f"плагин {plugin_version(root)} · стандарт {SPEC_VERSION} · "
          f"контракт чтения {query_contract(root)}")
    print(f"откуда: {root}")

    problems = check(root)
    for line in problems:
        print(f"РАСХОЖДЕНИЕ: {line}")
    if problems:
        print(f"\n❌ расхождений: {len(problems)}")
        return 1
    print("✅ плагин согласован сам с собой")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
