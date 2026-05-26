"""
Smoke-тест веб-слоя: POST /search → /status → /results → /export.csv.

Запуск (без новых зависимостей):
    python tests/smoke_test.py

Сервер запускается в фоновом потоке через uvicorn.
HTTP-запросы делаются через встроенный urllib — httpx/requests не нужны.
Парсинг Авито НЕ вызывается — используется синтетическая async-заглушка.
Тестовый кэш хранится в smoke_cache.db и удаляется после теста.
"""

import asyncio
import csv
import gc
import io
import logging
import os
import sys
import threading
import time
import unittest.mock as mock
import urllib.error
import urllib.parse
import urllib.request

# Добавляем корень проекта в sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

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


# Москва: 3 станции метро (Арбатская, Тверская) + None → «Без метро»
# Больше просмотров → avg_views_today > Новосибирска → первая в таблице
# URL вида https://www.avito.ru/moskva/... — первый сегмент пути = «moskva» → _is_local вернёт True
MOSKVA_LISTINGS: list[dict] = [
    # Арбатская: 3 объявления с разными адресами
    _make_listing("https://www.avito.ru/moskva/divany/item_1", "Диван 1", "ул. Арбат 1", "Арбатская", 300),
    _make_listing("https://www.avito.ru/moskva/divany/item_2", "Диван 2", "ул. Арбат 2", "Арбатская", 250),
    _make_listing("https://www.avito.ru/moskva/divany/item_3", "Диван 3", "ул. Арбат 3", "Арбатская", 200),
    # Тверская: 2 объявления с разными адресами
    _make_listing("https://www.avito.ru/moskva/divany/item_4", "Диван 4", "пл. Тверская 1", "Тверская", 180),
    _make_listing("https://www.avito.ru/moskva/divany/item_5", "Диван 5", "пл. Тверская 2", "Тверская", 150),
    # Без метро: 1 объявление
    _make_listing("https://www.avito.ru/moskva/divany/item_6", "Диван 6", "ул. Дальняя 1", None, 50),
]
# avg_moskva = (300+250+200+180+150+50) / 6 = 188.33  (знаменатель = local_count = 6)

NOVOSIBIRSK_LISTINGS: list[dict] = [
    # Новосибирск без метро: меньше просмотров → идёт ниже Москвы
    # URL вида https://www.avito.ru/novosibirsk/... — первый сегмент = «novosibirsk» → _is_local True
    _make_listing("https://www.avito.ru/novosibirsk/divany/item_1", "Диван НСК 1", "пр. Ленина 1", None, 60),
    _make_listing("https://www.avito.ru/novosibirsk/divany/item_2", "Диван НСК 2", "пр. Ленина 2", None, 40),
    _make_listing("https://www.avito.ru/novosibirsk/divany/item_3", "Диван НСК 3", "пр. Карла Маркса 1", None, 30),
]
# avg_novosibirsk = (60+40+30) / 3 = 43.33  (знаменатель = local_count = 3)
# avg_moskva (188.33) > avg_novosibirsk (43.33) → Москва первая в таблице

# Словарь всех синтетических данных: slug → listings
# Используется заглушкой — возвращает только те города, что были запрошены
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
    Принимает все kwargs, которые передаёт run_job (cdp_url, max_items, filters, cities).
    Возвращает данные только для тех городов, что переданы в cities
    (если cities=None — для всех в _ALL_SYNTHETIC).
    """
    # Определяем, для каких городов возвращать данные
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

    # Финальный callback
    if progress_cb is not None:
        progress_cb(total, total, "")

    # Возвращаем синтетические данные для каждого запрошенного города
    # Для городов, которых нет в _ALL_SYNTHETIC, возвращаем пустой список
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


def _post_form(path: str, data: dict | list) -> tuple[int, str, str]:
    """POST с application/x-www-form-urlencoded. Возвращает (status, body, location).
    data может быть словарём или списком пар (key, value) для повторяющихся полей."""
    url = BASE_URL + path
    # urllib.parse.urlencode поддерживает список пар для повторяющихся полей
    if isinstance(data, dict):
        # Преобразуем dict в список пар, разворачивая списки значений
        pairs: list[tuple[str, str]] = []
        for k, v in data.items():
            if isinstance(v, list):
                for item in v:
                    pairs.append((k, item))
            else:
                pairs.append((k, v))
        encoded = urllib.parse.urlencode(pairs).encode("utf-8")
    else:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # не перенаправляем

    no_redirect_opener = urllib.request.build_opener(_NoRedirect())
    try:
        with no_redirect_opener.open(req, timeout=10) as resp:
            loc = resp.headers.get("Location", "")
            return resp.status, resp.read().decode("utf-8", errors="replace"), loc
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        return e.code, e.read().decode("utf-8", errors="replace"), loc


def _get_bytes(path: str, params=None) -> tuple[int, bytes, dict]:
    """GET-запрос, возвращает сырые байты (для CSV).
    params может быть dict или списком пар (для повторяющихся параметров)."""
    url = BASE_URL + path
    if params is not None:
        if isinstance(params, dict):
            # Разворачиваем списки в повторяющиеся параметры
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
    Опрашивает /status/{job_id} до получения статуса != 'running'.
    Поднимает AssertionError при таймауте.
    """
    import json as _json
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_code, body, _ = _get(f"/status/{job_id}")
        assert status_code == 200, f"/status/{job_id} вернул {status_code}"
        data = _json.loads(body)
        if data.get("status") != "running":
            return data
        time.sleep(0.15)
    raise AssertionError(
        f"Таймаут {timeout}с: задача {job_id} так и в статусе 'running'"
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
    """Сквозной smoke-тест веб-слоя на синтетических данных."""
    print("=== Smoke-тест начат ===")

    # Удаляем старую тестовую БД если есть
    if os.path.exists(SMOKE_DB):
        os.remove(SMOKE_DB)

    # --- Патчим функции кэша и парсера ПЕРЕД импортом app --------------------
    # app.py вызывает cache_mod.init_db(), cache_mod.get_result(), cache_mod.save_result()
    # без db_path → используют "cache.db". Подменяем на тестовую БД.
    import cache as cache_real

    # Сохраняем ссылки на оригинальные функции ДО патчинга, чтобы избежать рекурсии
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
        # Запускаем сервер
        _start_server(app_module.app)
        _wait_server_ready(timeout=15.0)
        print(f"Сервер запущен на порту {TEST_PORT}")

        # ── Шаг 1: POST /search ───────────────────────────────────────────────
        # Отправляем count=30, фильтр цены и выбранные города
        # Включаем moskva, sankt-peterburg, novosibirsk — для ассертов про метро и сортировку
        print("Шаг 1: POST /search")
        status_code, _body, location = _post_form(
            "/search",
            {
                "query": "смоук тест",
                "count": "30",
                "price_min": "1000",
                "price_max": "5000",
                # Список городов: повторяющееся поле формы
                "cities": TEST_CITY_SLUGS,
            },
        )
        assert status_code in (302, 303), (
            f"POST /search: ожидали 302/303, получили {status_code}"
        )
        assert location, f"POST /search: нет Location, статус={status_code}"
        print(f"  Location: {location}")

        # Извлекаем job_id из /progress/... или /results/...
        if "/progress/" in location:
            job_id = location.split("/progress/")[-1].split("?")[0]

            # Проверяем рендер страницы прогресса
            sc_prog, html_prog, _ = _get(f"/progress/{job_id}")
            assert sc_prog == 200, f"/progress/{job_id} вернул {sc_prog}"
            assert job_id in html_prog, "Страница прогресса не содержит job_id"
            print(f"  Страница прогресса OK (job_id={job_id})")

            # ── Шаг 2: опрос /status/{job_id} ────────────────────────────────
            print("Шаг 2: опрос /status/...")
            final_status = _wait_for_done(job_id, timeout=30.0)

        elif "/results/" in location:
            job_id = location.split("/results/")[-1].split("?")[0]
            print(f"  Кэш-хит: сразу /results/{job_id}")
            final_status = {"status": "done"}
        else:
            raise AssertionError(
                f"Неожиданный Location при POST /search: {location!r}"
            )

        # ── Assert 1: статус done ─────────────────────────────────────────────
        assert final_status.get("status") == "done", (
            f"Assert 1 FAIL: ожидали status='done', получили {final_status}"
        )
        print("Assert 1 PASS: status=done")

        # ── Шаг 3: GET /results/{job_id} ─────────────────────────────────────
        print(f"Шаг 3: GET /results/{job_id}")
        sc_res, html, _headers = _get(f"/results/{job_id}")
        assert sc_res == 200, (
            f"/results/{job_id} вернул {sc_res}:\n{html[:400]}"
        )

        # ── Assert 2: города присутствуют, Москва выше Новосибирска ──────────
        assert "Москва" in html, "Assert 2 FAIL: 'Москва' не найдена в HTML"
        assert "Новосибирск" in html, (
            "Assert 2 FAIL: 'Новосибирск' не найдена в HTML"
        )
        pos_mos = html.index("Москва")
        pos_nov = html.index("Новосибирск")
        assert pos_mos < pos_nov, (
            f"Assert 2 FAIL: Москва (pos={pos_mos}) должна быть ДО "
            f"Новосибирска (pos={pos_nov}) — сортировка нарушена"
        )
        print(
            f"Assert 2 PASS: Москва (pos={pos_mos}) < Новосибирск (pos={pos_nov})"
        )

        # ── Assert 3: метро Москвы в HTML ────────────────────────────────────
        assert "Арбатская" in html, (
            "Assert 3 FAIL: 'Арбатская' не найдена в HTML"
        )
        assert "Тверская" in html, (
            "Assert 3 FAIL: 'Тверская' не найдена в HTML"
        )
        assert "Без метро" in html, (
            "Assert 3 FAIL: 'Без метро' не найдена в HTML"
        )
        print("Assert 3 PASS: станции метро Москвы присутствуют в HTML")

        # ── Assert 4: кликабельные ссылки и числа просм/день ─────────────────
        assert 'href="https://www.avito.ru/' in html, (
            "Assert 4 FAIL: href с URL объявления не найден в HTML"
        )
        assert "просм" in html, (
            "Assert 4 FAIL: текст 'просм' не найден в ячейках топ"
        )
        print("Assert 4 PASS: ссылки и просмотры в топ-ячейках присутствуют")

        # ── Assert 5: GET /export.csv ─────────────────────────────────────────
        # Передаём count=30 и те же фильтры и города, чтобы попасть в тот же ключ кэша
        print("Шаг 4: GET /export.csv (с городами и count=30)")
        sc_csv, csv_bytes, csv_headers = _get_bytes(
            "/export.csv",
            params={
                "query": "смоук тест",
                "count": "30",
                "price_min": "1000",
                "price_max": "5000",
                # Повторяющийся query-параметр cities
                "cities": TEST_CITY_SLUGS,
            },
        )
        assert sc_csv == 200, (
            f"Assert 5 FAIL: /export.csv вернул {sc_csv}"
        )
        ct = csv_headers.get("Content-Type", csv_headers.get("content-type", ""))
        assert "csv" in ct.lower(), (
            f"Assert 5 FAIL: content-type не csv: {ct}"
        )

        # Декодируем UTF-8-sig (снимает BOM)
        try:
            csv_text = csv_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise AssertionError(f"Assert 5 FAIL: CSV не декодируется как utf-8-sig: {e}")

        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)

        assert rows, "Assert 5 FAIL: CSV пустой"
        header = rows[0]
        assert "Город" in header, (
            f"Assert 5 FAIL: нет 'Город' в заголовке CSV: {header}"
        )
        assert "Адрес/метро" in header, (
            f"Assert 5 FAIL: нет 'Адрес/метро' в заголовке CSV: {header}"
        )
        assert "Среднее просм/день" in header, (
            f"Assert 5 FAIL: нет 'Среднее просм/день' в заголовке CSV: {header}"
        )

        # Оба города присутствуют в строках CSV
        city_col = [row[0] for row in rows[1:] if row]
        assert "Москва" in city_col, (
            f"Assert 5 FAIL: 'Москва' не в CSV. Первые города: {city_col[:5]}"
        )
        assert "Новосибирск" in city_col, (
            f"Assert 5 FAIL: 'Новосибирск' не в CSV. Первые города: {city_col[:5]}"
        )

        # Для Москвы есть строки по станциям метро
        metro_cells = [
            row[1] for row in rows[1:]
            if row and row[0] == "Москва"
        ]
        assert any("Арбатская" in v for v in metro_cells), (
            f"Assert 5 FAIL: 'м. Арбатская' не найдена в CSV. "
            f"Значения Адрес/метро для Москвы: {metro_cells}"
        )
        assert any("Тверская" in v for v in metro_cells), (
            f"Assert 5 FAIL: 'м. Тверская' не найдена в CSV. "
            f"Значения Адрес/метро для Москвы: {metro_cells}"
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
