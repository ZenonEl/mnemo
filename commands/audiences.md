---
description: Настроить, кому проект обязан понятной синхронизацией
argument-hint: Имя аудитории и люди либо просмотр текущих настроек
---

Используй навык **mnemo:reconcile**.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py audiences --export <dir>
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py audiences --export <dir> \
  --add --name "руководство" --kind business --people "Пётр Иванов"
```

По умолчанию baseline — следующая сверка. Прошлое поднимай только по явной
просьбе через `--backfill-from sNNN`. Незнакомого человека не сохраняй свободной
строкой: сначала покажи one-step создание, затем используй
`--confirm-create-people` только после подтверждения.

$ARGUMENTS
