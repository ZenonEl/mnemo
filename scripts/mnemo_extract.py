#!/usr/bin/env python3
"""Извлечение текста и вшитых изображений из офисных документов.

`.docx` и `.xlsx` — это zip-архивы с XML внутри. Текст и картинки достаются
стандартной библиотекой, без внешних пакетов.

Результат — производные (`derived_paths` родительской записи), а не самостоятельный
материал: та же достоверность, то же происхождение, пересобираемы из оригинала.

Использование:
    mnemo_extract.py --export <dir> --file raw/attachments/doc.docx [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnemo_core import (  # noqa: E402
    EXTRACTED_DIR, FROM_DOCX_DIR, MnemoError, find_export, rel, slugify,
)

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
X_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

SUPPORTED = {".docx", ".xlsx"}
# Форматы Apple iWork тоже zip, но со своей бинарной начинкой (IWA snappy),
# стандартной библиотекой не разбираются. Честно сообщаем, а не выдаём пустоту.
KNOWN_UNSUPPORTED = {".pages", ".numbers", ".key", ".doc", ".xls", ".pdf"}


def _docx_text(archive: zipfile.ZipFile) -> str:
    """Текст документа: абзац на строку, порядок сохранён."""
    try:
        xml = archive.read("word/document.xml")
    except KeyError:
        return ""
    root = ElementTree.fromstring(xml)
    lines: list[str] = []
    for paragraph in root.iter(f"{W_NS}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{W_NS}t")]
        # Разрывы строк внутри абзаца — тоже содержимое.
        text = "".join(parts).strip()
        lines.append(text)
    # Схлопываем длинные серии пустых строк, но абзацную структуру не теряем.
    cleaned: list[str] = []
    for line in lines:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _xlsx_text(archive: zipfile.ZipFile) -> str:
    """Текст таблицы: строка на строку, ячейки через табуляцию."""
    shared: list[str] = []
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        for entry in root.iter(f"{X_NS}si"):
            shared.append("".join(node.text or "" for node in entry.iter(f"{X_NS}t")))
    except KeyError:
        pass

    chunks: list[str] = []
    sheets = sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet"))
    for sheet in sheets:
        root = ElementTree.fromstring(archive.read(sheet))
        chunks.append(f"### {Path(sheet).stem}")
        for row in root.iter(f"{X_NS}row"):
            cells: list[str] = []
            for cell in row.iter(f"{X_NS}c"):
                value = cell.find(f"{X_NS}v")
                if value is None or value.text is None:
                    cells.append("")
                elif cell.get("t") == "s":
                    index = int(value.text)
                    cells.append(shared[index] if index < len(shared) else "")
                else:
                    cells.append(value.text)
            if any(c.strip() for c in cells):
                chunks.append("\t".join(cells))
    return "\n".join(chunks).strip()


def _media(archive: zipfile.ZipFile) -> list[tuple[str, bytes]]:
    """Вшитые изображения. Отдаются байт-в-байт, без пересжатия (§3 стандарта)."""
    found = []
    for name in sorted(archive.namelist()):
        if name.startswith(("word/media/", "xl/media/")) and not name.endswith("/"):
            found.append((Path(name).name, archive.read(name)))
    return found


def extract(export: Path, source: Path) -> dict:
    suffix = source.suffix.lower()
    if suffix in KNOWN_UNSUPPORTED:
        raise MnemoError(
            f"{suffix} не разбирается стандартной библиотекой. "
            "Приложи текстовую расшифровку через /mnemo:add-text "
            "с fidelity=digest, а оригинал оставь в raw/attachments/."
        )
    if suffix not in SUPPORTED:
        raise MnemoError(f"не умею извлекать из {suffix}; поддержаны {sorted(SUPPORTED)}")
    if not source.is_file():
        raise MnemoError(f"нет файла: {source}")

    stem = slugify(source.stem)
    derived: list[str] = []

    with zipfile.ZipFile(source) as archive:
        text = _docx_text(archive) if suffix == ".docx" else _xlsx_text(archive)
        images = _media(archive)

    if text:
        target = export / EXTRACTED_DIR / f"{stem}.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        derived.append(rel(export, target))

    for name, blob in images:
        target = export / FROM_DOCX_DIR / stem / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        derived.append(rel(export, target))

    return {
        "source": rel(export, source),
        "derived_paths": derived,
        "text_chars": len(text),
        "images": len(images),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Извлечь текст и картинки из docx/xlsx")
    parser.add_argument("--export", default=".", help="корень экспорта (по умолчанию — найти вверх по дереву)")
    parser.add_argument("--file", required=True, help="путь к документу")
    parser.add_argument("--json", action="store_true", help="вывод в JSON")
    args = parser.parse_args()

    try:
        export = find_export(Path(args.export))
        source = Path(args.file)
        if not source.is_absolute():
            source = export / source
        result = extract(export, source)
    except MnemoError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"извлечено из {result['source']}:")
        print(f"  текст: {result['text_chars']} символов")
        print(f"  изображений: {result['images']}")
        for path in result["derived_paths"]:
            print(f"  → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
