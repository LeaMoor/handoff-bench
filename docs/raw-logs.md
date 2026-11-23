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

Прогон заливаем **одним архивом** `<run_id>.tar.gz`. Google Drive принимает около 30 файлов в
минуту, а в прогоне их тысячи (по ~13 на эпизод): папка на 150 эпизодов грузится час, архив — секунды.

Из корня репозитория:

```bash
tar -czf /tmp/prefreeze-haiku-retell.tar.gz -C runs prefreeze-haiku-retell
```

```bash
rclone copy /tmp/prefreeze-haiku-retell.tar.gz gdrive: --progress
```

Несколько прогонов сразу:

```bash
for r in runs/prefreeze-*-retell/; do n=$(basename "$r"); tar -czf "/tmp/$n.tar.gz" -C runs "$n" && rclone copy "/tmp/$n.tar.gz" gdrive: --progress; done
```

Проверить, что дошло без порчи (сверка по контрольной сумме):

```bash
rclone check /tmp/prefreeze-haiku-retell.tar.gz gdrive: --one-way --include prefreeze-haiku-retell.tar.gz
```

`rclone copy` ничего не удаляет на диске (в отличие от `rclone sync`, которым не пользуемся). Если
архив с тем же именем уже есть и отличается, rclone заменит его новым.

## Скачать прогон

```bash
rclone copy gdrive:prefreeze-haiku-retell.tar.gz /tmp/ --progress
```

```bash
tar -xzf /tmp/prefreeze-haiku-retell.tar.gz -C runs
```

## Правила

- **Залить до мержа PR с результатами.** В README результатов, в таблице «Прогоны», — `run_id`;
  по нему прогон находится на диске.
- **Архив на диске не переименовывать.** Если прогон пересчитан или
  перенумерован скриптом из `scripts/` (например, `renumber_hedged.py`), залить новый архив
  поверх и написать об этом в README результатов.
- **Сломанные прогоны** (лимит ключа, падение провайдера) — в `gdrive:_broken/`, а не
  рядом с рабочими.
- Прогоны на ветках, которые не идут в `main` (например, `main-for-harness`), — туда же, под своим
  `run_id`.

Если rclone пишет, что общий `client_id` Google скоро перестанет работать: сейчас всё работает; когда
перестанет, нужно завести свой `client_id` ([инструкция rclone](https://rclone.org/drive/#making-your-own-client-id))
и пройти `rclone config` заново.
