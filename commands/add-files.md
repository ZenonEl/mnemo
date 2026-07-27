---
description: Добавить вложения в RAW с извлечением содержимого
argument-hint: Пути к файлам
---

Используй навык **mnemo:chat-export**.

Положить документы в `raw/attachments/`. Для `.docx` и `.xlsx` — извлечь текст и
вшитые изображения.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py add-file --export <dir> --kind attachment \
  --file <путь> --extract \
  --source docx --fidelity verbatim \
  --origin "<откуда прислано>" --date YYYY-MM-DD
```

- `--extract` обязателен для docx/xlsx: текст уйдёт в `_extracted-text/`,
  картинки — в `raw/screenshots/from-docx/<doc>/`. Отдельных записей им не заводи —
  это `derived_paths` родителя.
- `.pages`, `.numbers`, `.pdf`, `.doc` стандартной библиотекой не разбираются.
  Оригинал всё равно клади в RAW, а расшифровку добавь через `/mnemo:add-text`
  с `--fidelity digest`.
- После добавления документов обнови `summaries/attachments-summary.md`: что решено
  и на что сверяться. Явные решения заказчика помечай видимым маркером.

После изменения обязательно: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_render.py --export <dir>`,
затем `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_verify.py --export <dir>`. Покажи результат линтера человеку.

Аргумент пользователя: $ARGUMENTS
