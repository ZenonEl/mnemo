#!/usr/bin/env python3
"""Публичный срез экспорта: то, что можно показать.

Отбор **детерминированный**, а не на усмотрение модели. Аргумент тот же, что и
для линтера, только цена ошибки выше: линтер ошибётся — архив будет неточен;
отбор ошибётся — клиентские данные уедут наружу, и это необратимо.

Правило одно и простое: наружу выходит **только** материал с `contour: public`.
Значение по умолчанию — `work`, и повысить контур может лишь человек, явным
решением. Инструмент никогда не повышает его сам.

Использование:
    mnemo_publish.py --export <dir> --out <dir>          показать план
    mnemo_publish.py --export <dir> --out <dir> --apply  собрать
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    MnemoError, find_export, load_manifest, save_manifest, today,
)

# Слои, которые выходят наружу всегда: они описывают порядок работы, а не
# содержание переписки.
ALWAYS_INCLUDED = ("summaries/conventions.md",)


def select(manifest: dict) -> tuple[list[dict], list[dict]]:
    public, held = [], []
    for item in manifest["items"]:
        (public if item.get("contour") == "public" else held).append(item)
    return public, held


def build(export: Path, out: Path, manifest: dict, public: list[dict]) -> dict:
    stats = Counter()
    out.mkdir(parents=True, exist_ok=True)

    for item in public:
        raw_path = item.get("raw_path")
        if not raw_path or item.get("status") != "present":
            stats["пропущено (нет файла)"] += 1
            continue
        source = export / raw_path
        if not source.is_file():
            stats["пропущено (нет файла)"] += 1
            continue
        target = out / raw_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        stats["материалов"] += 1
        for derived in item.get("derived_paths") or []:
            found = export / derived
            if found.is_file():
                copy_to = out / derived
                copy_to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(found, copy_to)
                stats["производных"] += 1

    for relative in ALWAYS_INCLUDED:
        found = export / relative
        if found.is_file():
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, target)
            stats["правил"] += 1

    # Манифест среза содержит только опубликованное. Изъятия и реестр людей
    # наружу не идут: первые описывают, что скрыто, вторые — кто есть кто.
    meta = dict(manifest["export"])
    meta["contour"] = "public"
    meta["title"] = f"{meta['title']} — публичный срез"
    meta["participants"] = []
    slim = {
        "mnemo_spec": manifest["mnemo_spec"],
        "export": meta,
        "items": public,
        "redactions": [],
        "imports": [],
        "people": [],
    }
    save_manifest(out, slim)

    # Срез — самостоятельный экспорт, а не куча файлов: §2 требует полного
    # состава. Раньше он выходил без обязательных сводок и падал по
    # собственному линтеру, при этом команда рапортовала успех.
    from mnemo_manifest import write_stubs
    from mnemo_render import sync
    (out / "summaries").mkdir(parents=True, exist_ok=True)
    write_stubs(out)
    sync(out, rehash=False)
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Собрать публичный срез экспорта")
    argp.add_argument("--export", default=".")
    argp.add_argument("--out", required=True, help="куда сложить срез (пустой каталог)")
    argp.add_argument("--apply", action="store_true", help="выполнить (иначе только план)")
    args = argp.parse_args()

    try:
        export = find_export(Path(args.export))
        out = Path(args.out).expanduser().resolve()
        if out.resolve().is_relative_to(export.resolve()):
            raise MnemoError("срез нельзя складывать внутрь самого экспорта")

        manifest = load_manifest(export)
        public, held = select(manifest)

        print(f"экспорт: {export}")
        print(f"срез:    {out}")
        print()
        print(f"выйдет наружу:      {len(public)}")
        print(f"останется закрытым: {len(held)}")
        by_contour = Counter(i.get("contour", "work") for i in held)
        for contour, count in by_contour.most_common():
            print(f"    {count:>4}  contour: {contour}")
        if manifest.get("redactions"):
            print(f"\nизъятий в архиве: {len(manifest['redactions'])} — наружу не идут")
        if manifest.get("people"):
            print(f"реестр людей: {len(manifest['people'])} — наружу не идёт")

        if not public:
            print("\nНичего с contour: public — публиковать нечего.")
            print("Контур повышается только явным решением человека, инструмент")
            print("его не поднимает. Проверь, что действительно можно показать.")
            return 0

        print("\nчто выйдет:")
        for item in public[:20]:
            print(f"  {item['id']}  {item.get('raw_path')}")
        if len(public) > 20:
            print(f"  … и ещё {len(public) - 20}")

        if not args.apply:
            print("\n— это план. Ничего не создано. Повтори с --apply.")
            return 0

        if out.exists() and any(out.iterdir()):
            raise MnemoError(f"{out} не пуст — укажи пустой каталог")

        stats = build(export, out, manifest, public)
        print("\n--- собрано ---")
        for key, count in sorted(stats.items()):
            print(f"  {key}: {count}")

        from mnemo_verify import check
        report = check(out)
        leaked = [i["id"] for i in load_manifest(out)["items"]
                  if i.get("contour") != "public"]
        if leaked:
            print(f"  ❌ в срез попало непубличное: {leaked}")
            return 1
        if not report.ok:
            # Инструмент, объявляющий поддержку стандарта, не считает архив
            # исправным при нарушении любого правила (§13). Рапортовать успех
            # на непроходящем срезе — прямое нарушение собственной гарантии.
            print(f"  ❌ срез не проходит линтер: ошибок {len(report.errors)}")
            for entry in report.errors[:6]:
                print(f"     {entry['code']}: {entry['message']}")
            print("\nСрез собран, но отдавать его нельзя — это дефект инструмента.")
            return 1
        print("  проверка среза: ✅")
        print(f"\nсрез готов: {out}")
        print("Просмотри его глазами перед тем, как отдавать: публикация необратима.")
        return 0

    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
