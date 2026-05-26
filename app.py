"""
FastAPI-сервер для анализа спроса на Авито.

Запуск: python app.py → http://127.0.0.1:8000
"""

import asyncio
import csv
import io
import logging
import os
import urllib.parse
import uuid
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import analytics
import cache as cache_mod
import parser as avito_parser
from cities import CITIES, City, get_cities_by_slugs, get_city_by_slug
from filters import SearchFilters
from parser import AvitoBlockedError

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

# Подключаем статику и шаблоны
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# Хранилище задач в памяти
# Структура: {job_id: {status, done, total, current, results, error, ...}}
# ---------------------------------------------------------------------------

JOBS: dict[str, dict[str, Any]] = {}


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
async def index(request: Request) -> HTMLResponse:
    """Главная страница с формой поиска. Передаёт список городов для выбора."""
    return templates.TemplateResponse("index.html", {"request": request, "cities": CITIES})


@app.post("/search")
async def search(
    request: Request,
    query: str = Form(...),
    count: int = Form(150),
    price_min: str = Form(""),
    price_max: str = Form(""),
    cities: list[str] = Form([]),
    gender: list[str] = Form([]),
) -> RedirectResponse:
    """
    Обрабатывает поисковый запрос.

    Если результат есть в кэше — редиректит на страницу результатов.
    Иначе создаёт задачу и редиректит на страницу прогресса.

    Args:
        query:     поисковый запрос пользователя
        count:     количество объявлений на город (зажимается в диапазон [1, 500])
        price_min: минимальная цена (сырая строка из формы)
        price_max: максимальная цена (сырая строка из формы)
        cities:    список slug'ов выбранных городов (повторяющееся поле формы)
        gender:    список значений чекбоксов пола ("male"/"female"); 0 или 2 → без фильтра
    """
    query = query.strip()
    if not query:
        return RedirectResponse(url="/", status_code=303)

    # Ограничиваем диапазоном [1, 500] без ошибки пользователю
    count = max(1, min(500, count))

    # Разбираем чекбоксы пола: ровно 1 значение → фильтр; 0 или 2 → без фильтра
    valid_g = [g for g in gender if g in ("male", "female")]
    gender_val = valid_g[0] if len(valid_g) == 1 else None

    # Нормализуем фильтры (цена + пол) из формы
    filters = SearchFilters.from_form(price_min, price_max, gender_val)

    # Поля для шаблонов и CSV-ссылки
    price_desc = filters.describe()
    price_min_val = filters.price_min if filters.price_min is not None else ""
    price_max_val = filters.price_max if filters.price_max is not None else ""

    # Валидация городов: оставляем только известные slug'и
    valid_slugs = [s for s in cities if get_city_by_slug(s) is not None]
    if not valid_slugs:
        # Нет ни одного корректного города — возвращаем на главную
        logger.warning("POST /search: список городов пуст или содержит только неизвестные slug'и")
        return RedirectResponse(url="/", status_code=303)

    # Получаем объекты City в порядке убывания населения
    selected_cities = get_cities_by_slugs(valid_slugs)

    # Ключ кэша по набору городов: отсортированные slug'и через запятую
    cities_key = ",".join(sorted(c.slug for c in selected_cities))

    # Проверяем кэш (ключ включает count, фильтры и набор городов)
    cached = cache_mod.get_result(
        query, count,
        filters_key=filters.cache_key_part(),
        cities_key=cities_key,
    )
    if cached is not None:
        logger.info(
            "Кэш найден для запроса '%s' (count=%d, filters=%s, cities=%s), редирект на результаты",
            query, count, filters.cache_key_part(), cities_key,
        )
        # Создаём «готовую» задачу из кэша, чтобы results.html мог её показать
        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "status": "done",
            "done": len(selected_cities),
            "total": len(selected_cities),
            "current": "",
            "results": cached,
            "error": None,
            # Сохраняем запрос, count, фильтры и города для шаблонов и ссылки «Скачать CSV»
            "query": query,
            "count": count,
            "price_desc": price_desc,
            "price_min": price_min_val,
            "price_max": price_max_val,
            "cities_count": len(selected_cities),
            "selected_slugs": [c.slug for c in selected_cities],
            "selected_gender": [gender_val] if gender_val else [],
        }
        return RedirectResponse(url=f"/results/{job_id}", status_code=303)

    # Создаём новую задачу
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "running",
        "done": 0,
        "total": len(selected_cities),
        "current": selected_cities[0].name if selected_cities else "",
        "results": None,
        "error": None,
        # Сохраняем запрос, count, фильтры и города для шаблонов и ссылки «Скачать CSV»
        "query": query,
        "count": count,
        "price_desc": price_desc,
        "price_min": price_min_val,
        "price_max": price_max_val,
        "cities_count": len(selected_cities),
        "selected_slugs": [c.slug for c in selected_cities],
        "selected_gender": [gender_val] if gender_val else [],
    }

    # Запускаем фоновый парсинг с выбранными городами
    asyncio.create_task(run_job(job_id, query, count, filters, selected_cities))
    logger.info(
        "Создана задача %s для запроса '%s' (count=%d, filters=%s, cities=%s)",
        job_id, query, count, filters.cache_key_part(), cities_key,
    )

    return RedirectResponse(url=f"/progress/{job_id}", status_code=303)


@app.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress_page(request: Request, job_id: str) -> HTMLResponse:
    """Страница прогресса — показывает статус парсинга через JS-опрос."""
    if job_id not in JOBS:
        return HTMLResponse("<h2>Задача не найдена</h2>", status_code=404)

    job = JOBS[job_id]
    return templates.TemplateResponse(
        "progress.html",
        {
            "request": request,
            "job_id": job_id,
            "query": job.get("query", ""),
            "count": job.get("count", 150),
            "price_desc": job.get("price_desc", ""),
            "cities_count": job.get("cities_count", 0),
        },
    )


@app.get("/status/{job_id}")
async def job_status(job_id: str) -> JSONResponse:
    """JSON-эндпоинт для опроса состояния задачи из JS."""
    if job_id not in JOBS:
        return JSONResponse({"status": "not_found"}, status_code=404)

    job = JOBS[job_id]
    return JSONResponse(
        {
            "status": job["status"],
            "done": job["done"],
            "total": job["total"],
            "current": job["current"],
            "error": job["error"],
        }
    )


@app.get("/results/{job_id}", response_class=HTMLResponse)
async def results_by_job(request: Request, job_id: str) -> HTMLResponse:
    """Страница результатов по ID задачи."""
    if job_id not in JOBS:
        return HTMLResponse("<h2>Задача не найдена</h2>", status_code=404)

    job = JOBS[job_id]

    if job["status"] != "done":
        # Если задача ещё не завершена — перенаправляем на прогресс
        return RedirectResponse(url=f"/progress/{job_id}", status_code=303)

    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "results": job.get("results") or [],
            "query": job.get("query", ""),
            "error": job.get("error"),
            "count": job.get("count", 150),
            "price_desc": job.get("price_desc", ""),
            "price_min": job.get("price_min", ""),
            "price_max": job.get("price_max", ""),
            "cities_count": job.get("cities_count", 0),
            "selected_slugs": job.get("selected_slugs", []),
            "selected_gender": job.get("selected_gender", []),
        },
    )


@app.get("/results", response_class=HTMLResponse)
async def results_from_cache(
    request: Request,
    query: str = "",
    count: int = 150,
    price_min: str = "",
    price_max: str = "",
    cities: list[str] = Query([]),
    gender: list[str] = Query([]),
) -> HTMLResponse:
    """Страница результатов по запросу из кэша (GET /results?query=...&count=...)."""
    query = query.strip()

    # Разбираем параметр пола: ровно 1 значение → фильтр; 0 или 2 → без фильтра
    valid_g = [g for g in gender if g in ("male", "female")]
    gender_val = valid_g[0] if len(valid_g) == 1 else None

    # Нормализуем фильтры (цена + пол) из параметров запроса
    filters = SearchFilters.from_form(price_min, price_max, gender_val)

    # Валидируем и разрешаем города
    valid_slugs = [s for s in cities if get_city_by_slug(s) is not None]
    selected_cities = get_cities_by_slugs(valid_slugs)
    cities_key = ",".join(sorted(valid_slugs))

    cached: Optional[list] = (
        cache_mod.get_result(
            query, count,
            filters_key=filters.cache_key_part(),
            cities_key=cities_key,
        )
        if query else None
    )

    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "results": cached or [],
            "query": query,
            "error": None if cached else "Результаты не найдены в кэше.",
            "count": count,
            "price_desc": filters.describe(),
            "price_min": filters.price_min if filters.price_min is not None else "",
            "price_max": filters.price_max if filters.price_max is not None else "",
            "cities_count": len(selected_cities),
            "selected_slugs": [c.slug for c in selected_cities],
            "selected_gender": [gender_val] if gender_val else [],
        },
    )


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
