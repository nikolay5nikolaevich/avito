"""
Smoke-тест веб-слоя (JSON API): GET / → /api/bootstrap → /api/search →
/api/status → /api/results → /export.csv.

Запуск (без новых зависимостей):
    python tests/smoke_test.py

Сервер запускается в фоновом потоке через uvicorn.
HTTP-запросы делаются через встроенный urllib — httpx/requests не нужны.
Парсинг Авито НЕ вызывается — используется синтетическая async-заглушка.
Тестовый кэш хранится в smoke_cache.db и удаляется после теста.

Legacy-фронтенд на Jinja2 (templates/, static/, POST /search) удалён — тест
проверяет только текущий контур React-frontend ↔ /api/*.
"""

import asyncio
import csv
import gc
import io
import json
import logging
import os
import sys
import threading
import time
import unittest.mock as mock
import urllib.error
import urllib.parse
import urllib.request

# Корень проекта и каталог backend/ в sys.path (модули бэкенда лежат в backend/)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(_project_root, "backend")
for _p in (_project_root, _backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger("smoke_test")

# ── Путь к тестовой БД ────────────────────────────────────────────────────────

SMOKE_DB = os.path.join(_project_root, "smoke_cache.db")
TEST_PORT = 18765  # нестандартный порт, чтобы не конфликтовать с продакшном

# Города, которые выбираем в тесте (slug'и должны существовать в cities.CITIES)
# Обязательно включаем moskva, sankt-peterburg и novosibirsk — для ассертов
TEST_CITY_SLUGS = ["moskva", "sankt-peterburg", "novosibirsk"]


# ── Синтетические данные ──────────────────────────────────────────────────────

def _make_listing(
    url: str,
    title: str,
    address: str,
    metro: str | None,
    views_today: int,
    age_hours: float = 100.0,
) -> dict:
    """Конструктор объявления по контракту parser.py."""
    return {
        "url": url,
        "title": title,
        "price": 10_000,
        "address": address,
        "metro": metro,
        "views_total": views_today * 5,
        "views_today": views_today,
        "published_at": None,
        "age_hours": age_hours,
    }


# Москва: 2 станции метро (Арбатская, Тверская) + None → «Без метро».
# Больше просмотров → avg_views_today > Новосибирска → первая в таблице.
# URL вида https://www.avito.ru/moskva/... — первый сегмент пути = «moskva» → _is_local True.
MOSKVA_LISTINGS: list[dict] = [
    _make_listing("https://www.avito.ru/moskva/divany/item_1", "Диван 1", "ул. Арбат 1", "Арбатская", 300),
    _make_listing("https://www.avito.ru/moskva/divany/item_2", "Диван 2", "ул. Арбат 2", "Арбатская", 250),
    _make_listing("https://www.avito.ru/moskva/divany/item_3", "Диван 3", "ул. Арбат 3", "Арбатская", 200),
    _make_listing("https://www.avito.ru/moskva/divany/item_4", "Диван 4", "пл. Тверская 1", "Тверская", 180),
    _make_listing("https://www.avito.ru/moskva/divany/item_5", "Диван 5", "пл. Тверская 2", "Тверская", 150),
    _make_listing("https://www.avito.ru/moskva/divany/item_6", "Диван 6", "ул. Дальняя 1", None, 50),
]
# avg_moskva = (300+250+200+180+150+50) / 6 = 188.33  (знаменатель = local_count = 6)

NOVOSIBIRSK_LISTINGS: list[dict] = [
    # Новосибирск без метро: меньше просмотров → идёт ниже Москвы
    _make_listing("https://www.avito.ru/novosibirsk/divany/item_1", "Диван НСК 1", "пр. Ленина 1", None, 60),
    _make_listing("https://www.avito.ru/novosibirsk/divany/item_2", "Диван НСК 2", "пр. Ленина 2", None, 40),
    _make_listing("https://www.avito.ru/novosibirsk/divany/item_3", "Диван НСК 3", "пр. Карла Маркса 1", None, 30),
]
# avg_novosibirsk = (60+40+30) / 3 = 43.33 → Москва (188.33) выше Новосибирска

# Словарь всех синтетических данных: slug → listings
_ALL_SYNTHETIC: dict[str, list[dict]] = {
    "moskva": MOSKVA_LISTINGS,
    "novosibirsk": NOVOSIBIRSK_LISTINGS,
    "sankt-peterburg": [],
}


# ── Заглушка parse_all ────────────────────────────────────────────────────────

async def _fake_parse_all(
    query: str,
    *,
    progress_cb=None,
    headless: bool = True,
    cdp_url=None,
    max_items: int = 150,
    filters=None,
    cities=None,
) -> dict[str, list[dict]]:
    """
    Синтетическая заглушка — не обращается к Авито.
    Вызывает progress_cb так же, как настоящий parse_all.
    Возвращает данные только для тех городов, что переданы в cities
    (если cities=None — для всех в _ALL_SYNTHETIC).
    """
    if cities is not None:
        city_list = cities
    else:
        from cities import CITIES as _ALL_CITIES
        city_list = _ALL_CITIES

    total = len(city_list)
    for idx, city in enumerate(city_list):
        if progress_cb is not None:
            progress_cb(idx, total, city.name)
        await asyncio.sleep(0)  # отдаём управление event loop

    if progress_cb is not None:
        progress_cb(total, total, "")

    return {city.slug: _ALL_SYNTHETIC.get(city.slug, []) for city in city_list}


# ── HTTP-хелперы (только stdlib) ──────────────────────────────────────────────

BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def _get(path: str, params: dict | None = None) -> tuple[int, str, dict]:
    """GET-запрос. Возвращает (status_code, body_text, headers)."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), {}


def _post_json(path: str, payload: dict) -> tuple[int, str, dict]:
    """POST с application/json. Возвращает (status_code, body_text, headers)."""
    url = BASE_URL + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers)


def _get_bytes(path: str, params=None) -> tuple[int, bytes, dict]:
    """GET-запрос, возвращает сырые байты (для CSV).
    params может быть dict или списком пар (для повторяющихся параметров)."""
    url = BASE_URL + path
    if params is not None:
        if isinstance(params, dict):
            pairs: list[tuple[str, str]] = []
            for k, v in params.items():
                if isinstance(v, list):
                    for item in v:
                        pairs.append((k, item))
                else:
                    pairs.append((k, v))
            url += "?" + urllib.parse.urlencode(pairs)
        else:
            url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {}


def _wait_server_ready(timeout: float = 15.0) -> None:
    """Ждёт, пока сервер начнёт отвечать на /."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{BASE_URL}/", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Сервер не поднялся за {timeout}с на порту {TEST_PORT}")


def _wait_for_done(job_id: str, timeout: float = 30.0) -> dict:
    """
    Опрашивает /api/status/{job_id} до получения статуса != 'running'.
    Поднимает AssertionError при таймауте.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_code, body, _ = _get(f"/api/status/{job_id}")
        assert status_code == 200, f"/api/status/{job_id} вернул {status_code}"
        data = json.loads(body)
        if data.get("status") != "running":
            return data
        time.sleep(0.15)
    raise AssertionError(
        f"Таймаут {timeout}с: задача {job_id} так и осталась в статусе 'running'"
    )


# ── Запуск тестового сервера в потоке ─────────────────────────────────────────

def _start_server(app) -> threading.Thread:
    """Запускает uvicorn в демон-потоке. Возвращает поток."""
    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=TEST_PORT,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    def _run():
        server.run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── Основной тест ─────────────────────────────────────────────────────────────

def run_smoke_test() -> None:
    """Сквозной smoke-тест веб-слоя на синтетических данных (только JSON API)."""
    print("=== Smoke-тест начат ===")

    # Удаляем старую тестовую БД если есть
    if os.path.exists(SMOKE_DB):
        os.remove(SMOKE_DB)

    # --- Патчим функции кэша ПЕРЕД импортом app ------------------------------
    # app.py вызывает cache_mod.init_db()/get_result()/save_result() без db_path
    # → используют "cache.db". Подменяем на тестовую БД.
    import cache as cache_real

    _orig_init_db = cache_real.init_db
    _orig_get_result = cache_real.get_result
    _orig_save_result = cache_real.save_result

    def _test_init_db(db_path: str = SMOKE_DB) -> None:
        _orig_init_db(db_path=SMOKE_DB)

    def _test_get_result(
        query: str,
        count: int = 150,
        filters_key: str = "",
        cities_key: str = "",
        db_path: str = SMOKE_DB,
    ) -> object | None:
        return _orig_get_result(
            query, count=count,
            filters_key=filters_key,
            cities_key=cities_key,
            db_path=SMOKE_DB,
        )

    def _test_save_result(
        query: str,
        results: object,
        count: int = 150,
        filters_key: str = "",
        cities_key: str = "",
        db_path: str = SMOKE_DB,
    ) -> None:
        _orig_save_result(
            query, results, count=count,
            filters_key=filters_key,
            cities_key=cities_key,
            db_path=SMOKE_DB,
        )

    # Импортируем app после установки путей
    import app as app_module

    with (
        mock.patch.object(app_module.cache_mod, "init_db", _test_init_db),
        mock.patch.object(app_module.cache_mod, "get_result", _test_get_result),
        mock.patch.object(app_module.cache_mod, "save_result", _test_save_result),
        # app.py: import parser as avito_parser → патчим avito_parser.parse_all
        mock.patch.object(app_module.avito_parser, "parse_all", _fake_parse_all),
    ):
        _start_server(app_module.app)
        _wait_server_ready(timeout=15.0)

        # ── Шаг 1: GET / (frontend shell) ─────────────────────────────────────
        print("Шаг 1: GET / (frontend shell)")
        sc_root, root_html, _ = _get("/")
        assert sc_root == 200, f"/ вернул {sc_root}"
        assert 'id="root"' in root_html, (
            'Frontend shell FAIL: на / нет контейнера id="root"'
        )
        assert "Avito Research" in root_html, (
            "Frontend shell FAIL: на / нет следов frontend shell"
        )
        print("  Frontend shell OK")

        # ── Шаг 2: GET /api/bootstrap ─────────────────────────────────────────
        print("Шаг 2: GET /api/bootstrap")
        sc_boot, boot_body, boot_headers = _get("/api/bootstrap")
        assert sc_boot == 200, f"/api/bootstrap вернул {sc_boot}: {boot_body[:300]}"
        boot_ct = boot_headers.get("Content-Type", boot_headers.get("content-type", ""))
        assert "application/json" in boot_ct.lower(), (
            f"/api/bootstrap должен вернуть JSON, получили: {boot_ct}"
        )
        boot_data = json.loads(boot_body)
        assert boot_data.get("app_name") == "Avito Research", (
            f"Bootstrap FAIL: app_name={boot_data.get('app_name')!r}"
        )
        assert isinstance(boot_data.get("cities"), list) and boot_data["cities"], (
            "Bootstrap FAIL: список городов пуст или отсутствует"
        )
        assert any(city.get("slug") == "moskva" for city in boot_data["cities"]), (
            "Bootstrap FAIL: в bootstrap нет города moskva"
        )
        print("  Bootstrap API OK")

        # ── Шаг 3: POST /api/search ───────────────────────────────────────────
        print("Шаг 3: POST /api/search")
        sc_api, api_body, api_headers = _post_json(
            "/api/search",
            {
                "query": "смоук тест",
                "count": 30,
                "price_min": "1000",
                "price_max": "5000",
                "cities": TEST_CITY_SLUGS,
                "gender": [],
            },
        )
        assert sc_api == 200, f"/api/search вернул {sc_api}: {api_body[:300]}"
        api_ct = api_headers.get("Content-Type", api_headers.get("content-type", ""))
        assert "application/json" in api_ct.lower(), (
            f"/api/search должен вернуть JSON, получили: {api_ct}"
        )
        api_data = json.loads(api_body)
        job_id = api_data.get("job_id")
        assert job_id, "API search FAIL: нет job_id"
        assert api_data.get("status") in {"running", "done"}, (
            f"API search FAIL: неожиданный status={api_data.get('status')!r}"
        )
        print(f"  JSON search API OK (job_id={job_id})")

        # ── Шаг 4: опрос /api/status/{job_id} ─────────────────────────────────
        print("Шаг 4: опрос /api/status/...")
        final_status = _wait_for_done(job_id, timeout=30.0)
        assert final_status.get("status") == "done", (
            f"Assert 1 FAIL: ожидали status='done', получили {final_status}"
        )
        print("Assert 1 PASS: status=done")

        # ── Шаг 5: GET /api/results/{job_id} ──────────────────────────────────
        print(f"Шаг 5: GET /api/results/{job_id}")
        sc_res, res_body, res_headers = _get(f"/api/results/{job_id}")
        assert sc_res == 200, f"/api/results/{job_id} вернул {sc_res}: {res_body[:300]}"
        res_ct = res_headers.get("Content-Type", res_headers.get("content-type", ""))
        assert "application/json" in res_ct.lower(), (
            f"/api/results/{job_id} должен вернуть JSON, получили: {res_ct}"
        )
        res_data = json.loads(res_body)
        assert res_data.get("query") == "смоук тест", (
            f"API results FAIL: query={res_data.get('query')!r}"
        )
        results = res_data.get("results")
        assert isinstance(results, list) and results, (
            "API results FAIL: results отсутствуют или пусты"
        )

        # Assert 2: Москва присутствует и стоит ВЫШЕ Новосибирска (сортировка по avg)
        names = [c.get("city_name") for c in results]
        assert "Москва" in names, f"Assert 2 FAIL: 'Москва' не в результатах: {names}"
        assert "Новосибирск" in names, (
            f"Assert 2 FAIL: 'Новосибирск' не в результатах: {names}"
        )
        assert names.index("Москва") < names.index("Новосибирск"), (
            f"Assert 2 FAIL: Москва должна быть ВЫШЕ Новосибирска. Порядок: {names}"
        )
        print(f"Assert 2 PASS: порядок городов {names}")

        # Assert 3: у Москвы есть разбивка по метро (Арбатская/Тверская)
        moskva = next(c for c in results if c.get("city_name") == "Москва")
        metro_bd = moskva.get("metro_breakdown") or []
        metro_names = {st.get("metro") for st in metro_bd}
        assert "Арбатская" in metro_names, (
            f"Assert 3 FAIL: 'Арбатская' нет в metro_breakdown: {metro_names}"
        )
        assert "Тверская" in metro_names, (
            f"Assert 3 FAIL: 'Тверская' нет в metro_breakdown: {metro_names}"
        )
        print(f"Assert 3 PASS: метро Москвы {sorted(n for n in metro_names if n)}")

        # Assert 4: export_url присутствует и ведёт на /export.csv
        export_url = res_data.get("export_url", "")
        assert export_url.startswith("/export.csv"), (
            f"Assert 4 FAIL: export_url некорректен: {export_url!r}"
        )
        print("Assert 4 PASS: export_url присутствует")

        # ── Шаг 6: GET /export.csv ────────────────────────────────────────────
        # Те же count/фильтры/города, чтобы попасть в тот же ключ кэша
        print("Шаг 6: GET /export.csv")
        sc_csv, csv_bytes, csv_headers = _get_bytes(
            "/export.csv",
            params={
                "query": "смоук тест",
                "count": "30",
                "price_min": "1000",
                "price_max": "5000",
                "cities": TEST_CITY_SLUGS,
            },
        )
        assert sc_csv == 200, f"Assert 5 FAIL: /export.csv вернул {sc_csv}"
        ct = csv_headers.get("Content-Type", csv_headers.get("content-type", ""))
        assert "csv" in ct.lower(), f"Assert 5 FAIL: content-type не csv: {ct}"

        try:
            csv_text = csv_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise AssertionError(f"Assert 5 FAIL: CSV не декодируется как utf-8-sig: {e}")

        rows = list(csv.reader(io.StringIO(csv_text)))
        assert rows, "Assert 5 FAIL: CSV пустой"
        header = rows[0]
        for col in ("Город", "Адрес/метро", "Среднее просм/день"):
            assert col in header, f"Assert 5 FAIL: нет '{col}' в заголовке CSV: {header}"

        city_col = [row[0] for row in rows[1:] if row]
        assert "Москва" in city_col, (
            f"Assert 5 FAIL: 'Москва' не в CSV. Первые: {city_col[:5]}"
        )
        assert "Новосибирск" in city_col, (
            f"Assert 5 FAIL: 'Новосибирск' не в CSV. Первые: {city_col[:5]}"
        )

        metro_cells = [row[1] for row in rows[1:] if row and row[0] == "Москва"]
        assert any("Арбатская" in v for v in metro_cells), (
            f"Assert 5 FAIL: 'Арбатская' не в CSV. Значения: {metro_cells}"
        )
        assert any("Тверская" in v for v in metro_cells), (
            f"Assert 5 FAIL: 'Тверская' не в CSV. Значения: {metro_cells}"
        )
        print("Assert 5 PASS: CSV корректен, станции метро присутствуют")

    # ── Подчистка ──────────────────────────────────────────────────────────────
    gc.collect()  # освобождаем SQLite-соединения (важно для Windows)
    if os.path.exists(SMOKE_DB):
        try:
            os.remove(SMOKE_DB)
            print("smoke_cache.db удалён")
        except OSError as e:
            print(f"Не удалось удалить smoke_cache.db: {e}")

    print("\n=== SMOKE TEST: ВСЕ ПРОВЕРКИ ПРОШЛИ УСПЕШНО ===")


# ── Самозапуск ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run_smoke_test()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n[ERROR] {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(2)
