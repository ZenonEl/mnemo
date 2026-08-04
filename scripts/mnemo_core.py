"""Общее ядро скриптов mnemo.

Только стандартная библиотека Python 3 — никаких зависимостей. Причина в
SPEC/STANDARD.md: экспорт должен читаться и проверяться где угодно, без установки
окружения.

Здесь живёт всё, что знает про формат манифеста. Остальные скрипты — тонкие
обёртки над этим модулем.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

SPEC_VERSION = "1.7"
SPEC_MAJOR = 1

MANIFEST_NAME = "MANIFEST.json"
INDEX_NAME = "INDEX.md"

SOURCES = (
    "telegram", "docx", "xlsx", "screenshot", "voice",
    "claude-chat", "daily", "web", "other",
)
FIDELITIES = ("verbatim", "reconstructed", "digest", "placeholder")
STATUSES = ("present", "missing", "unrecoverable")
CONTOURS = ("work", "personal", "public")
# Насколько можно доверять имени рядом с текстом. Ось, независимая от fidelity:
# текст может быть дословным, а подпись под ним — не того человека.
ATTRIBUTIONS = ("reliable", "forwarder-shown", "unknown")
# Отношение человека к архиву. Описывает факт связи, а не рабочий процесс:
# «с клиентом общаемся только через начальство» — это соглашение конкретной
# команды, ему место в conventions.md, а не в общем стандарте.
PERSON_ROLES = ("self", "colleague", "management", "client", "other")
# Состояние требования — суждение, которое делаем мы, поэтому пишется явно.
REQUIREMENT_STATES = ("stated", "accepted", "done", "verified", "dropped")
REDACTION_REASONS = ("pii", "off-topic", "leaked-internal", "client-confidential")

# Зона RAW по виду материала. Ключи — то, чем оперируют команды add-*.
RAW_ZONES = {
    "message": "raw/messages",
    "attachment": "raw/attachments",
    "screenshot": "raw/screenshots",
    "voice": "raw/voice",
}

EXTRACTED_DIR = "raw/attachments/_extracted-text"
FROM_DOCX_DIR = "raw/screenshots/from-docx"

# Рекомендуемое имя каталога экспорта. Не нормативное: приёмка принимает
# экспорт под любым именем, и правило здесь давало бы вечное предупреждение
# на каждом принятом каталоге с исторического названия.
DEFAULT_EXPORT_DIR = "_chat-export"

SUMMARY_DIR = "summaries"
REQUIRED_SUMMARIES = (
    "summaries/attachments-summary.md",
    "summaries/conventions.md",
    "summaries/findings-log.md",
    "summaries/redactions.md",
)
REQUIRED_FILES = (MANIFEST_NAME, INDEX_NAME) + REQUIRED_SUMMARIES

# Всё, что разрешено лежать в экспорте (§2 стандарта). Проверяется правилом V11.
ALLOWED_TOP = {MANIFEST_NAME, INDEX_NAME, "summaries", "raw"}


class MnemoError(Exception):
    """Ошибка формата или использования. Сообщение предназначено человеку."""


# --------------------------------------------------------------------------
# Слаги и имена
# --------------------------------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str) -> str:
    """Человеческое имя → безопасный slug: латиница, цифры, дефисы.

    Кириллица транслитерируется, а не выбрасывается: имена участников и названия
    документов в этом проекте по большей части русские, и `2026-07-21_ivan-leontev`
    читается, а `2026-07-21_` — нет.
    """
    text = text.strip().lower()
    out = []
    for ch in text:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        elif unicodedata.category(ch).startswith("L"):
            # прочие буквы (латиница с диакритикой и т.п.) — нормализуем
            folded = unicodedata.normalize("NFKD", ch)
            out.append("".join(c for c in folded if c.isascii() and c.isalnum()))
        else:
            out.append("-")
    slug = "".join(out)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unnamed"


def message_filename(day: str, author: str, label: str | None = None) -> str:
    """`YYYY-MM-DD_<author>[_<label>].md` — сортируемое и фильтруемое имя (§3).

    Метка нужна, когда за один день от одного автора приходит больше одного
    материала — на реальных данных это происходит сразу же. Без неё второй файл
    молча затёр бы первый, а молчаливая потеря RAW — худшее, что эта система
    может сделать.
    """
    base = f"{day}_{slugify(author)}"
    if label:
        base += f"_{slugify(label)}"
    return f"{base}.md"


# --------------------------------------------------------------------------
# Даты и хеши
# --------------------------------------------------------------------------

def today() -> str:
    return date.today().isoformat()


def parse_day(value: str) -> str:
    """Проверить `YYYY-MM-DD` и что дата не из будущего (правило V10)."""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise MnemoError(f"дата должна быть в формате YYYY-MM-DD, получено: {value!r}") from exc
    if parsed > date.today():
        raise MnemoError(f"дата из будущего: {value}")
    return parsed.isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Пути
# --------------------------------------------------------------------------

def rel(export: Path, path: Path) -> str:
    """Путь относительно корня экспорта, всегда через прямой слеш."""
    return path.resolve().relative_to(export.resolve()).as_posix()


def claim_path(path: Path) -> Path:
    """Свободное имя рядом с `path`, если оно занято.

    RAW неизменен, поэтому запись поверх существующего файла запрещена всегда —
    даже когда имя формируется автоматически. Наивная схема «при совпадении
    добавить метку» ломается на третьем совпадении и молча затирает второе:
    потеря материала, которую линтер увидит только как расхождение хеша, когда
    данных уже нет.
    """
    if not path.exists():
        return path
    for number in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise MnemoError(f"не смог подобрать свободное имя рядом с {path}")


def contained(root: Path, relative: str) -> Path | None:
    """Путь `relative` внутри `root` — или `None`, если он выводит наружу.

    Поля с именами файлов приходят из чужой выгрузки, а выгрузку присылают
    третьи лица: это ровно тот материал, ради которого инструмент существует.
    Абсолютный путь в таком поле молча отбрасывает базовый каталог, а цепочка
    `../` уводит куда угодно, докуда дотягивается процесс, — и посторонний файл
    оказывается в архиве неотличимым от настоящего вложения.
    """
    if not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def find_export(start: Path) -> Path:
    """Найти корень экспорта, поднимаясь вверх от `start`.

    Экспорт опознаётся по наличию манифеста — не по имени каталога, потому что
    имя у принятых экспортов может быть любым.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    raise MnemoError(
        f"не нашёл экспорт (нет {MANIFEST_NAME}) начиная от {start}. "
        "Сначала /mnemo:init"
    )


# --------------------------------------------------------------------------
# Манифест
# --------------------------------------------------------------------------

def empty_manifest(slug: str, title: str, project: str | None = None,
                   contour: str = "work", participants: list[str] | None = None) -> dict:
    if contour not in CONTOURS:
        raise MnemoError(f"contour должен быть одним из {CONTOURS}, получено {contour!r}")
    return {
        "mnemo_spec": SPEC_VERSION,
        "export": {
            "slug": slug,
            "title": title,
            "created": today(),
            "project": project,
            "contour": contour,
            "participants": participants or [],
        },
        "items": [],
        "redactions": [],
        "imports": [],
        "people": [],
        "retired": [],
        "requirements": [],
        "questions": [],
    }


# Раздел манифеста → версия стандарта, в которой он появился. Нужен, чтобы
# заявленная версия не расходилась с содержимым: манифест, объявляющий 1.0 и
# содержащий `retired`, вводит в заблуждение любого, кто его читает.
SECTION_SINCE = (
    ("imports", "1.1"),
    ("people", "1.2"),
    ("retired", "1.5"),
    ("requirements", "1.7"),
    ("questions", "1.7"),
)
ITEM_FIELD_SINCE = (("attribution", "1.1"),)


def _ver(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(value).split("."))
    except ValueError:
        return (0,)


def required_spec(manifest: dict) -> str:
    """Минимальная версия стандарта, которую манифест обязан объявлять.

    Определяется по фактическому содержимому, а не по тому, что записано:
    инструмент дописывает разделы в старые манифесты, и без этого версия
    быстро перестаёт соответствовать формату.
    """
    need = (1, 0)
    for section, since in SECTION_SINCE:
        if manifest.get(section):
            need = max(need, _ver(since))
    for field, since in ITEM_FIELD_SINCE:
        if any(field in item for item in manifest.get("items", [])):
            need = max(need, _ver(since))
    return ".".join(str(x) for x in need)


def load_manifest(export: Path) -> dict:
    path = export / MANIFEST_NAME
    if not path.is_file():
        raise MnemoError(f"нет {MANIFEST_NAME} в {export}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MnemoError(f"{MANIFEST_NAME} повреждён: {exc}") from exc

    spec = str(data.get("mnemo_spec", "0"))
    try:
        major = int(spec.split(".")[0])
    except ValueError as exc:
        raise MnemoError(f"нечитаемая версия стандарта: {spec!r}") from exc
    if major > SPEC_MAJOR:
        # §12 стандарта: отказаться, а не угадывать.
        raise MnemoError(
            f"манифест версии {spec} новее поддерживаемой {SPEC_VERSION}. Обнови mnemo."
        )
    data.setdefault("items", [])
    data.setdefault("redactions", [])
    data.setdefault("imports", [])
    data.setdefault("people", [])
    data.setdefault("retired", [])
    data.setdefault("requirements", [])
    data.setdefault("questions", [])
    return data


def save_manifest(export: Path, manifest: dict) -> None:
    """Записать манифест атомарно: сначала во временный файл, потом подменить.

    Обрыв на середине прямой записи оставил бы обрезанный JSON, а манифест —
    единственный источник истины об архиве, и восстановить его неоткуда:
    экспорт намеренно держится вне git, истории версий у него нет.
    `os.replace` в пределах одной файловой системы атомарен.
    """
    # Поднимаем заявленную версию, если содержимое её переросло. Чинить дрейф
    # в момент возникновения дешевле, чем ловить линтером годы спустя.
    needed = required_spec(manifest)
    if _ver(manifest.get("mnemo_spec", "1.0")) < _ver(needed):
        manifest["mnemo_spec"] = needed

    path = export / MANIFEST_NAME
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def next_id(manifest: dict, kind: str = "item") -> str:
    """Следующий свободный идентификатор: i001 / r001.

    Счётчик, а не ULID — скрипты обязаны работать на голой стандартной библиотеке,
    а сортировка нужна по `date`, не по идентификатору.
    """
    prefix, bucket = {
        "item": ("i", "items"),
        "redaction": ("r", "redactions"),
        "requirement": ("t", "requirements"),
        "question": ("q", "questions"),
    }[kind]
    used = 0
    # Отставленные идентификаторы учитываются наравне с живыми: §5 требует, что
    # id не переиспользуется. Ссылку на него могли записать в issue, в дейлик,
    # в сообщение коллеге — и после переиспользования она молча показывала бы
    # на другой материал.
    known = [str(r.get("id", "")) for r in manifest.get(bucket, [])]
    known += [str(r.get("id", "")) for r in manifest.get("retired", [])]
    for raw in known:
        if raw.startswith(prefix) and raw[1:].isdigit():
            used = max(used, int(raw[1:]))
    return f"{prefix}{used + 1:03d}"


def _dedupe(values: list[str] | None) -> list[str]:
    """Убрать повторы, сохранив порядок.

    Автор сообщения добавляется в участников автоматически и часто уже есть
    в переданном списке — под слегка другим написанием он всё равно попадёт
    дважды, но точные дубли ловим здесь.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values or []:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def new_item(**kwargs: Any) -> dict:
    """Единица хранения со всеми полями контракта (§5 стандарта).

    Все ключи присутствуют всегда: необязательность выражается значением `null`
    или пустым списком. Потребителю не нужно гадать, отсутствует поле или пусто.
    """
    item = {
        "id": kwargs["id"],
        "source": kwargs["source"],
        "origin": kwargs.get("origin") or "",
        "fidelity": kwargs["fidelity"],
        "fidelity_note": kwargs.get("fidelity_note"),
        "date": kwargs["date"],
        "imported": kwargs.get("imported") or today(),
        "participants": _dedupe(kwargs.get("participants")),
        "raw_path": kwargs.get("raw_path"),
        "sha256": kwargs.get("sha256"),
        "derived_paths": list(kwargs.get("derived_paths") or []),
        "attribution": kwargs.get("attribution", "reliable"),
        "status": kwargs.get("status", "present"),
        "summary_ref": kwargs.get("summary_ref"),
        "redactions": list(kwargs.get("redactions") or []),
        "tags": list(kwargs.get("tags") or []),
        "project": kwargs.get("project"),
        "contour": kwargs.get("contour", "work"),
    }
    validate_item(item)
    return item


def validate_item(item: dict) -> None:
    """Проверки, которые обязаны выполняться в момент записи, а не только линтером."""
    if item["source"] not in SOURCES:
        raise MnemoError(f"source должен быть одним из {SOURCES}, получено {item['source']!r}")
    if item["fidelity"] not in FIDELITIES:
        raise MnemoError(f"fidelity должен быть одним из {FIDELITIES}, получено {item['fidelity']!r}")
    if item["status"] not in STATUSES:
        raise MnemoError(f"status должен быть одним из {STATUSES}, получено {item['status']!r}")
    if item["contour"] not in CONTOURS:
        raise MnemoError(f"contour должен быть одним из {CONTOURS}, получено {item['contour']!r}")
    if item.get("attribution", "reliable") not in ATTRIBUTIONS:
        raise MnemoError(
            f"attribution должен быть одним из {ATTRIBUTIONS}, получено {item.get('attribution')!r}"
        )
    parse_day(item["date"])

    # §4 стандарта: всё, что не дословно, обязано объяснить почему.
    if item["fidelity"] != "verbatim" and not (item["fidelity_note"] or "").strip():
        raise MnemoError(
            f"{item['id']}: fidelity={item['fidelity']} требует непустой fidelity_note"
        )
    # §4а: ненадёжное авторство обязано объяснить себя — иначе через месяц
    # непонятно, чьи это слова, и пометка бесполезна.
    if item.get("attribution", "reliable") != "reliable" and not (item["fidelity_note"] or "").strip():
        raise MnemoError(
            f"{item['id']}: attribution={item['attribution']} требует fidelity_note "
            "с объяснением, чьё авторство под вопросом и почему"
        )
    # §5: различие missing / unrecoverable выражается путём, а не только словом.
    if item["status"] == "unrecoverable":
        if item["raw_path"] is not None:
            raise MnemoError(f"{item['id']}: при status=unrecoverable raw_path должен быть null")
        if item["fidelity"] != "placeholder":
            raise MnemoError(f"{item['id']}: при status=unrecoverable fidelity должен быть placeholder")
    elif item["raw_path"] is None:
        raise MnemoError(f"{item['id']}: raw_path обязателен при status={item['status']}")


def new_requirement(**kwargs: Any) -> dict:
    """Требование — чужая воля: что от нас хотят.

    Отличается от факта и решения авторством. Факт мы проверили, решение мы
    выбрали, а требование **высказал кто-то другой** — поэтому у него есть
    дословная цитата, автор и материал, в котором оно прозвучало.

    Поля `blocking` и `stage` подняты из живого `open-questions.md`: там вопросы
    сгруппированы по тому, стоит ли без них работа, и у каждого написано, что
    меняется от ответа. Без этой оси сводка возвращает сорок пунктов вперемешку
    и тонет в неважном.
    """
    record = {
        "id": kwargs["id"],
        "quote": kwargs["quote"],
        "wanted_by": kwargs.get("wanted_by"),
        "based_on": _dedupe(kwargs.get("based_on")),
        "state": kwargs.get("state", "stated"),
        "evidence": kwargs.get("evidence"),
        "blocking": kwargs.get("blocking"),
        "stage": kwargs.get("stage"),
        "supersedes": kwargs.get("supersedes"),
        "note": kwargs.get("note"),
        "date": kwargs.get("date") or today(),
    }
    if record["state"] not in REQUIREMENT_STATES:
        raise MnemoError(f"state должен быть одним из {REQUIREMENT_STATES}")
    if not str(record["quote"]).strip():
        raise MnemoError("quote обязателен: дословно, как было сказано")
    if record["state"] in ("done", "verified") and not (record["evidence"] or "").strip():
        # «Сделано» без доказательства — это мнение, а не отчёт. Ровно тот случай,
        # ради которого весь архив и заводился.
        raise MnemoError(
            f"{record['id']}: state={record['state']} требует --evidence — "
            "чем именно подтверждено, что сделано"
        )
    parse_day(record["date"])
    return record


def new_question(**kwargs: Any) -> dict:
    """Открытый вопрос. Состояние **выводится**, а не хранится.

    Хранимый статус «отвечено» — утверждение, которому нечем возразить.
    Выведенный из `answered_by` и `raised` — проверяемый факт: либо есть ссылка
    на ответ, либо нет.

    `raised` отвечает на «а это разве не спрашивали уже?»: без него следующая
    сводка поднимает вопрос заново как новый.
    """
    record = {
        "id": kwargs["id"],
        "text": kwargs["text"],
        "impact": kwargs.get("impact"),
        "blocking": kwargs.get("blocking"),
        "asked_of": kwargs.get("asked_of"),
        "based_on": _dedupe(kwargs.get("based_on")),
        "raised": list(kwargs.get("raised") or []),
        "answered_by": kwargs.get("answered_by"),
        "dropped_reason": kwargs.get("dropped_reason"),
        "date": kwargs.get("date") or today(),
    }
    if not str(record["text"]).strip():
        raise MnemoError("text обязателен")
    parse_day(record["date"])
    return record


def question_state(record: dict) -> str:
    """Состояние вопроса, выведенное из содержимого."""
    if record.get("dropped_reason"):
        return "dropped"
    if record.get("answered_by"):
        return "answered"
    if record.get("raised"):
        return "raised"
    return "open"


def superseded_ids(manifest: dict, bucket: str = "requirements") -> set[str]:
    """Идентификаторы, отменённые более поздними записями."""
    return {r["supersedes"] for r in manifest.get(bucket, []) if r.get("supersedes")}


def new_redaction(**kwargs: Any) -> dict:
    record = {
        "id": kwargs["id"],
        "reason": kwargs["reason"],
        "description": kwargs["description"],
        "scope": kwargs["scope"],
        "reversible": bool(kwargs.get("reversible", False)),
        "vault_ref": kwargs.get("vault_ref"),
        "date": kwargs.get("date") or today(),
    }
    if record["reason"] not in REDACTION_REASONS:
        raise MnemoError(
            f"reason должен быть одним из {REDACTION_REASONS}, получено {record['reason']!r}"
        )
    if not record["description"].strip():
        raise MnemoError("description обязателен: что изъято, не раскрывая изъятого")
    if record["reversible"] and not record["vault_ref"]:
        raise MnemoError(
            "обратимое изъятие обязано указать vault_ref — где лежит оригинал (вне экспорта)"
        )
    parse_day(record["date"])
    return record


def new_person(**kwargs: Any) -> dict:
    """Человек в реестре экспорта.

    Один и тот же человек в разных источниках выглядит по-разному: в Telegram
    он под отображаемым именем, в git — под логином, в разговоре — по имени.
    Без реестра поиск «что говорил X» находит одну треть, а ассистент не знает,
    что один из участников переписки — тот, с кем он сейчас разговаривает.
    """
    person = {
        "id": kwargs["id"],
        "display": kwargs["display"],
        "role": kwargs.get("role", "other"),
        "aliases": _dedupe(kwargs.get("aliases")),
        "handles": dict(kwargs.get("handles") or {}),
        "note": kwargs.get("note"),
    }
    if person["role"] not in PERSON_ROLES:
        raise MnemoError(f"role должен быть одним из {PERSON_ROLES}, получено {person['role']!r}")
    if not person["id"] or not person["display"]:
        raise MnemoError("у человека обязательны id и display")
    return person


def _norm_name(value: str) -> str:
    """Имя для сопоставления: без регистра, пробелов по краям и эмодзи.

    Отображаемые имена в мессенджерах обрастают значками («Эрми 🤍»,
    «Соловейко :D»), и один и тот же человек не должен раздваиваться из-за них.
    """
    kept = [ch for ch in value.strip().lower()
            if ch.isalnum() or ch.isspace() or ch in "_-@."]
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def resolve_person(manifest: dict, name: str) -> dict | None:
    """Найти человека по отображаемому имени, алиасу или хэндлу."""
    target = _norm_name(name)
    if not target:
        return None
    for person in manifest.get("people", []):
        candidates = [person["display"], person["id"], *person.get("aliases", [])]
        candidates += list(person.get("handles", {}).values())
        if any(_norm_name(str(c)) == target for c in candidates if c):
            return person
    return None


def unknown_names(manifest: dict, names) -> list[str]:
    """Имена, которых нет в реестре. Порядок сохраняется."""
    out, seen = [], set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        if resolve_person(manifest, name) is None:
            out.append(name)
    return out


def imported_keys(manifest: dict) -> set[str]:
    """Все ключи сообщений, уже попавших в экспорт.

    Основа инкрементального импорта: повторная выгрузка того же чата приносит
    и старое, и новое, и задваивать старое нельзя. Хранится в манифесте, а не
    рядом, потому что это такая же часть истины об экспорте, как и сами записи.
    """
    keys: set[str] = set()
    for batch in manifest.get("imports", []):
        keys.update(batch.get("keys") or [])
    return keys


def find_item(manifest: dict, item_id: str) -> dict:
    for item in manifest["items"]:
        if item["id"] == item_id:
            return item
    raise MnemoError(f"нет item с id={item_id}")


def all_tracked_paths(manifest: dict) -> set[str]:
    """Пути, учтённые манифестом: сами материалы и их производные.

    Используется правилом V03. Производные учитываются здесь, а не отдельными
    записями: у них та же достоверность и то же происхождение, что у родителя.
    """
    tracked: set[str] = set()
    for item in manifest["items"]:
        if item.get("raw_path"):
            tracked.add(item["raw_path"])
        tracked.update(item.get("derived_paths") or [])
    return tracked


def iter_raw_files(export: Path):
    """Все реальные файлы в raw/, кроме служебных."""
    raw_root = export / "raw"
    if not raw_root.is_dir():
        return
    for path in sorted(raw_root.rglob("*")):
        # Скрытые файлы тоже перечисляем: раньше точка в начале имени делала
        # материал невидимым для линтера, и внутри raw/ можно было держать
        # что угодно — включая «хранилище» изъятых персональных данных.
        if path.is_file() and path.name != f".{MANIFEST_NAME}.tmp":
            yield path


def ensure_skeleton(export: Path) -> None:
    """Создать зоны раскладки. Пустые зоны допустимы, но каркас единообразен."""
    for zone in RAW_ZONES.values():
        (export / zone).mkdir(parents=True, exist_ok=True)
    (export / SUMMARY_DIR).mkdir(parents=True, exist_ok=True)
