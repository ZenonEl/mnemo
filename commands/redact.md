---
description: Зарегистрировать изъятие материала
argument-hint: Что изъять и почему
---

Используй навык **mnemo:chat-export**.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py redact --export <dir> \
  --reason pii|off-topic|leaked-internal|client-confidential \
  --description "<что изъято, НЕ раскрывая изъятого>" \
  --scope "<где было>" [--reversible --vault-ref "<путь ВНЕ экспорта>"] \
  [--items i001,i002]
```

Два правила, нарушение которых отменяет смысл изъятия:

1. `--description` говорит **что** изъято, но не показывает изъятое.
   Правильно: «паспортные данные директора». Неправильно: сами данные.
2. `--vault-ref` указывает **вне** экспорта. Изъятое и экспорт не хранятся вместе.

Само содержимое из RAW удали отдельно — команда только регистрирует факт изъятия.

После изменения обязательно: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_render.py --export <dir>`,
затем `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_verify.py --export <dir>`. Покажи результат линтера человеку.

Что изъять: $ARGUMENTS
