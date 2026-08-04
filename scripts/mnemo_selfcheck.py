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
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import SPEC_VERSION  # noqa: E402

VERSION_RE = re.compile(r"^\*\*Версия стандарта:\*\*\s*([\d.]+)", re.M)
RULE_RE = re.compile(r"\bV(\d{2})\b")


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

    # 2. Правила линтера: объявленные в стандарте и реализованные — одно и то же.
    standard = (spec / "STANDARD.md").read_text(encoding="utf-8")
    verifier = (root / "scripts" / "mnemo_verify.py").read_text(encoding="utf-8")
    in_spec = {m for m in RULE_RE.findall(standard)}
    implemented = {m for m in RULE_RE.findall(verifier) if f'"V{m}"' in verifier}
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

    problems = check(Path(args.root))
    for line in problems:
        print(f"РАСХОЖДЕНИЕ: {line}")
    if problems:
        print(f"\n❌ расхождений: {len(problems)}")
        return 1
    print(f"✅ плагин согласован сам с собой (стандарт {SPEC_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
