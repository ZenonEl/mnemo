---
description: Отметить фактическую доставку сохранённого feedback
argument-hint: Сверка, номер feedback, канал и стабильный ref
---

Используй навык **mnemo:reconcile**. Вызывай `deliver` только после реальной
отправки сообщения:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py deliver --export <dir> \
  sNNN --feedback <n> --where <канал> --ref <стабильная-ссылка>
```

Та же пара `where/ref` идемпотентна. Другой ref — только явный `--resend` с
содержательной `--reason`. Автоматически отправлять сообщение нельзя.

$ARGUMENTS
