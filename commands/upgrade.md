---
description: Обновить известные legacy-случаи экспорта до текущего стандарта
argument-hint: Путь к экспорту
---

Сначала покажи неизменяющий план:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py upgrade --export <dir>
```

Объясни человеку каждый кандидат и `known_unhandled`. Применяй только после
явного подтверждения, передав неизменившийся hash из плана, автора и
содержательную причину:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py upgrade --export <dir> \
  --apply --base-manifest-sha256 <sha256> --by <person-id> --reason '<почему>'
```

После применения запусти `verify`. Обычный review для исправления замороженной
истории не используй.

$ARGUMENTS
