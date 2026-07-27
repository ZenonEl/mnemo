---
description: Добавить сообщение или транскрипт в RAW
argument-hint: Текст, путь к файлу, или описание что добавить
---

Используй навык **mnemo:chat-export**.

Записать текст в `raw/messages/` дословно.

1. Определи **автора** и **дату материала** (не сегодняшнюю — дату, когда это сказано).
2. Определи **достоверность**. Не угадывай: если непонятно, откуда текст, — спроси.
   Пересланная копия и лог сессии — это `reconstructed`, твой пересказ — `digest`.
3. Запиши:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py add-text --export <dir> \
  --author "<кто>" --date YYYY-MM-DD \
  --source telegram --fidelity <уровень> --note "<если не verbatim>" \
  --origin "<откуда физически взято>" --from-file <файл>
```
Без `--from-file` текст читается со stdin.

RAW дословен: не исправляй опечатки и не причёсывай. Пояснения — в `summaries/`.

После изменения обязательно: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_render.py --export <dir>`,
затем `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_verify.py --export <dir>`. Покажи результат линтера человеку.

Аргумент пользователя: $ARGUMENTS
