---
description: Показать и завести хвосты — чего в экспорте не хватает
argument-hint: Опционально: чего не хватает
---

Используй навык **mnemo:chat-export**.

**Показать хвосты:** раздел «Хвосты» в `INDEX.md`, либо
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py show --export <dir>` и записи со `status != present`.

**Завести хвост:**

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py add-gap --export <dir> \
  --status missing --expected-path raw/screenshots/<имя>.png \
  --source screenshot --fidelity placeholder \
  --note "<чего не хватает и почему>" --origin "<что это было>" --date YYYY-MM-DD
```

Различай:
- `missing` — оригинал существует, но не добыт. Это **задача**, и `--expected-path`
  говорит, куда его положить, когда достанем.
- `unrecoverable` — утрачен принципиально (голосовое не сохранено, кэш стёрт).
  Это **факт**, `--expected-path` не нужен.

Молча пропустить материал хуже, чем показать дыру: пропуск, которого не видно,
выглядит как отсутствие материала.

После изменения обязательно: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_render.py --export <dir>`,
затем `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_verify.py --export <dir>`. Покажи результат линтера человеку.

$ARGUMENTS
