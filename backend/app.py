"""
FastAPI-сервер для анализа спроса на Авито.

Запуск из корня проекта: python backend/app.py → http://127.0.0.1:8000
"""

import asyncio
import csv
import io
import logging
import os
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

import analytics
import cache as cache_mod
import parser as avito_parser
from cities import CITIES, City, get_cities_by_slugs, get_city_by_slug
from filters import SearchFilters
from parser import AvitoBlockedError

# ---------------------------------------------------------------------------
# Корень проекта и рабочая директория.
# Код бэкенда лежит в backend/, но все артефакты (cache.db, logs/, debug/,
# .pw-profile/) и собранный фронтенд (frontend/dist/) находятся в корне проекта.
# Переходим в корень, чтобы относительные пути резолвились одинаково независимо
# от того, откуда запущен сервер.
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# ---------------------------------------------------------------------------
# CDP: подключение к Chrome, запущенному пользователем (start-chrome.bat).
# Полноценный парсинг Авито работает ТОЛЬКО через этот реальный браузер —
# свой Playwright-браузер Авито детектит и банит. Переопределяется переменной
# окружения AVITO_CDP_URL; пустая строка → парсер поднимет собственный браузер.
# ---------------------------------------------------------------------------

CDP_URL: Optional[str] = os.environ.get("AVITO_CDP_URL", "http://localhost:9222") or None

# Сколько объявлений парсить на город по умолчанию (150 по ТЗ).
# Для быстрой проверки сайта можно временно уменьшить:
#   PowerShell:  $env:AVITO_MAX_ITEMS = "10"
# Пользователь может задать произвольное количество через форму (диапазон [1, 500]).
try:
    MAX_ITEMS_PER_CITY: int = int(os.environ.get("AVITO_MAX_ITEMS", "150"))
except ValueError:
    MAX_ITEMS_PER_CITY = 150

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

import pathlib as _pathlib

# Создаём папку logs/ если её нет
_logs_dir = _pathlib.Path("logs")
_logs_dir.mkdir(exist_ok=True)

_log_fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_log_datefmt = "%Y-%m-%d %H:%M:%S"
_formatter = logging.Formatter(fmt=_log_fmt, datefmt=_log_datefmt)

# Консольный хэндлер
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_formatter)

# Файловый хэндлер — logs/app.log, utf-8, режим append
_file_handler = logging.FileHandler(
    _logs_dir / "app.log", mode="a", encoding="utf-8"
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _file_handler],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Приложение FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="Авито — анализ спроса")

# Собранный React-фронтенд (Vite build). Раздаётся через маршруты ниже.
FRONTEND_DIST_DIR = Path("frontend") / "dist"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"

# ---------------------------------------------------------------------------
# Хранилище задач в памяти
# Структура: {job_id: {status, done, total, current, results, error, ...}}
# ---------------------------------------------------------------------------

JOBS: dict[str, dict[str, Any]] = {}


def _serialize_city(city: City) -> dict[str, Any]:
    """JSON-представление города для frontend bootstrap."""
    return {
        "name": city.name,
        "slug": city.slug,
        "has_metro": city.has_metro,
        "population": city.population,
    }


def _frontend_index_response() -> Response:
    """Отдаёт собранный frontend или понятное сообщение, если build отсутствует."""
    index_path = FRONTEND_DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)

    return HTMLResponse(
        """
        <h2>Frontend build not found</h2>
        <p>Open <code>frontend/</code>, install dependencies and run <code>npm run build</code>.</p>
        """,
        status_code=503,
    )


def _job_export_url(job: dict[str, Any]) -> str:
    """Формирует URL экспорта CSV из данных задачи."""
    params: list[tuple[str, str]] = [
        ("query", job.get("query", "")),
        ("count", str(job.get("count", 150))),
        ("price_min", str(job.get("price_min", ""))),
        ("price_max", str(job.get("price_max", ""))),
    ]

    for slug in job.get("selected_slugs", []):
        params.append(("cities", slug))
    for gender_value in job.get("selected_gender", []):
        params.append(("gender", gender_value))

    return f"/export.csv?{urllib.parse.urlencode(params)}"


def _serialize_job_status(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """JSON-представление статуса задачи."""
    return {
        "job_id": job_id,
        "status": job["status"],
        "done": job["done"],
        "total": job["total"],
        "current": job["current"],
        "error": job["error"],
    }


def _serialize_job_results(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """JSON-представление результатов задачи."""
    return {
        **_serialize_job_status(job_id, job),
        "query": job.get("query", ""),
        "count": job.get("count", 150),
        "price_desc": job.get("price_desc", ""),
        "price_min": job.get("price_min", ""),
        "price_max": job.get("price_max", ""),
        "cities_count": job.get("cities_count", 0),
        "selected_slugs": job.get("selected_slugs", []),
        "selected_gender": job.get("selected_gender", []),
        "results": job.get("results") or [],
        "export_url": _job_export_url(job),
    }


def _parse_count(raw_count: Any) -> int:
    """Безопасно приводит count к int и зажимает в диапазон [1, 500]."""
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        count = 150

    return max(1, min(500, count))


def _normalize_search_payload(
    query: str,
    count: Any,
    price_min: Any,
    price_max: Any,
    cities: list[str],
    gender: list[str],
) -> Optional[dict[str, Any]]:
    """Нормализует данные поиска для form- и JSON-маршрутов."""
    clean_query = (query or "").strip()
    if not clean_query:
        return None

    safe_count = _parse_count(count)
    valid_gender = [value for value in gender if value in ("male", "female")]
    gender_value = valid_gender[0] if len(valid_gender) == 1 else None

    filters = SearchFilters.from_form(
        "" if price_min is None else str(price_min),
        "" if price_max is None else str(price_max),
        gender_value,
    )

    valid_slugs = [slug for slug in cities if get_city_by_slug(slug) is not None]
    if not valid_slugs:
        return None

    selected_cities = get_cities_by_slugs(valid_slugs)
    cities_key = ",".join(sorted(city.slug for city in selected_cities))

    return {
        "query": clean_query,
        "count": safe_count,
        "filters": filters,
        "price_desc": filters.describe(),
        "price_min": filters.price_min if filters.price_min is not None else "",
        "price_max": filters.price_max if filters.price_max is not None else "",
        "selected_cities": selected_cities,
        "cities_key": cities_key,
        "selected_gender": [gender_value] if gender_value else [],
    }


def _create_done_job(search_data: dict[str, Any], cached_results: list[dict]) -> str:
    """Создаёт задачу из кэша и помечает её завершённой."""
    job_id = str(uuid.uuid4())
    selected_cities = search_data["selected_cities"]

    JOBS[job_id] = {
        "status": "done",
        "done": len(selected_cities),
        "total": len(selected_cities),
        "current": "",
        "results": cached_results,
        "error": None,
        "query": search_data["query"],
        "count": search_data["count"],
        "price_desc": search_data["price_desc"],
        "price_min": search_data["price_min"],
        "price_max": search_data["price_max"],
        "cities_count": len(selected_cities),
        "selected_slugs": [city.slug for city in selected_cities],
        "selected_gender": search_data["selected_gender"],
    }
    return job_id


def _create_running_job(search_data: dict[str, Any]) -> str:
    """Создаёт фоновой job и запускает парсинг."""
    job_id = str(uuid.uuid4())
    selected_cities = search_data["selected_cities"]

    JOBS[job_id] = {
        "status": "running",
        "done": 0,
        "total": len(selected_cities),
        "current": selected_cities[0].name if selected_cities else "",
        "results": None,
        "error": None,
        "query": search_data["query"],
        "count": search_data["count"],
        "price_desc": search_data["price_desc"],
        "price_min": search_data["price_min"],
        "price_max": search_data["price_max"],
        "cities_count": len(selected_cities),
        "selected_slugs": [city.slug for city in selected_cities],
        "selected_gender": search_data["selected_gender"],
    }

    asyncio.create_task(
        run_job(
            job_id,
            search_data["query"],
            search_data["count"],
            search_data["filters"],
            selected_cities,
        )
    )
    return job_id


def _prepare_search_job(search_data: dict[str, Any]) -> tuple[str, bool]:
    """Создаёт задачу из кэша или запускает новую."""
    cached = cache_mod.get_result(
        search_data["query"],
        search_data["count"],
        filters_key=search_data["filters"].cache_key_part(),
        cities_key=search_data["cities_key"],
    )
    if cached is not None:
        return _create_done_job(search_data, cached), True

    return _create_running_job(search_data), False


# ---------------------------------------------------------------------------
# Инициализация при старте
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    """Инициализируем кэш-базу при старте сервера."""
    cache_mod.init_db()
    logger.info("SQLite-кэш инициализирован")


# ---------------------------------------------------------------------------
# Фоновая задача: запускает парсер и сохраняет результаты в JOBS
# ---------------------------------------------------------------------------

async def run_job(
    job_id: str,
    query: str,
    count: int,
    filters: SearchFilters,
    cities: list[City],
) -> None:
    """
    Запускает парсинг выбранных городов, считает метрики и сохраняет результат
    в JOBS[job_id] и в SQLite-кэш.

    Args:
        job_id:   идентификатор задачи в JOBS
        query:    поисковый запрос пользователя
        count:    количество объявлений на город (1–500)
        filters:  фильтры поиска (цена и др.)
        cities:   список городов для парсинга (подмножество CITIES)
    """
    job = JOBS[job_id]

    def progress_cb(done: int, total: int, city_name: str) -> None:
        """Callback из парсера — обновляем состояние задачи."""
        job["done"] = done
        job["total"] = total
        job["current"] = city_name
        logger.info("Прогресс: %d/%d — %s", done, total, city_name)

    try:
        # Запускаем парсинг с выбранным количеством объявлений, фильтрами и городами
        raw_results: dict[str, list[dict]] = await avito_parser.parse_all(
            query,
            progress_cb=progress_cb,
            headless=False,
            cdp_url=CDP_URL,
            max_items=count,
            filters=filters,
            cities=cities,
        )

        # Считаем метрики только по выбранным городам; знаменатель = число местных объявлений
        city_results: list[dict] = []
        for city in cities:
            listings = raw_results.get(city.slug, [])
            try:
                result = analytics.compute_city_result(city, listings)
                city_results.append(result)
            except Exception as exc:
                logger.error(
                    "Ошибка при расчёте метрик для %s: %s", city.name, exc
                )

        # Сортируем по убыванию среднего просмотров/день
        city_results.sort(key=lambda x: x["avg_views_today"], reverse=True)

        # Формируем cities_key для изоляции кэша по набору городов
        cities_key = ",".join(sorted(c.slug for c in cities))

        # Сохраняем в кэш (ключ учитывает count, фильтры и набор городов) и в состояние задачи
        cache_mod.save_result(
            query, city_results, count,
            filters_key=filters.cache_key_part(),
            cities_key=cities_key,
        )
        job["results"] = city_results
        job["status"] = "done"
        job["done"] = job["total"]
        logger.info("Задача %s завершена. Городов: %d", job_id, len(city_results))

    except AvitoBlockedError as exc:
        logger.error("Блокировка Авито при выполнении задачи %s: %s", job_id, exc)
        job["status"] = "blocked"
        job["error"] = (
            "Авито заблокировал запрос. Попробуйте включить VPN и повторить поиск."
        )

    except Exception as exc:
        logger.error("Непредвиденная ошибка задачи %s: %s", job_id, exc)
        job["status"] = "error"
        job["error"] = f"Ошибка парсинга: {exc}"


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> Response:
    """Главная страница с формой поиска. Передаёт список городов для выбора."""
    return _frontend_index_response()


@app.get("/workspace", response_class=HTMLResponse)
async def workspace_page() -> Response:
    """Отдельный route React workspace."""
    return _frontend_index_response()


@app.get("/assets/{asset_path:path}")
async def frontend_assets(asset_path: str) -> Response:
    """Раздаёт собранные Vite-ассеты."""
    file_path = FRONTEND_ASSETS_DIR / asset_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)

    return HTMLResponse("<h2>Asset not found</h2>", status_code=404)


@app.get("/api/bootstrap")
async def api_bootstrap() -> JSONResponse:
    """Возвращает начальные данные frontend."""
    return JSONResponse(
        {
            "app_name": "Avito Research",
            "cities": [_serialize_city(city) for city in CITIES],
            "defaults": {
                "count": 150,
                "selected_city_slugs": [city.slug for city in CITIES[:10]],
            },
            "limits": {
                "count_min": 1,
                "count_max": 500,
            },
        }
    )


@app.post("/api/search")
async def api_search(request: Request) -> JSONResponse:
    """JSON-версия запуска поиска для React frontend."""
    payload = await request.json()
    raw_cities = payload.get("cities") or []
    raw_gender = payload.get("gender") or []

    search_data = _normalize_search_payload(
        str(payload.get("query", "")),
        payload.get("count", 150),
        payload.get("price_min", ""),
        payload.get("price_max", ""),
        [str(slug) for slug in raw_cities],
        [str(value) for value in raw_gender],
    )
    if search_data is None:
        return JSONResponse(
            {"error": "Некорректный запрос поиска или не выбраны города."},
            status_code=400,
        )

    job_id, cached = _prepare_search_job(search_data)
    job = JOBS[job_id]

    logger.info(
        "API-задача %s для '%s' (cached=%s, cities=%s)",
        job_id,
        search_data["query"],
        cached,
        search_data["cities_key"],
    )
    return JSONResponse(
        {
            **_serialize_job_status(job_id, job),
            "cached": cached,
            "results_url": f"/api/results/{job_id}",
        }
    )


@app.get("/api/status/{job_id}")
async def api_job_status(job_id: str) -> JSONResponse:
    """JSON-статус задачи для опроса из frontend."""
    if job_id not in JOBS:
        return JSONResponse({"status": "not_found"}, status_code=404)

    job = JOBS[job_id]
    return JSONResponse(_serialize_job_status(job_id, job))


@app.get("/api/results/{job_id}")
async def api_results(job_id: str) -> JSONResponse:
    """JSON-результаты задачи для frontend."""
    if job_id not in JOBS:
        return JSONResponse({"status": "not_found"}, status_code=404)

    job = JOBS[job_id]
    return JSONResponse(_serialize_job_results(job_id, job))


@app.get("/export.csv")
async def export_csv(
    query: str = "",
    count: int = 150,
    price_min: str = "",
    price_max: str = "",
    cities: list[str] = Query([]),
    gender: list[str] = Query([]),
) -> StreamingResponse:
    """
    Экспорт результатов в CSV (UTF-8 с BOM для корректного открытия в Excel).

    Колонки: Город, Адрес/метро, Среднее просм/день, Топ-1, Топ-2, Топ-3.
    Для МСК/СПб добавляются строки по каждой станции метро.

    Args:
        query:     поисковый запрос
        count:     количество объявлений на город (используется как ключ кэша)
        price_min: минимальная цена (сырая строка; нормализуется для ключа кэша)
        price_max: максимальная цена (сырая строка; нормализуется для ключа кэша)
        cities:    список slug'ов выбранных городов (повторяющийся query-параметр)
        gender:    список значений чекбоксов пола ("male"/"female"); 0 или 2 → без фильтра
    """
    query = query.strip()

    # Разбираем параметр пола: ровно 1 значение → фильтр; 0 или 2 → без фильтра
    valid_g = [g for g in gender if g in ("male", "female")]
    gender_val = valid_g[0] if len(valid_g) == 1 else None

    # Нормализуем фильтры (цена + пол) — они влияют на ключ кэша
    filters = SearchFilters.from_form(price_min, price_max, gender_val)

    # Валидируем и разрешаем города (как в /results)
    valid_slugs = [s for s in cities if get_city_by_slug(s) is not None]
    cities_key = ",".join(sorted(valid_slugs))

    results: Optional[list] = (
        cache_mod.get_result(
            query, count,
            filters_key=filters.cache_key_part(),
            cities_key=cities_key,
        )
        if query else None
    )

    output = io.StringIO()
    # BOM добавляется при encode("utf-8-sig") ниже — здесь не нужен
    writer = csv.writer(output, dialect="excel")

    # Заголовок
    writer.writerow([
        "Город", "Адрес/метро", "Среднее просм/день",
        "Топ-1 (просм — ссылка)", "Топ-2 (просм — ссылка)", "Топ-3 (просм — ссылка)",
    ])

    if results:
        for city_result in results:
            city_name: str = city_result.get("city_name", "")
            avg: float = city_result.get("avg_views_today", 0.0)
            top3: list = city_result.get("top3") or []

            # Формируем топ-ячейки: «N просм — https://...»
            top_cells = _format_top3_cells(top3)

            # Основная строка города
            local_count = city_result.get("local_count", 0)
            writer.writerow([city_name, f"Весь город ({local_count} местных)", avg] + top_cells)

            # Строки по станциям метро (МСК/СПб)
            metro_bd: Optional[list] = city_result.get("metro_breakdown")
            if metro_bd:
                for station in metro_bd:
                    metro_name: str = station.get("metro", "")
                    metro_avg: float = station.get("avg_views_today", 0.0)
                    metro_top3: list = station.get("top3") or []
                    metro_cells = _format_top3_cells(metro_top3)
                    writer.writerow(
                        [city_name, f"м. {metro_name}", metro_avg] + metro_cells
                    )
    else:
        writer.writerow(["Нет данных — сначала выполните поиск", "", "", "", "", ""])

    content = output.getvalue()

    # Формируем имя файла.
    # Content-Disposition должен быть latin-1-кодируемым, поэтому:
    # - ASCII-запасное имя (только ASCII-символы)
    # - RFC 5987 filename* для юникодного имени (поддерживается всеми современными браузерами)
    safe_query = query.replace(" ", "_")[:40] if query else "result"
    # ASCII-fallback: убираем всё, что не ASCII
    ascii_name = "".join(c if ord(c) < 128 else "_" for c in safe_query)
    filename_ascii = f"avito_{ascii_name}.csv"
    # RFC 5987: percent-encode UTF-8 байты
    filename_utf8_encoded = urllib.parse.quote(f"avito_{safe_query}.csv", safe="")
    content_disposition = (
        f'attachment; filename="{filename_ascii}"; '
        f"filename*=UTF-8''{filename_utf8_encoded}"
    )

    return StreamingResponse(
        iter([content.encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition,
        },
    )


def _format_top3_cells(top3: list) -> list[str]:
    """
    Преобразует список топ-3 в три строки вида «N просм — https://...».
    Возвращает ровно 3 элемента (пустые строки для отсутствующих позиций).
    """
    cells: list[str] = []
    for item in top3[:3]:
        views: Optional[int] = item.get("views_today")
        url: str = item.get("url") or ""
        views_str = f"{views} просм" if views is not None else "? просм"
        cells.append(f"{views_str} — {url}" if url else views_str)

    # Добиваем до 3 пустых ячеек если топов меньше
    while len(cells) < 3:
        cells.append("")

    return cells


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
