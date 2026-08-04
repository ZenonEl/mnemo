#!/usr/bin/env python3
"""Запись в манифест: создание экспорта и добавление материала.

Манифест — источник истины о содержимом экспорта (SPEC/STANDARD.md §7).
Все изменения содержимого проходят через этот скрипт, чтобы правила контракта
проверялись в момент записи, а не только линтером постфактум.

Подкоманды:
    init        создать скелет экспорта
    remove      снять запись с учёта (вместо правки манифеста руками)
    add-file    положить файл в RAW и завести запись
    add-text    записать сообщение/транскрипт в raw/messages/
    add-gap     завести запись о материале, которого нет
    redact      зарегистрировать изъятие
    rehash      пересчитать хеши после легитимной замены файла
    req         требование заказчика: что от нас хотят
    ask         открытый вопрос: чего мы не знаем
    people      реестр людей: кто есть кто
    whois       опознать имя из источника
    show        показать манифест или отдельную запись (отладка, не контракт)
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
    ATTRIBUTIONS, CONTOURS, DEFAULT_EXPORT_DIR, FIDELITIES, REQUIREMENT_STATES, INDEX_NAME, MANIFEST_NAME, PERSON_ROLES,
    RAW_ZONES, REDACTION_REASONS,
    SOURCES, STATUSES, MnemoError, ensure_skeleton, empty_manifest, find_export,
    contained, find_item, load_manifest, message_filename, new_item, new_person,
    blocked_since, find_record, new_question, new_redaction, new_requirement,
    next_id, question_state,
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

    if "\n" in rule or "\r" in rule:
        return ("⚠️ в имени каталога перевод строки — правило исключения выразить "
                "нельзя. Переименуй каталог, иначе данные попадут в репозиторий")
    # gitignore трактует [ ] * ? как шаблон, ! и # как служебные. Без
    # экранирования `chat-export[1]` — обычное имя распакованной копии —
    # превращается в шаблон, который не совпадает ни с чем: правило
    # записывается, init рапортует «добавлено», а каталог не исключён.
    escaped = "".join("\\" + ch if ch in "*?[]!#\\" else ch for ch in rule)

    existing = exclude_file.read_text(encoding="utf-8") if exclude_file.is_file() else ""
    if f"/{escaped}/" in existing:
        return "правило уже есть в .git/info/exclude"
    with exclude_file.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"# mnemo: экспорт содержит рабочие данные, в историю не попадает\n")
        handle.write(f"/{escaped}/\n")
    # Путь к exclude показываем как есть: в worktree и подмодуле он лежит вне
    # рабочего каталога, и вычислять относительный путь бессмысленно.
    try:
        shown = exclude_file.relative_to(root).as_posix()
    except ValueError:
        shown = str(exclude_file)
    if not is_git_ignored(export):
        # Проверяем результат, а не факт записи: правило могло не сработать по
        # причине, которой мы не предусмотрели. Рапорт «добавлено» без проверки —
        # это обещание, а не гарантия.
        return (f"⚠️ правило записано в {shown}, но каталог всё ещё не исключён. "
                "Проверь вручную, иначе данные попадут в репозиторий")
    return f"добавлено в {shown}: /{escaped}/"


CLAUDE_MD_MARK = "<!-- mnemo:archive -->"


def _safe_span(text: str) -> str:
    """Имя, пригодное для вставки в чужой markdown.

    Имя каталога уезжает в `CLAUDE.md`, который читает каждая сессия, и приходит
    оно в том числе от третьих лиц: приёмка принимает каталоги вроде
    `ChatExport_*` из клиентского архива. Бэктик закрывает code-span, перевод
    строки выходит из него — и в файл с инструкциями для модели попадает
    произвольный текст, выглядящий как её собственные правила.
    """
    flat = " ".join(str(text).split())
    return flat.replace("`", "'")


def announce_in_claude_md(export: Path, slug: str) -> str:
    """Прописать архив в CLAUDE.md проекта.

    Пять замеров подряд показали одно: `INDEX.md` открывали ноль раз в четырёх
    независимых проектах. Не потому что он плох — потому что **сессия не знает,
    что архив существует**. Она читает CLAUDE.md на старте, и архива в этом
    списке нет.

    Вход в архив — действие один раз за сессию, и документ, читаемый один раз за
    сессию, ровно под это и подходит. Ведение он не чинит: правило «Основано на»
    уже лежало в CLAUDE.md и прожило сутки. Для ведения нужен хук, не текст.

    Ведём на `audit` и `findings-log`, а не на `INDEX.md`: возвращающейся сессии
    нужен не список материалов, а список выводов и открытого.
    """
    host = export.parent
    target = host / "CLAUDE.md"
    if target.is_symlink():
        # Дописывание пошло бы по ссылке — в файл вне проекта, о котором человек
        # ничего не узнает.
        return ("⚠️ CLAUDE.md — символическая ссылка, дописывать не буду: "
                "запись ушла бы за пределы проекта. Пропиши архив вручную")
    if target.exists() and not target.is_file():
        return "⚠️ CLAUDE.md не обычный файл — пропиши архив вручную"

    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    # Метка своя у каждого экспорта: общая помечала файл как обработанный, и
    # второй архив в том же проекте молча не объявлялся при рапорте «уже прописан».
    mark = f"<!-- mnemo:archive:{slug} -->"
    if mark in existing:
        return "уже прописан в CLAUDE.md"

    name = _safe_span(export.name)
    slug = _safe_span(slug)
    # Ссылка строится от СЛАГА, не от имени каталога. Раньше здесь стояло имя
    # каталога, и сгенерированный файл — первое, что читает свежая сессия, —
    # учил формату, которого нет в стандарте. Ссылки уезжают в issue и дейлики
    # и живут дольше архива, а линтер их не проверяет: тихая ошибка ровно того
    # класса, против которого проект и заведён.
    block = f"""
{mark}
## Архив контекста — `{name}/`

Дословный архив материалов проекта с провенансом: переписка, документы, скрины,
голосовые. Ведётся плагином mnemo.

**Начинать отсюда, а не с поиска по репе:**

- `{name}/summaries/findings-log.md` — что уже выяснено. Читать при возврате к работе.
- `/mnemo:audit` — всё ли сделано, как хотел заказчик; что блокирует; что не спрошено.
- `{name}/INDEX.md` — что вообще есть и откуда. Когда ищешь конкретный материал.

**Правила:**

- Цитировать: `ctx:{slug}#iNNN` (слаг экспорта, не имя каталога). Уровень достоверности обязателен — `digest`
  (конспект, машинная расшифровка) **нельзя** приводить как чьи-то слова.
- Пополнять командами `/mnemo:import`, `/mnemo:add-*`, `/mnemo:req`, `/mnemo:ask`.
  `MANIFEST.json` и `INDEX.md` руками не править.
- Требование заказчика в состоянии `done` обязано нести доказательство.
"""
    # Пишем в ЧУЖОЙ файл вне экспорта — значит отказ возможен и не должен ронять
    # уже созданный экспорт. Ведём себя как проверка git: сообщаем и продолжаем.
    try:
        with target.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(block)
    except OSError as exc:
        return (f"⚠️ не удалось дописать в {target.name}: {exc.strerror or exc}. "
                "Пропиши архив вручную, иначе сессии его не найдут")
    return f"архив прописан в {target.name} — сессии его увидят на старте"


def trash_file(path: Path) -> str:
    """Убрать файл в корзину. Безвозвратного удаления в инструменте нет."""
    if shutil.which("trash-put") is None:
        return "trash-cli не найден — файл оставлен на месте, убери вручную"
    try:
        subprocess.run(["trash-put", str(path)], check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"не удалось убрать в корзину: {exc}"
    return "в корзине (вернуть: trash-restore)"


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
    parser.add_argument("--attribution", choices=ATTRIBUTIONS, default="reliable",
                        help="надёжность авторства (§4а): копипаста из мессенджера — "
                             "всегда forwarder-shown")
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
        "attribution": args.attribution,
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
    claude_note = announce_in_claude_md(export, manifest["export"]["slug"])

    # Собираем производные сразу: пустой, но валидный экспорт лучше «почти
    # созданного», на котором линтер падает по V07.
    from mnemo_render import sync
    sync(export, rehash=False)

    print(f"экспорт создан: {export}")
    print(f"  slug: {manifest['export']['slug']}")
    print(f"  git:  {git_note}")
    print(f"  CLAUDE.md: {claude_note}")
    if ("прописан в CLAUDE.md" in claude_note
            and git_status(export.parent)[0] == "repo"
            and not is_git_ignored(export.parent / "CLAUDE.md")):
        # CLAUDE.md — обычный файл проекта и попадает в коммит. В нём теперь
        # стоит имя каталога экспорта, а оно часто совпадает с именем клиента.
        #
        # Проверка на репозиторий обязательна: `check-ignore` вне git отвечает
        # «не игнорируется», и предупреждение об утечке в историю печаталось
        # там, где истории нет вовсе — строкой ниже сообщения «хост-проект не
        # под git». Предупреждение, которое противоречит соседней строке, учит
        # не читать предупреждения.
        print("             ⚠️ CLAUDE.md отслеживается git — имя каталога уедет "
              "в историю репозитория. Проверь перед коммитом")
    if export.name != DEFAULT_EXPORT_DIR:
        # Однократная подсказка при создании, а не правило линтера: принятые
        # экспорты живут под историческими именами, и вечное предупреждение
        # на них обесценило бы остальные проверки.
        print(f"  имя:  рекомендуемое — `{DEFAULT_EXPORT_DIR}` "
              f"(у тебя `{export.name}`; это не ошибка, только единообразие)")
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

    # contained() отвергает абсолютные пути, возвращая None, — и проверка
    # «не None» пропускала абсолютный путь внутрь экспорта. Резолвим сами.
    vault_inside = False
    if args.vault_ref:
        candidate = Path(args.vault_ref).expanduser()
        resolved = candidate if candidate.is_absolute() else (export / candidate)
        vault_inside = resolved.resolve().is_relative_to(export.resolve())
    if vault_inside:
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
        # Изъятие может касаться не только материала: дословная цитата
        # требования содержит ровно те же данные, что и сообщение, из которого
        # она взята, и печатается сводкой. Раньше `redact --items t001` падал.
        _, target = find_record(manifest, item_id)
        target.setdefault("redactions", [])
        if record["id"] not in target["redactions"]:
            target["redactions"].append(record["id"])

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


def read_batch(path: Path) -> list[tuple[str, list[str]]]:
    """Список записей из файла: по одной на строку.

    Двадцать требований из одного ТЗ — это двадцать вызовов с одинаковыми
    хвостами, и на десятом начинаешь срезать углы. Срезанный угол здесь — это
    потерянная дословная формулировка, а спор всегда идёт о формулировке.

    Формат намеренно бедный: строка — это текст записи. Общие поля задаются
    флагами один раз. Необязательный хвост после `::` — ссылки `based_on` через
    запятую, потому что каждое требование обычно указывает на своё сообщение.
    Пустые строки и `#` пропускаются.
    """
    if not path.is_file():
        raise MnemoError(f"нет файла {path}")
    out: list[tuple[str, list[str]]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip().lstrip("-*").strip()
        if not text or text.startswith("#"):
            continue
        refs: list[str] = []
        if "::" in text:
            text, tail = text.split("::", 1)
            text = text.strip()
            refs = [r.strip() for r in tail.split(",") if r.strip()]
        if not text:
            raise MnemoError(f"{path}:{number}: пустая формулировка перед `::`")
        out.append((text, refs))
    if not out:
        raise MnemoError(f"{path}: ни одной записи")
    return out


def batch_plan(entries: list[tuple[str, list[str]]], existing: list[str],
               kind: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Что заведётся, а что уже есть.

    План перед записью — по образцу импорта: там же двадцать сообщений разом, и
    там же нельзя узнать после. Совпадением считаем точный текст: решать, что
    две разные формулировки — одно требование, инструмент не вправе.
    """
    known = {normalize_text(t) for t in existing}
    fresh, dupes = [], []
    seen: set[str] = set()
    for text, refs in entries:
        key = normalize_text(text)
        if key in known or key in seen:
            dupes.append(text)
            continue
        seen.add(key)
        fresh.append((text, refs))
    print(f"файл: {len(entries)} строк")
    print(f"заведётся: {len(fresh)}")
    if dupes:
        print(f"уже есть, пропускаются: {len(dupes)}")
        for text in dupes[:5]:
            print(f"    {text[:66]}")
        if len(dupes) > 5:
            print(f"    … и ещё {len(dupes) - 5}")
    print()
    for text, refs in fresh[:20]:
        tail = f"   → {', '.join(refs)}" if refs else ""
        print(f"  {kind}  {text[:62]}{tail}")
    if len(fresh) > 20:
        print(f"  … и ещё {len(fresh) - 20}")
    return fresh, dupes


def normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def cmd_req(args) -> int:
    """Требование: что от нас хотят, дословно и с доказательством."""
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    if getattr(args, "batch", None):
        if args.id:
            raise MnemoError("--batch и --id несовместимы: пакет заводит новые записи")
        if args.quote:
            raise MnemoError("--batch и --quote несовместимы: формулировки берутся из файла")
        if args.supersedes:
            # Отмена — отношение между двумя конкретными требованиями. Один
            # `--supersedes` на пакет означал бы, что двадцать новых записей
            # отменяют одну и ту же, и девятнадцать из них — неправда.
            raise MnemoError("--supersedes нельзя применить к пакету: отмена оформляется "
                             "поштучно через req --id")
        entries = read_batch(Path(args.batch).expanduser())
        fresh, _ = batch_plan(entries, [r["quote"] for r in manifest["requirements"]],
                              "требование")
        if not args.apply:
            print("\n— это план. Ничего не изменено. Повтори с --apply.")
            return 0
        if not fresh:
            print("\nНовых требований нет — манифест не тронут.")
            return 0
        for text, refs in fresh:
            record = new_requirement(
                id=next_id(manifest, "requirement"), quote=text,
                wanted_by=args.wanted_by,
                based_on=refs or [b.strip() for b in (args.based_on or "").split(",") if b.strip()],
                state=args.state or "stated", evidence=args.evidence,
                blocking=args.blocking, blocking_since=args.blocking_since,
                stage=args.stage, note=args.note, date=args.date,
            )
            manifest["requirements"].append(record)
            print(f"{record['id']}  {record['state']}  {record['quote'][:56]}")
        save_manifest(export, manifest)
        return 0

    if args.id:  # обновление состояния существующего
        record = next((r for r in manifest["requirements"] if r["id"] == args.id), None)
        if record is None:
            raise MnemoError(f"нет требования {args.id}")
        # Обновляем всё, что человек передал. Раньше цикл читал только часть
        # полей, и `--quote` при исправлении опечатки молча терялся: команда
        # отвечала успехом, а в манифесте оставалась старая формулировка.
        if args.state == "dropped" and not (args.note or record.get("note") or "").strip():
            # Симметрично снятию материала и снятию вопроса: причина обязательна.
            # Иначе блокирующее требование заказчика исчезает из отчёта молча.
            raise MnemoError(
                f"{record['id']}: снятие требования требует --note с причиной — "
                "почему оно больше не действует"
            )
        for field in ("quote", "wanted_by", "state", "evidence", "blocking",
                      "blocking_since", "stage", "supersedes", "note", "date"):
            value = getattr(args, field, None)
            if value is not None:
                record[field] = value
        if args.based_on:
            record["based_on"] = [b.strip() for b in args.based_on.split(",") if b.strip()]
        new_requirement(**record)  # перепроверка контракта после правки
        save_manifest(export, manifest)
        print(f"{record['id']}  {record['state']}  {record['quote'][:56]}")
        return 0

    if not args.quote:
        raise MnemoError("--quote обязателен: дословно, как было сказано")
    record = new_requirement(
        id=next_id(manifest, "requirement"), quote=args.quote,
        wanted_by=args.wanted_by, based_on=[b.strip() for b in (args.based_on or "").split(",") if b.strip()],
        state=args.state or "stated", evidence=args.evidence, blocking=args.blocking,
        blocking_since=args.blocking_since,
        stage=args.stage, supersedes=args.supersedes, note=args.note, date=args.date,
    )
    manifest["requirements"].append(record)
    save_manifest(export, manifest)
    print(f"{record['id']}  {record['state']}  {record['quote'][:56]}")
    return 0


def cmd_ask(args) -> int:
    """Открытый вопрос. Состояние выводится из содержимого, не хранится."""
    export = find_export(Path(args.export))
    manifest = load_manifest(export)

    if getattr(args, "batch", None):
        if args.id:
            raise MnemoError("--batch и --id несовместимы: пакет заводит новые записи")
        if args.text:
            raise MnemoError("--batch и --text несовместимы: вопросы берутся из файла")
        if args.raised_to or args.answered_by or args.dropped_reason:
            # Отметки «спросили», «ответили», «снят» относятся к конкретному
            # вопросу. Один флаг на пакет пометил бы разом двадцать вопросов
            # заданными — и следующая сводка молча перестала бы их поднимать.
            raise MnemoError("отметки --raised-to / --answered-by / --dropped-reason "
                             "к пакету не применяются: они про конкретный вопрос")
        entries = read_batch(Path(args.batch).expanduser())
        fresh, _ = batch_plan(entries, [q["text"] for q in manifest["questions"]], "вопрос")
        if not args.apply:
            print("\n— это план. Ничего не изменено. Повтори с --apply.")
            return 0
        if not fresh:
            print("\nНовых вопросов нет — манифест не тронут.")
            return 0
        for text, refs in fresh:
            record = new_question(
                id=next_id(manifest, "question"), text=text, impact=args.impact,
                blocking=args.blocking, blocking_since=args.blocking_since,
                asked_of=args.asked_of,
                based_on=refs or [b.strip() for b in (args.based_on or "").split(",") if b.strip()],
                date=args.date,
            )
            manifest["questions"].append(record)
            print(f"{record['id']}  {question_state(record)}  {record['text'][:56]}")
        save_manifest(export, manifest)
        return 0

    if args.id:
        record = next((q for q in manifest["questions"] if q["id"] == args.id), None)
        if record is None:
            raise MnemoError(f"нет вопроса {args.id}")
        if args.raised_to:
            # Отметка «спросили» — накопительная: вопрос могли поднимать дважды,
            # и это разные события, а не перезапись одного.
            record["raised"].append({
                "to": args.raised_to, "at": args.raised_at or today(),
                "where": args.where or "",
            })
        for field in ("text", "impact", "blocking", "blocking_since", "asked_of",
                      "answered_by", "dropped_reason", "date"):
            value = getattr(args, field, None)
            if value is not None:
                record[field] = value
        if args.based_on:
            record["based_on"] = [b.strip() for b in args.based_on.split(",") if b.strip()]
        new_question(**record)  # перепроверка контракта после правки
        save_manifest(export, manifest)
        print(f"{record['id']}  {question_state(record)}  {record['text'][:56]}")
        return 0

    if not args.text:
        raise MnemoError("--text обязателен")
    record = new_question(
        id=next_id(manifest, "question"), text=args.text, impact=args.impact,
        blocking=args.blocking, blocking_since=args.blocking_since, asked_of=args.asked_of,
        based_on=[b.strip() for b in (args.based_on or "").split(",") if b.strip()],
        date=args.date,
    )
    manifest["questions"].append(record)
    save_manifest(export, manifest)
    print(f"{record['id']}  {question_state(record)}  {record['text'][:56]}")
    return 0


def cmd_remove(args) -> int:
    """Снять запись с учёта.

    Существует ради того, чтобы никому не приходилось править MANIFEST.json
    руками: манифест пишется только инструментом, потому что только так правила
    контракта проверяются в момент записи, а не постфактум линтером.

    Идентификатор отправляется в `retired` и **больше не выдаётся**: ссылку на
    него могли уже записать снаружи архива.
    """
    export = find_export(Path(args.export))
    manifest = load_manifest(export)
    item = find_item(manifest, args.id)

    if not (args.reason or "").strip():
        raise MnemoError("--reason обязателен: почему запись снимается")

    path = export / item["raw_path"] if item.get("raw_path") else None
    has_file = bool(path and path.is_file())

    if has_file and not args.confirm:
        print(f"{item['id']}  {item['raw_path']}  [{item['fidelity']}, {item['status']}]")
        print("\nЗа записью стоит реальный файл. Снятие уберёт его из архива —")
        print("в корзину, обратимо. Повтори с --confirm, если это то, что нужно.")
        return 1

    moved = []
    if has_file:
        # Материал не уничтожается: пока человек не проверил результат, файл
        # остаётся единственной копией.
        for candidate in [path, *[export / d for d in item.get("derived_paths") or []]]:
            if candidate.is_file():
                note = trash_file(candidate)
                moved.append(f"{rel(export, candidate)} — {note}")

    manifest["items"] = [i for i in manifest["items"] if i["id"] != item["id"]]
    manifest.setdefault("retired", []).append({
        "id": item["id"],
        "was": item.get("raw_path"),
        "reason": args.reason,
        "date": today(),
    })
    save_manifest(export, manifest)
    from mnemo_render import sync
    sync(export, rehash=False)

    print(f"{item['id']} снят с учёта: {args.reason}")
    for line in moved:
        print(f"  {line}")
    print(f"  идентификатор {item['id']} больше не будет выдан")
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
        bucket, record = find_record(manifest, args.id)
        print(f"// {bucket}", file=sys.stderr)
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Запись в манифест mnemo")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="создать скелет экспорта")
    p_init.add_argument("--dir", required=True)
    p_init.add_argument("--slug", default=None,
                        help="устойчивый ключ экспорта, kebab-case; входит в ссылки ctx:<slug>#<id>")
    p_init.add_argument("--title", default=None, help="человекочитаемое название")
    p_init.add_argument("--project", default=None, help="slug проекта для фильтра")
    p_init.add_argument("--contour", choices=CONTOURS, default="work",
                        help="work — рабочее (по умолчанию); personal — личное; "
                             "public — разрешено к публикации (§11)")
    p_init.add_argument("--participants", default="",
                        help="отображаемые имена через запятую; полноценный реестр "
                             "заводится отдельно командой people")
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

    p_req = sub.add_parser("req", help="требование заказчика")
    p_req.add_argument("--export", default=".")
    p_req.add_argument("--id", default=None, help="обновить существующее")
    p_req.add_argument("--quote", default=None, help="дословно, как было сказано")
    p_req.add_argument("--wanted-by", dest="wanted_by", default=None, help="кто хочет (id из реестра)")
    p_req.add_argument("--based-on", dest="based_on", default="", help="ссылки через запятую")
    p_req.add_argument("--state", choices=REQUIREMENT_STATES, default=None)
    p_req.add_argument("--evidence", default=None, help="чем подтверждено (для done/verified)")
    p_req.add_argument("--blocking-since", dest="blocking_since", default=None,
                        help="с какого числа блокирует; по умолчанию дата записи")
    p_req.add_argument("--blocking", default=None, help="что стоит без этого")
    p_req.add_argument("--stage", default=None, help="к какому этапу относится")
    p_req.add_argument("--supersedes", default=None, help="какое требование отменяет")
    p_req.add_argument("--note", default=None, help="расхождения, оговорки")
    p_req.add_argument("--date", default=None)
    p_req.add_argument("--batch", default=None,
                    help="файл со списком: по записи на строку, необязательный хвост после `::` — ссылки based_on")
    p_req.add_argument("--apply", action="store_true",
                    help="выполнить пакет (иначе только план)")
    p_req.set_defaults(func=cmd_req)

    p_ask = sub.add_parser("ask", help="открытый вопрос")
    p_ask.add_argument("--export", default=".")
    p_ask.add_argument("--id", default=None, help="обновить существующий")
    p_ask.add_argument("--text", default=None)
    p_ask.add_argument("--impact", default=None, help="что меняется от ответа")
    p_ask.add_argument("--blocking-since", dest="blocking_since", default=None,
                        help="с какого числа блокирует; по умолчанию дата записи")
    p_ask.add_argument("--blocking", default=None, help="что стоит без ответа")
    p_ask.add_argument("--asked-of", dest="asked_of", default=None)
    p_ask.add_argument("--based-on", dest="based_on", default="", help="ссылки через запятую")
    p_ask.add_argument("--raised-to", dest="raised_to", default=None, help="отметить, что спросили у ...")
    p_ask.add_argument("--raised-at", dest="raised_at", default=None,
                       help="когда спросили; по умолчанию сегодня")
    p_ask.add_argument("--where", default=None, help="где спросили: issue, дейлик, чат")
    p_ask.add_argument("--answered-by", dest="answered_by", default=None, help="ссылка на ответ")
    p_ask.add_argument("--dropped-reason", dest="dropped_reason", default=None)
    p_ask.add_argument("--date", default=None)
    p_ask.add_argument("--batch", default=None,
                    help="файл со списком: по записи на строку, необязательный хвост после `::` — ссылки based_on")
    p_ask.add_argument("--apply", action="store_true",
                    help="выполнить пакет (иначе только план)")
    p_ask.set_defaults(func=cmd_ask)

    p_rm = sub.add_parser("remove", help="снять запись с учёта, не правя манифест руками")
    p_rm.add_argument("--export", default=".")
    p_rm.add_argument("--id", required=True)
    p_rm.add_argument("--reason", required=True, help="почему запись снимается")
    p_rm.add_argument("--confirm", action="store_true", help="подтвердить, если за записью есть файл")
    p_rm.set_defaults(func=cmd_remove)

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

    p_show = sub.add_parser(
        "show", help="показать манифест — отладочный просмотр, не контракт "
                     "чтения; для интеграции см. audit --json и SPEC/QUERY.md")
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
