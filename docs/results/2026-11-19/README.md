# Прогоны до заморозки, 19–20 ноября 2026

Все прогоны помечены `prefreeze`: это поиск поломок и прикидка эффекта до пререгистрации,
а не основной результат. Около 2400 эпизодов, около $4 на OpenRouter.

## Что здесь

| Файл | Что |
|---|---|
| [night-report.md](night-report.md) | отчёт ночи: смоук, пилот, шаги 3–5 на Haiku 5.5 и GPT-6 Luna; почему упёрлись в потолок |
| [manual-labels.md](manual-labels.md) | ручная разметка спорных эпизодов: правило грейдера для `failed` + `uncertain`, «поручил проверку» |
| [notes.md](notes.md) | заметки по ходу, в том числе усложнённый мир, Qwen и калибровка без лазейки |
| [compare-models.txt](compare-models.txt) | шесть моделей: главный блок (6–11), калибровка (2–5), усложнённая сетка (6–31) |
| [compare-calibration.txt](compare-calibration.txt) | калибровка с лазейкой (2–5) и без неё (32–35), шесть моделей |
| [night-tables.txt](night-tables.txt) | таблицы шагов 3–5 ночи |

## Прогоны: модель, объём, код

Сырые логи — в `runs/<прогон>/` у того, кто запускал; передаются через общий диск (в git их нет).
Конфиги — [configs/prefreeze/](../../../configs/prefreeze/).

| Прогоны | Модель | Эпизодов | Код |
|---|---|---|---|
| `01-smoke-haiku`, `02-pilot`, `02-pilot-repeats`, `03-calibration`, `04-main-haiku` | Haiku 5.5 | 283 | `main` 9e4fade |
| `01b-smoke-luna`, `05-main-luna` | GPT-6 Luna | 137 | `main` 9e4fade |
| `06-mini-pilot`, `07-calibration-hidden` | Haiku 5.5 | 188 | ветка `feat/harder-world` |
| `q1-smoke`, `q2-cal-main`, `q3-grid`, `qwen-calibration-hidden` | Qwen 3.7 Flash | 430 | ветка `feat/harder-world` |
| `glm-*` | GLM-5.3-Flash | 430 | ветка `feat/harder-world` |
| `ds-*` | DeepSeek V4.1 Flash 1017 | 430 | ветка `feat/harder-world` |
| `4omini-*` | GPT-4o-mini 2024-07-18 | 430 | ветка `feat/harder-world` |
| `luna-calibration-hidden` | GPT-6 Luna | 84 | ветка `feat/harder-world` |

В `config.json` эпизодов коммит записан как `9e4fade…-dirty`: прогоны шли с незакоммиченными
изменениями. Для прогонов ветки это код коммита, который добавил эту папку; ячейки 32–35 появились
в нём позже прогонов 06 и Qwen, на ячейки 1–31 это не влияет. В `results.csv` ночных прогонов
(01–05) кодер ещё не различал «поручил проверку»: пересчёт текущими грейдером и кодером —
`uv run python scripts/regrade.py runs/prefreeze-0*`.

## Скрипты

Все в [scripts/](../../../scripts/), запуск из корня репозитория через `uv run python`.

| Скрипт | Что делает |
|---|---|
| `show.py runs/<прогон>/<эпизод>…` | эпизод коротко: все вызовы по порядку, что вернулось, итоговый `finish` |
| `regrade.py runs/<прогон>…` | пересчитать исходы и действия текущими грейдером и кодером |
| `calib_trace.py runs/<прогон>…` | калибровка: чем было второе поручение и что увидела проверка |
| `compare.py` | сравнение моделей (прогоны перечислены в начале скрипта) |
| `compare_calibration.py` | калибровка с лазейкой и без, все модели |
