# Сырые логи прогонов

В git попадают только таблицы и отчёты (`docs/results/`). Сырые логи из `runs/` — `results.csv`,
папки эпизодов (`calls.jsonl`, `effects.jsonl`, `model_calls.jsonl`, `trace.json`, `bank.sqlite`,
…) — лежат в общей папке **`handoff-bench-runs`** на Google Drive. По ним любой может
перепроверить таблицы и прочитать эпизод (`scripts/show.py`).

## Один раз: подключить папку

1. Попроси Алину открыть тебе доступ к папке `handoff-bench-runs` с правом «Редактор».
2. Поставь [rclone](https://rclone.org):

   ```bash
   brew install rclone
   ```

3. Подключи свой Google-аккаунт, сразу привязав его к общей папке:

   ```bash
   rclone config create gdrive drive scope=drive root_folder_id=1d33p8WVDL3DxKhBYWWvBuVvGcStwSZH2
   ```

   Откроется браузер: войди в свой аккаунт и разреши доступ. Теперь `gdrive:` — это корень
   общей папки.

4. Проверь:

   ```bash
   rclone lsd gdrive:
   ```

Токен rclone лежит в `~/.config/rclone/rclone.conf` и даёт доступ ко всему твоему диску: не
коммить его и не пересылай. Каждый подключает свой аккаунт.

## Залить прогон

Из корня репозитория, имя папки на диске — `run_id` прогона:

```bash
rclone copy runs/prefreeze-haiku-retell gdrive:prefreeze-haiku-retell --progress
```

Несколько прогонов сразу:

```bash
for r in runs/prefreeze-*-retell/; do rclone copy "$r" "gdrive:$(basename "$r")" --progress; done
```

`rclone copy` докачивает только новое и изменённое; повторный запуск безопасен. Он ничего не
удаляет на диске (в отличие от `rclone sync`, который им не пользуемся).

Архивы (`.tar.gz`) тоже можно, но папкой удобнее: эпизод можно открыть, не распаковывая всё.

## Скачать прогон

```bash
rclone copy gdrive:prefreeze-haiku-retell runs/prefreeze-haiku-retell --progress
```

## Правила

- **Залить до мержа PR с результатами.** В README результатов, в таблице «Прогоны», — `run_id`;
  по нему прогон находится на диске.
- **Папку на диске не переименовывать и не переписывать.** Если прогон пересчитан или
  перенумерован скриптом из `scripts/` (например, `renumber_hedged.py`), залить новую версию
  поверх и написать об этом в README результатов.
- **Сломанные прогоны** (лимит ключа, падение провайдера) — в `gdrive:_broken/<run_id>`, а не
  рядом с рабочими.
- Прогоны на ветках, которые не идут в `main` (например, `main-for-harness`), — туда же, под своим
  `run_id`.

Если rclone пишет, что общий `client_id` Google скоро перестанет работать: сейчас всё работает; когда
перестанет, нужно завести свой `client_id` ([инструкция rclone](https://rclone.org/drive/#making-your-own-client-id))
и пройти `rclone config` заново.
