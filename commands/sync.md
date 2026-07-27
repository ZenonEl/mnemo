---
description: Пересобрать INDEX.md и redactions.md из манифеста
---

Используй навык **mnemo:chat-export**.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_render.py --export <dir>
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_verify.py --export <dir>
```

Пересобирает производные из `MANIFEST.json` и подхватывает файлы, которые числились
`missing`, а теперь появились на диске.

LLM-пересказы (`*-summary.md`, `conventions.md`, `findings-log.md`) не трогаются —
их перегенерация всегда запрашивается явно.

$ARGUMENTS
