#!/usr/bin/env python3
"""Запись в манифест: создание экспорта и добавление материала.

Манифест — источник истины о содержимом экспорта (SPEC/STANDARD.md §7).
Все изменения содержимого проходят через этот скрипт, чтобы правила контракта
проверялись в момент записи, а не только линтером постфактум.

Подкоманды:
    init        создать скелет экспорта
    add-file    положить файл в RAW и завести запись
    add-text    записать сообщение/транскрипт в raw/messages/
    add-gap     завести запись о материале, которого нет
    redact      зарегистрировать изъятие
    rehash      пересчитать хеши после легитимной замены файла
    show        показать манифест или отдельную запись
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    CONTOURS, FIDELITIES, INDEX_NAME, MANIFEST_NAME, PERSON_ROLES, RAW_ZONES, REDACTION_REASONS,
    SOURCES, STATUSES, MnemoError, ensure_skeleton, empty_manifest, find_export,
    contained, find_item, load_manifest, message_filename, new_item, new_person,
    new_redaction, next_id,
    parse_day, rel, resolve_person, save_manifest, sha256_file, slugify, today,
)

EXTRACTABLE = {".docx", ".xlsx"}


# --------------------------------------------------------------------------
# git: экспорт не должен уехать в чужую репу
# --------------------------------------------------------------------------

def _git(path: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        # git недоступен как программа — это не «репозитория нет», это «мы не
        # смогли проверить». Различие принципиально: см. git_status().
        return 127, ""
    return result.returncode, result.stdout.strip()


def git_status(path: Path) -> tuple[str, Path | None]:
    """Состояние git вокруг пути: `("repo"|"none"|"unknown", корень)`.

    Раньше любой сбой `git` трактовался как «репозитория нет», и проверка
    приватности тихо выключалась. Но сбой бывает не только от отсутствия репы:
    `dubious ownership` на общей машине, git не в PATH, сломанные права — во
    всех этих случаях репозиторий есть, экспорт не исключён, а инструмент
    рапортует, что исключать нечего. Незнание должно выглядеть как незнание.
    """
    code, out = _git(path, "rev-parse", "--show-toplevel")
    if code == 0 and out:
        return "repo", Path(out)
    if code == 127:
        return "unknown", None
    # Штатный ответ git «здесь не репозиторий» — единственное, что считается «none».
    code2, _ = _git(path, "rev-parse", "--is-inside-work-tree")
    return ("none", None) if code2 != 0 else ("unknown", None)


def git_root(path: Path) -> Path | None:
    state, root = git_status(path)
    return root if state == "repo" else None


def git_dir(path: Path) -> Path | None:
    """Настоящий каталог `.git`.

    В подмодуле и в git-worktree `.git` — это **файл** со ссылкой, а не каталог.
    Наивное `root/".git"/"info"` там падает с NotADirectoryError, экспорт
    остаётся неисключённым, и рабочие данные уезжают в чужой репозиторий при
    первом же `git add -A`.
    """
    code, out = _git(path, "rev-parse", "--git-common-dir")
    if code != 0 or not out:
        code, out = _git(path, "rev-parse", "--git-dir")
    if code != 0 or not out:
        return None
    candidate = Path(out)
    if not candidate.is_absolute():
        candidate = (path / candidate).resolve()
    return candidate


def is_git_ignored(path: Path) -> bool:
    code, _ = _git(path.parent, "check-ignore", "-q", str(path))
    return code == 0


def exclude_from_git(export: Path) -> str:
    """Исключить каталог экспорта из git хост-проекта.

    Делается в `init`, до появления первого файла с данными — §11 стандарта.
    Пишем в `.git/info/exclude`, а не в `.gitignore`: экспорт живёт в чужом
    рабочем репозитории, и правило для личных данных не должно попадать в его
    историю и мешать остальным.
    """
    state, root = git_status(export)
    if state == "none":
        return "хост-проект не под git — исключать нечего"
    if state == "unknown" or root is None:
        return ("⚠️ НЕ УДАЛОСЬ ПРОВЕРИТЬ git — исключение не прописано. "
                "Убедись сам, что каталог не попадёт в репозиторий")
    if is_git_ignored(export):
        return "уже исключён из git"

    try:
        rule = export.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "экспорт вне дерева git-репозитория — исключать нечего"
    if rule in ("", "."):
        # Экспорт совпал с корнем репозитория: исключить репозиторий из самого
        # себя нельзя, и правило `/./` молча ничего не делает.
        return ("⚠️ экспорт находится в КОРНЕ git-репозитория — исключить нечего. "
                "Перенеси его в подкаталог, иначе данные попадут в историю")

    git_home = git_dir(export)
    if git_home is None:
        return "⚠️ не нашёл каталог .git — исключение не прописано, проверь сам"
    exclude_file = git_home / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)

    existing = exclude_file.read_text(encoding="utf-8") if exclude_file.is_file() else ""
    if f"/{rule}/" in existing or f"\n{rule}/" in existing:
        return "правило уже есть в .git/info/exclude"
    with exclude_file.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"# mnemo: экспорт содержит рабочие данные, в историю не попадает\n")
        handle.write(f"/{rule}/\n")
    # Путь к exclude показываем как есть: в worktree и подмодуле он лежит вне
    # рабочего каталога, и вычислять относительный путь бессмысленно.
    try:
        shown = exclude_file.relative_to(root).as_posix()
    except ValueError:
        shown = str(exclude_file)
    return f"добавлено в {shown}: /{rule}/"


# --------------------------------------------------------------------------
# Заглушки summaries
# --------------------------------------------------------------------------

STUBS = {
    "summaries/attachments-summary.md": (
        "# Пересказ вложений\n\n"
        "_Пусто. Заполняется по мере добавления документов: что за документ, "
        "какие решения в нём зафиксированы, на что свериться при работе._\n"
    ),
    "summaries/conventions.md": (
        "# Рабочие правила\n\n"
        "_Правила, выведенные из переписки. Каждая запись обязана нести источник: "
        "кто сказал, когда, где это лежит в `raw/`._\n"
    ),
    "summaries/findings-log.md": (
        "# Findings log\n\n"
        "_Проверенные факты. У каждой записи статус: `open` (зафиксирован), "
        "`distributed` (донесён — куда и чем), `dropped` (снят — почему)._\n"
    ),
}


def write_stubs(export: Path) -> None:
    for path, body in STUBS.items():
        target = export / path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------
# Общие флаги метаданных
# --------------------------------------------------------------------------

def add_meta_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--origin", default="", help="откуда физически взято")
    parser.add_argument("--fidelity", choices=FIDELITIES, required=True)
    parser.add_argument("--note", default=None, help="fidelity_note; обязателен если fidelity != verbatim")
    parser.add_argument("--date", default=None, help="дата материала YYYY-MM-DD (по умолчанию сегодня)")
    parser.add_argument("--participants", default="", help="через запятую")
    parser.add_argument("--tags", default="", help="через запятую")
    parser.add_argument("--project", default=None)
    parser.add_argument("--contour", choices=CONTOURS, default="work")
    parser.add_argument("--summary-ref", default=None)


def meta_from_args(args) -> dict:
    return {
        "source": args.source,
        "origin": args.origin,
        "fidelity": args.fidelity,
        "fidelity_note": args.note,
        "date": parse_day(args.date) if args.date else today(),
        "participants": [p.strip() for p in args.participants.split(",") if p.strip()],
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()],
        "project": args.project,
        "contour": args.contour,
        "summary_ref": args.summary_ref,
    }


# --------------------------------------------------------------------------
# Подкоманды
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    export = Path(args.dir).resolve()
    if (export / MANIFEST_NAME).is_file():
        raise MnemoError(
            f"в {export} уже есть {MANIFEST_NAME}. "
            "Для приёмки существующего экспорта используй /mnemo:init — он покажет план."
        )
    export.mkdir(parents=True, exist_ok=True)
    ensure_skeleton(export)
    write_stubs(export)

    manifest = empty_manifest(
        slug=args.slug or slugify(export.name),
        title=args.title or export.name,
        project=args.project,
        contour=args.contour,
        participants=[p.strip() for p in (args.participants or "").split(",") if p.strip()],
    )
    save_manifest(export, manifest)

    # Исключаем из git до того, как в каталог попал первый файл с данными (§11
    # стандарта). Порядок здесь — не стилистика: наоборот было бы окном, в
    # котором рабочий материал уже лежит в отслеживаемом каталоге.
    git_note = exclude_from_git(export)

    # Собираем производные сразу: пустой, но валидный экспорт лучше «почти
    # созданного», на котором линтер падает по V07.
    from mnemo_render import sync
    sync(export, rehash=False)

    print(f"экспорт создан: {export}")
    print(f"  slug: {manifest['export']['slug']}")
    print(f"  git:  {git_note}")
    return 0


def cmd_add_file(args) -> int:
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    zone = RAW_ZONES[args.kind]
    source = Path(args.file).expanduser().resolve()
    if not source.is_file():
        raise MnemoError(f"нет файла: {source}")

    name = args.name or source.name
    if Path(name).name != name or name in (".", ".."):
        # `--name '../../../ключ.txt'` копировал файл наружу, в хост-репозиторий,
        # и падал уже ПОСЛЕ записи — данные оказывались вне исключённого каталога,
        # а линтер их не видел вовсе.
        raise MnemoError(
            f"--name должен быть именем файла, а не путём: получено {name!r}. "
            "Материал кладётся только внутрь зоны экспорта."
        )
    target = export / zone / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # §8б.1: запись поверх существующего файла запрещена всегда. Флага
        # «всё равно перезаписать» здесь нет намеренно: он уничтожал оригинал
        # безвозвратно, а линтер замечал это лишь по расхождению хеша, когда
        # данных уже не было. Нужна другая копия — дай ей другое имя.
        raise MnemoError(
            f"{rel(export, target)} уже существует. RAW не перезаписывается: "
            "положи под другим именем через --name, либо убедись, что материал "
            "уже в архиве."
        )

    # Метаданные проверяем ДО записи: иначе негодная дата оставляет на диске
    # файл, о котором манифест не знает.
    meta = meta_from_args(args)

    # copy2 сохраняет содержимое байт-в-байт и время — §3 стандарта запрещает
    # любую конвертацию и пересжатие.
    shutil.copy2(source, target)

    derived: list[str] = []
    if args.extract and target.suffix.lower() in EXTRACTABLE:
        from mnemo_extract import extract
        derived = extract(export, target)["derived_paths"]

    item = new_item(
        id=next_id(manifest),
        raw_path=rel(export, target),
        sha256=sha256_file(target),
        derived_paths=derived,
        status="present",
        **meta,
    )
    manifest["items"].append(item)
    save_manifest(export, manifest)

    print(f"{item['id']}  {item['raw_path']}  [{item['fidelity']}]")
    for path in derived:
        print(f"        → {path}")
    return 0


def cmd_add_text(args) -> int:
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    if args.from_file:
        body = Path(args.from_file).expanduser().read_text(encoding="utf-8")
    else:
        body = sys.stdin.read()
    if not body.strip():
        raise MnemoError("пустой текст — нечего записывать")

    meta = meta_from_args(args)
    day = parse_day(args.date) if args.date else today()
    target = export / RAW_ZONES["message"] / message_filename(day, args.author, args.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # Автоматический суффикс дал бы `..._zenonel-2.md` — имя, которое ничего
        # не говорит. Просим метку: она попадёт в имя файла и останется полезной.
        raise MnemoError(
            f"{rel(export, target)} уже существует. За один день от одного автора "
            "может быть несколько материалов — добавь --label, например "
            "--label handover. RAW не перезаписывается (§8б.1)."
        )
    target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")

    meta["date"] = day
    if args.author not in meta["participants"]:
        meta["participants"].insert(0, args.author)

    item = new_item(
        id=next_id(manifest),
        raw_path=rel(export, target),
        sha256=sha256_file(target),
        status="present",
        **meta,
    )
    manifest["items"].append(item)
    save_manifest(export, manifest)
    print(f"{item['id']}  {item['raw_path']}  [{item['fidelity']}]")
    return 0


def cmd_add_gap(args) -> int:
    """Материал, которого нет. Пропуск обязан быть виден — §3 п.5 стандарта."""
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    meta = meta_from_args(args)
    meta["fidelity"] = "placeholder"
    if not (meta["fidelity_note"] or "").strip():
        raise MnemoError("--note обязателен: чего именно не хватает и почему")

    if args.status == "unrecoverable":
        raw_path = None
    else:
        if not args.expected_path:
            raise MnemoError("--expected-path обязателен при status=missing: куда положить, когда достанем")
        # Путь наружу превращал бы `sync` в средство втянуть в архив
        # произвольный файл с диска: запись перешла бы в `present`, а INDEX стал
        # бы ссылаться за пределы экспорта.
        raw_path = args.expected_path
        if contained(export, raw_path) is None:
            raise MnemoError(
                f"--expected-path выводит за пределы экспорта: {raw_path!r}. "
                "Ожидаемый путь должен лежать внутри raw/."
            )
        if not raw_path.startswith("raw/"):
            raise MnemoError(
                f"--expected-path должен начинаться с raw/: получено {raw_path!r}"
            )

    item = new_item(
        id=next_id(manifest),
        raw_path=raw_path,
        sha256=None,
        status=args.status,
        **meta,
    )
    manifest["items"].append(item)
    save_manifest(export, manifest)
    print(f"{item['id']}  [{item['status']}]  {item['fidelity_note']}")
    return 0


def cmd_redact(args) -> int:
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    if args.vault_ref and contained(export, args.vault_ref) is not None:
        raise MnemoError(
            f"--vault-ref указывает внутрь экспорта: {args.vault_ref!r}. "
            "Изъятое и архив не хранятся вместе — иначе изъятие бессмысленно."
        )
    record = new_redaction(
        id=next_id(manifest, "redaction"),
        reason=args.reason,
        description=args.description,
        scope=args.scope,
        reversible=args.reversible,
        vault_ref=args.vault_ref,
        date=args.date,
    )
    manifest["redactions"].append(record)

    for item_id in [i.strip() for i in (args.items or "").split(",") if i.strip()]:
        item = find_item(manifest, item_id)
        if record["id"] not in item["redactions"]:
            item["redactions"].append(record["id"])

    save_manifest(export, manifest)
    from mnemo_render import sync
    sync(export, rehash=False)
    print(f"{record['id']}  {record['reason']}  {record['description']}")
    return 0


def cmd_rehash(args) -> int:
    """Пересчитать хеши.

    Нужно после легитимной замены файла — например, когда доложили оригинал,
    который раньше был `missing`. Расхождение хеша без rehash — это ошибка V02,
    и она должна оставаться ошибкой: RAW не редактируют.
    """
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    pending = []
    for item in manifest["items"]:
        if not item.get("raw_path"):
            continue
        path = export / item["raw_path"]
        if not path.is_file():
            continue
        digest = sha256_file(path)
        if item.get("sha256") != digest:
            pending.append((item, digest))

    if not pending:
        print("расхождений нет — обновлять нечего")
        return 0

    print("расхождение хеша означает, что RAW изменился. Это либо доложенный")
    print("оригинал, либо подмена. Обновление стирает улику, поэтому требует")
    print("подтверждения и оставляет след в записи.\n")
    for item, _ in pending:
        was = "ждал файла" if item["status"] == "missing" else "БЫЛ present"
        print(f"  {item['id']}  {item['raw_path']}  ({was})")

    if not args.confirm:
        print("\n— ничего не изменено. Повтори с --confirm, если это законная замена.")
        return 1

    if not (args.reason or "").strip():
        raise MnemoError("--reason обязателен: чем объясняется замена RAW")

    for item, digest in pending:
        item["sha256"] = digest
        item["status"] = "present"
        note = (item.get("fidelity_note") or "").strip()
        stamp = f"[{today()}] RAW заменён, хеш обновлён: {args.reason}"
        item["fidelity_note"] = f"{note}; {stamp}" if note else stamp
        print(f"{item['id']}  хеш обновлён, след записан")
    save_manifest(export, manifest)
    from mnemo_render import sync
    sync(export, rehash=False)
    print(f"обновлено записей: {len(pending)}")
    return 0


def cmd_people(args) -> int:
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    if not args.add:
        if not manifest["people"]:
            print("реестр пуст — заведи людей через people --add")
            return 0
        for person in manifest["people"]:
            handles = " ".join(f"{k}:{v}" for k, v in person["handles"].items())
            print(f"{person['id']:<14} {person['role']:<11} {person['display']}")
            if person["aliases"]:
                print(f"{'':<14} также: {', '.join(person['aliases'])}")
            if handles:
                print(f"{'':<14} {handles}")
        return 0

    person_id = args.id or slugify(args.display)
    if any(p["id"] == person_id for p in manifest["people"]):
        raise MnemoError(f"человек с id={person_id} уже есть; используй другой --id")

    # Оператор архива в единственном числе: иначе непонятно, чьи это «мои слова».
    if args.role == "self":
        existing = [p["id"] for p in manifest["people"] if p["role"] == "self"]
        if existing:
            raise MnemoError(f"роль self уже занята: {existing[0]}")

    # Алиас, который уже указывает на другого человека, делает реестр
    # бесполезным: `whois` молча вернёт первого попавшегося, и материал
    # припишется не тому. Лучше отказать сразу.
    proposed = [args.display, person_id]
    proposed += [a.strip() for a in (args.aliases or "").split(",") if a.strip()]
    for name in proposed:
        clash = resolve_person(manifest, name)
        if clash is not None:
            raise MnemoError(
                f"«{name}» уже указывает на {clash['display']} ({clash['id']}). "
                "Выбери другой алиас или допиши его тому человеку."
            )

    handles = {}
    for key in ("github", "telegram", "email"):
        value = getattr(args, key, None)
        if value:
            handles[key] = value

    person = new_person(
        id=person_id, display=args.display, role=args.role,
        aliases=[a.strip() for a in (args.aliases or "").split(",") if a.strip()],
        handles=handles, note=args.note,
    )
    manifest["people"].append(person)
    save_manifest(export, manifest)
    from mnemo_render import sync
    sync(export, rehash=False)
    print(f"{person['id']}  {person['role']}  {person['display']}")
    if person["aliases"]:
        print(f"  также: {', '.join(person['aliases'])}")
    return 0


def cmd_whois(args) -> int:
    export = find_export(Path(args.export))
    manifest = load_manifest(export)
    person = resolve_person(manifest, args.name)
    if person is None:
        print(f"{args.name}: в реестре нет")
        return 1
    print(json.dumps(person, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args) -> int:
    export = find_export(Path(args.export))
    manifest = load_manifest(export)
    if args.id:
        print(json.dumps(find_item(manifest, args.id), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Запись в манифест mnemo")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="создать скелет экспорта")
    p_init.add_argument("--dir", required=True)
    p_init.add_argument("--slug", default=None)
    p_init.add_argument("--title", default=None)
    p_init.add_argument("--project", default=None)
    p_init.add_argument("--contour", choices=CONTOURS, default="work")
    p_init.add_argument("--participants", default="")
    p_init.set_defaults(func=cmd_init)

    p_file = sub.add_parser("add-file", help="положить файл в RAW")
    p_file.add_argument("--export", default=".")
    p_file.add_argument("--kind", choices=sorted(RAW_ZONES), required=True)
    p_file.add_argument("--file", required=True)
    p_file.add_argument("--name", default=None, help="имя в RAW, если отличается от исходного")
    p_file.add_argument("--extract", action="store_true", help="для docx/xlsx извлечь текст и картинки")
    add_meta_args(p_file)
    p_file.set_defaults(func=cmd_add_file)

    p_text = sub.add_parser("add-text", help="записать сообщение в raw/messages/")
    p_text.add_argument("--export", default=".")
    p_text.add_argument("--author", required=True)
    p_text.add_argument("--label", default=None,
                        help="различитель в имени файла, если за день от автора не один материал")
    p_text.add_argument("--from-file", dest="from_file", default=None, help="откуда взять текст (иначе stdin)")
    add_meta_args(p_text)
    p_text.set_defaults(func=cmd_add_text)

    p_gap = sub.add_parser("add-gap", help="завести запись о недостающем материале")
    p_gap.add_argument("--export", default=".")
    p_gap.add_argument("--status", choices=["missing", "unrecoverable"], required=True)
    p_gap.add_argument("--expected-path", default=None, help="куда положить, когда достанем (для missing)")
    add_meta_args(p_gap)
    p_gap.set_defaults(func=cmd_add_gap)

    p_red = sub.add_parser("redact", help="зарегистрировать изъятие")
    p_red.add_argument("--export", default=".")
    p_red.add_argument("--reason", choices=REDACTION_REASONS, required=True)
    p_red.add_argument("--description", required=True, help="что изъято, не раскрывая изъятого")
    p_red.add_argument("--scope", required=True)
    p_red.add_argument("--reversible", action="store_true")
    p_red.add_argument("--vault-ref", default=None, help="где лежит оригинал — вне экспорта")
    p_red.add_argument("--items", default="", help="id записей через запятую")
    p_red.add_argument("--date", default=None)
    p_red.set_defaults(func=cmd_redact)

    p_hash = sub.add_parser("rehash", help="пересчитать хеши после легитимной замены")
    p_hash.add_argument("--export", default=".")
    p_hash.add_argument("--confirm", action="store_true", help="подтвердить замену RAW")
    p_hash.add_argument("--reason", default=None, help="чем объясняется замена")
    p_hash.set_defaults(func=cmd_rehash)

    p_people = sub.add_parser("people", help="реестр людей: кто есть кто")
    p_people.add_argument("--export", default=".")
    p_people.add_argument("--add", action="store_true")
    p_people.add_argument("--id", default=None, help="устойчивый ключ; по умолчанию из display")
    p_people.add_argument("--display", default=None, help="как называть в отчётах")
    p_people.add_argument("--role", choices=PERSON_ROLES, default="other")
    p_people.add_argument("--aliases", default="", help="через запятую: как он выглядит в источниках")
    p_people.add_argument("--github", default=None)
    p_people.add_argument("--telegram", default=None)
    p_people.add_argument("--email", default=None)
    p_people.add_argument("--note", default=None)
    p_people.set_defaults(func=cmd_people)

    p_who = sub.add_parser("whois", help="кто скрывается за именем из источника")
    p_who.add_argument("--export", default=".")
    p_who.add_argument("name")
    p_who.set_defaults(func=cmd_whois)

    p_show = sub.add_parser("show", help="показать манифест")
    p_show.add_argument("--export", default=".")
    p_show.add_argument("--id", default=None)
    p_show.set_defaults(func=cmd_show)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
