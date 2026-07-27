---
description: Добавить скриншоты в RAW без пересжатия
argument-hint: Пути к изображениям
---

Используй навык **mnemo:chat-export**.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py add-file --export <dir> --kind screenshot \
  --file <путь> --source screenshot --fidelity verbatim \
  --origin "<откуда>" --date YYYY-MM-DD
```

Копия байт-в-байт. Пересжатие и конвертация запрещены стандартом.

Если оригинала нет, а содержимое известно — **не выдумывай файл**: заведи хвост
через `/mnemo:gaps` и приложи текстовую расшифровку как `digest`.

После изменения обязательно: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_render.py --export <dir>`,
затем `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_verify.py --export <dir>`. Покажи результат линтера человеку.

Аргумент пользователя: $ARGUMENTS
