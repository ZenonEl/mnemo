---
description: Сверить новый пакет сообщений с действующим состоянием проекта
argument-hint: Пакет pNNN либо путь к JSON-плану
---

Используй навык **mnemo:reconcile**. Найди новый пакет, сопоставь его с
действующими требованиями, вопросами, решениями и фактами, затем составь полный
JSON-план по `SPEC/STANDARD.md` §6в.

Сначала обязательно выполни валидирующий dry-run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mnemo_manifest.py review --export <dir> \
  --plan-file <plan.json>
```

Покажи человеку changes, покрытие, impacts и подсказку формы feedback. Только
после подтверждения повтори с `--apply`, затем запусти `sync` и `verify`.

$ARGUMENTS
