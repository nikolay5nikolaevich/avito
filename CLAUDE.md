# CLAUDE.md

## Проект
Локальный веб-сервис: парсит Авито в 10 городах по запросу пользователя, выдаёт таблицу со средними просмотрами/день и топ-3 объявлениями по каждому городу. MVP, потом масштаб до 170 городов.

## Пользователь
Новичок. Знает JS, поверхностно Python. Не знает Node.js. Просил «сделай сам, я только запускаю» → давай готовые команды и файлы целиком, не диффы.

## Стек
**Бэкенд** (`backend/`): Python 3.11+, Playwright (Chromium), BeautifulSoup, FastAPI (JSON API),
SQLite. **Фронтенд** (`frontend/`): React + Vite (собирается в `frontend/dist/`, раздаётся бэкендом).
Локальный запуск из корня проекта: `python backend/app.py` → `localhost:8000`
(перед первым запуском собрать фронт: `cd frontend && npm install && npm run build`).

## Города
Справочник — **топ-50 городов РФ** по убыванию населения (`cities.py`, поле `population`
в тыс.). На сайте пользователь чекбоксами выбирает, какие парсить (по умолчанию топ-10).
Для МСК/СПб — доп. разбивка по метро (`has_metro=True` только у них). ~7 slug'ов менее
крупных городов помечены как требующие живой проверки (mahachkala, habarovsk, astrahan,
balashiha, groznyy, naberezhnye_chelny, ulan-ude) — неверный slug парсер логирует и пропускает.

URL: `https://www.avito.ru/{slug}?q={запрос}` (+ `&pmin=&pmax=` при фильтре цены)

## Логика
По каждому выбранному городу: парсим N объявлений (N — выбранное пользователем количество,
1–500, по умолч. 150) → вытаскиваем адрес/метро, просмотров за день, дату публикации.

**Фильтр локальности (ВАЖНО):** Авито подмешивает в выдачу города объявления из чужих
регионов (федеральные, с доставкой). Оставляем только объявления, чей URL физически в
парсимом городе (первый сегмент пути == city.slug), остальные отбрасываем
(`analytics._is_local`). Без этого метрики всех городов одинаковы (одно и то же
московское объявление везде).

**Фильтр свежести:** исключаем, если опубликовано <24ч назад И просмотров 1–30. Включаем всё остальное.

**Сбор:** парсер набирает именно N **местных** объявлений (`max_items` = N), листая
страницы выдачи и пропуская чужие регионы прямо при сборе (`parser._collect_listing_items`
+ `cities.is_local_listing`). Предохранители: `MAX_SEARCH_PAGES=20` и остановка, если на
странице нет ни одного местного (местные закончились). Если местных в городе меньше N —
берём сколько есть (честная нехватка).

**Метрики:**
- Среднее = сумма просм/день местных ÷ `local_count` (число фактически набранных местных
  после фильтра свежести).
- `local_count` показывается в таблице/CSV («Весь город · N местных»), чтобы avg по
  1 объявлению не вводил в заблуждение.
- Топ-3 объявления — с **разных** адресов/метро
- Для МСК/СПб: то же самое внутри каждой станции метро

## Вывод
Таблица, сортировка по убыванию среднего просм/день. Экспорт CSV. Кэш в SQLite на 24ч.

## Карта проекта (читай это вместо вычитки всего кода)

**Код:** все серверные модули — в `backend/` (запуск из корня: `python backend/app.py`;
`app.py` сам делает `os.chdir` в корень проекта, поэтому `cache.db`, `logs/`, `debug/`,
`.pw-profile/`, `frontend/dist/` всегда резолвятся относительно корня).
- `backend/app.py` — FastAPI-сервер и оркестрация. Раздаёт собранный React (`frontend/dist/`):
  `GET /` и `GET /workspace` → `index.html`, `GET /assets/{path}` → ассеты Vite.
  JSON API для фронта: `GET /api/bootstrap` (города + дефолты), `POST /api/search`
  (JSON: `query` + `count` [1,500] + `price_min`/`price_max` + `gender` [male/female; один →
  фильтр, 0/2 → без] + `cities` [slug'и]), `GET /api/status/{job_id}`, `GET /api/results/{job_id}`
  (в т.ч. `export_url`). Плюс `GET /export.csv?query=&count=&price_min=&price_max=&cities=&gender=`
  (CSV, UTF-8 с BOM). Главная функция
  `run_job(job_id, query, count, filters, cities)` — парсинг (`max_items=count`, `filters=`,
  `cities=` подмножество) → метрики ТОЛЬКО по выбранным городам →
  кэш (count + `filters.cache_key_part()` + `cities_key`). Состояние задач — в dict `JOBS`.
  Конфиг: `CDP_URL` (env `AVITO_CDP_URL`, по умолч. `http://localhost:9222`),
  `MAX_ITEMS_PER_CITY` (env `AVITO_MAX_ITEMS`, по умолч. 150).
- `parser.py` — асинхронный парсинг Авито на Playwright. Публичное:
  `parse_all(query, *, progress_cb, headless, cdp_url, max_items, filters=None, cities=None)`
  (cities=None → все CITIES; иначе подмножество, прогресс по их числу),
  `parse_city(city, query, *, max_items, headless, cdp_url, filters=None)`,
  `parse_avito_date()`, исключение `AvitoBlockedError`, `MAX_ITEMS=150`.
  Внутри: `_connect_over_cdp()` (подключение к Chrome пользователя),
  `_launch_persistent_context()` (свой браузер — банится Авито),
  `_wait_for_cards_and_scroll()` (ждёт React-рендер + докрутка),
  `_collect_listing_items()` (набирает N МЕСТНЫХ, пагинация с предохранителями `MAX_SEARCH_PAGES`
  и «нет местных на странице»), `_parse_item_page()`, `_extract_items_from_html()`,
  `_extract_metro()`, `_check_block()`.
- `cities.py` — `dataclass City(name, slug, has_metro, population)`, список `CITIES`
  (50 городов по убыванию населения), `build_search_url(slug, query, filters=None)`
  (дописывает `&pmin=&pmax=` И добавляет гендер-слово к запросу через `filters.query_suffix()`),
  `get_city_by_slug(slug)`,
  `get_cities_by_slugs(slugs)` (подмножество в порядке населения, неизвестные игнорит),
  `is_local_listing(url, city_slug)` (локальность по первому сегменту пути URL — единый
  источник для парсера и аналитики).
- `filters.py` — расширяемая система фильтров поиска. `@dataclass(frozen=True) SearchFilters`
  (поля `price_min`/`price_max`, `gender` ["male"/"female"/None]). Методы:
  `from_form(price_min, price_max, gender=None)` (нормализация), `to_avito_params()` →
  `{"pmin":..,"pmax":..}` (цена = URL-параметры), `query_suffix()` → "мужские"/"женские"/""
  (ПОЛ реализован НЕ параметром, а добавлением слова к запросу — на Авито пол это категория,
  а не параметр; см. память [[avito-gender-is-category]]), `cache_key_part()` (для ключа кэша,
  учитывает цену+пол), `describe()` (общее описание фильтров для шаблонов). Самотесты в `__main__`.
- `avito_selectors.py` — CSS-селекторы Авито (переименован из `selectors.py`: имя
  `selectors` конфликтует со стандартным модулем Python, нужным uvicorn).
  ПОДТВЕРЖДЁННЫЕ на живой странице: карточка `[data-marker='item']`, локация/метро
  `[data-marker='item-location']`, дата `[data-marker='item-view/item-date']`,
  просмотры `[data-marker='item-view/total-views']` и `[data-marker='item-view/today-views']`.
- `analytics.py` — метрики (без сети). `filter_listings()` (свежесть),
  `_is_local(item, city_slug)` (локальность по slug в URL), `compute_city_result(city, listings)`,
  `top3_distinct()`. Знаменатель среднего по городу = `local_count` (число местных после
  фильтров), по метро — размер группы. Результат содержит `local_count`. `FRESH_HOURS/MIN/MAX`.
  Самотесты в `__main__`.
- `cache.py` — SQLite (`sqlite3`). `init_db()`,
  `save_result(query, results, count=150, filters_key="", cities_key="")`,
  `get_result(query, count=150, filters_key="", cities_key="")`. Ключ кэша составной:
  `f"{нормализованный_запрос}|{count}|{filters_key}|{cities_key}"` (запросы с разным
  count/фильтрами/набором городов не путаются; `cities_key` = отсортированные выбранные
  slug'и через запятую). `CACHE_TTL_HOURS=24`. Файл `cache.db`.
- `backend/diag.py` — диагностика парсинга: `python backend/diag.py "запрос" [slug] [--cdp] [--port N]`.
  Сохраняет в `debug/`: `search.html`, `item.html`, скриншоты, `diag.log`. Переиспользует `parser.py`.
- `start-chrome.bat` — запускает Chrome с `--remote-debugging-port=9222` и профилем
  `%USERPROFILE%\avito-chrome-profile` (для CDP-подключения, см. «Запуск»).
- `frontend/` — React + Vite. Исходники в `frontend/src/` (`pages/LandingPage.jsx`,
  `pages/WorkspacePage.jsx`, `components/HeroSculpture.*`, `lib/api.js` — клиент `/api/*`,
  `lib/formatters.js`). Сборка `npm run build` → `frontend/dist/` (раздаётся бэкендом).
- `tests/smoke_test.py` — сквозной тест веб-слоя на синтетике: `GET /` → `/api/bootstrap` →
  `/api/search` → `/api/status` → `/api/results` → `/export.csv` (uvicorn в потоке, подмена
  `parse_all` и кэша). Запуск: `python tests/smoke_test.py`.

**Доки:** `README.md` (установка/запуск), `requirements.txt`, `install.sh`.

**Артефакты (не код, в `.gitignore`):** `.venv/`, `cache.db`, `logs/`, `debug/` (диагностика),
`.pw-profile/` (профиль Playwright для fallback-режима), `frontend/dist/` (сборка Vite),
`frontend/node_modules/`.

## Запуск (ВАЖНО — иначе бан)
Авито банит ЛЮБОЙ запущенный Playwright-браузер (даже реальный Chrome со stealth) →
заглушка «Доступ ограничен». Рабочий способ — подключаться к Chrome, запущенному
пользователем вручную, через CDP:
1. `start-chrome.bat` → откроется Chrome с отладкой; зайти руками на avito.ru (прогрев).
2. `python backend/app.py` → `localhost:8000`. Сам подключится к этому Chrome (CDP_URL).
- VPN-конфликт: Авито нужен РУ-IP, Claude Code — не-РУ. Разводим по времени
  (см. память проекта `vpn-decoupled-workflow`).
- Быстрая проверка сайта без многочасового прогона: `$env:AVITO_MAX_ITEMS="5"` перед запуском.

## Решения
- Playwright, не requests (Авито банит голые HTTP).
- Селекторы в `avito_selectors.py` — Авито меняет вёрстку, правим в одном месте.
- Карточки рендерит React → парсер ждёт появления `[data-marker='item']` в DOM, потом читает.
- Прогресс: polling фронтом (`/api/status/{job_id}`).
- Фронт и бэк — раздельно: React (`frontend/`) собирается в `frontend/dist/` и раздаётся бэкендом,
  общается с ним только по JSON `/api/*`. Старый серверный Jinja2-фронт удалён.
- Без прокси в MVP. Обход бана — CDP-подключение к ручному Chrome (см. «Запуск»).



**Сейчас:** MVP + произвольное количество + фильтр цены + выбор городов (топ-50) готовы
(самотесты и smoke зелёные). Параметры цены `pmin`/`pmax` ПОДТВЕРЖДЕНЫ на живом Авито
(2026-05-26): легаси-параметры работают, хотя новый UI Авито генерирует сложный `f`-блоб.
Добавлен фильтр локальности (отсев чужих регионов) — см. «Логика». Бэкенд вынесен в `backend/`,
серверный Jinja2-фронт заменён на React (`frontend/`), git-мусор (`.venv/`, `.pw-profile/` и пр.)
убран в `.gitignore`.
ВАЖНО: `python backend/app.py` запускает uvicorn БЕЗ авто-перезагрузки — после правок кода сервер
надо перезапускать вручную. ВАЖНО-2: при изменении логики метрик (analytics.py) ключ кэша
не меняется → удаляй `cache.db`, иначе отдаст старый результат. Остаётся ручная проверка
через CDP: (1) `max_items` реально ограничивает парсинг; (2) выверить ~7 помеченных slug'ов;
(3) что фильтр локальности по slug совпадает с реальными путями URL Авито (особенно
города-спутники типа Балашихи). Следующее — масштаб до 170 городов, прокси.

## Не делаем в MVP
25 городов, прокси, авторизацию, деплой, мобильную версию, объединение синонимов запросов.

## Стиль
Комментарии на русском. PEP 8, type hints. `logging`, не `print`. Ошибка одного объявления/города — лог + пропуск, не падение. Общение с пользователем — по-русски, без лишних дисклеймеров, команды отдельным блоком.