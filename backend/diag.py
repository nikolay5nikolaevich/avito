"""
Диагностический скрипт для отладки парсера Авито.

Запуск:
    python diag.py "поисковый запрос" [slug] [--cdp] [--port N]

Где:
    slug    — city slug из cities.py (по умолчанию «moskva»)
    --cdp   — подключиться к уже запущенному Chrome пользователя по CDP
    --port  — порт для CDP (по умолчанию 9222)

Сохраняет артефакты в папку debug/:
    search.html  — HTML страницы поиска
    search.png   — скриншот страницы поиска
    item.html    — HTML первого объявления
    item.png     — скриншот первого объявления
    diag.log     — полный лог DEBUG

Примеры:
    python diag.py "диван угловой"
    python diag.py "диван угловой" spb
    python diag.py "диван угловой" --cdp
    python diag.py "диван угловой" spb --cdp --port 9223
"""

import asyncio
import logging
import pathlib
import sys
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Настройка логирования: консоль + файл debug/diag.log
# ---------------------------------------------------------------------------

DEBUG_DIR = pathlib.Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

_log_formatter = logging.Formatter(
    fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Консольный хэндлер
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_log_formatter)

# Файловый хэндлер — debug/diag.log, utf-8
_file_handler = logging.FileHandler(
    DEBUG_DIR / "diag.log", mode="w", encoding="utf-8"
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_log_formatter)

# Корневой логгер
logging.basicConfig(level=logging.DEBUG, handlers=[_console_handler, _file_handler])

logger = logging.getLogger("diag")

# ---------------------------------------------------------------------------
# Импорты проекта (после настройки логирования)
# ---------------------------------------------------------------------------

from cities import CITIES, build_search_url, get_city_by_slug
from parser import (
    USER_AGENT,
    AvitoBlockedError,
    BLOCK_MARKERS,
    _check_block,
    _connect_over_cdp,            # CDP: подключение к Chrome пользователя
    _launch_persistent_context,   # persistent context с антидетект-мерами
    _extract_items_from_html,
    _extract_items_from_json,
    _item_from_json,
    _parse_item_page,
    _wait_for_cards_and_scroll,   # ожидание рендера карточек + прокрутка
    parse_avito_date,
)
from playwright.async_api import async_playwright


# ---------------------------------------------------------------------------
# Вспомогательная функция: случайная пауза
# ---------------------------------------------------------------------------

async def _delay(seconds: float = 1.5) -> None:
    """Пауза между переходами — имитируем человека."""
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Основная диагностика
# ---------------------------------------------------------------------------

async def run_diagnostics(
    query: str,
    slug: str,
    *,
    cdp_url: Optional[str] = None,
) -> None:
    """
    Выполняет пошаговую диагностику:
    1. Открывает страницу поиска, сохраняет HTML и скриншот.
    2. Проверяет блокировку.
    3. Извлекает ссылки на объявления (JSON → CSS резерв).
    4. Открывает первое объявление, сохраняет HTML и скриншот.
    5. Извлекает поля объявления и логирует их.

    cdp_url — если задан, подключаемся к Chrome пользователя по CDP
              вместо запуска нового браузера. Chrome пользователя НЕ закрывается.
    """
    # Проверяем, что slug известен
    city = get_city_by_slug(slug)
    if city is None:
        # Допускаем произвольный slug (Авито поддерживает больше городов)
        logger.warning(
            "Slug %r не найден в cities.py — продолжаю с произвольным slug", slug
        )
        from cities import City
        city = City(name=slug, slug=slug, has_metro=False, population=0)

    search_url = build_search_url(city.slug, query)
    logger.info("=== Диагностика ===")
    logger.info("Запрос: %r | Город: %s | URL: %s", query, city.name, search_url)

    use_cdp = cdp_url is not None
    if use_cdp:
        logger.info("Режим: CDP (подключение к Chrome пользователя по %s)", cdp_url)
    else:
        logger.info("Режим: persistent context (собственный браузер)")

    async with async_playwright() as pw:
        # Выбираем режим: CDP или собственный браузер
        if use_cdp:
            # CDP: подключаемся к Chrome пользователя — переиспользуем из parser.py
            _browser, context = await _connect_over_cdp(pw, cdp_url)  # type: ignore[arg-type]
        else:
            # Обычный режим: persistent context с антидетект-мерами.
            # headless=False: видимое окно позволяет решить капчу вручную.
            context = await _launch_persistent_context(pw, headless=False)

        # Открываем свою вкладку в браузере (в CDP — новая вкладка в Chrome пользователя)
        page = await context.new_page()

        blocked = False  # флаг: зафиксирована ли блокировка

        # -----------------------------------------------------------------------
        # Шаг 1: открываем страницу поиска и ждём рендера карточек
        # -----------------------------------------------------------------------
        logger.info("--- Шаг 1: загрузка страницы поиска + ожидание рендера ---")
        search_html: str = ""
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            # Авито — React-SPA: ждём, пока JS отрисует карточки в DOM,
            # и прокручиваем страницу для ленивого рендера.
            # Только после этого снимаем контент — иначе получим спиннер.
            await _wait_for_cards_and_scroll(page, search_url)
            search_html = await page.content()
            logger.info("Страница поиска загружена ПОСЛЕ рендера (%d байт)", len(search_html))
        except Exception as exc:
            logger.error("Не удалось загрузить страницу поиска: %s", exc)

        # -----------------------------------------------------------------------
        # Шаг 2: сохраняем HTML и скриншот страницы поиска (уже с карточками)
        # -----------------------------------------------------------------------
        logger.info("--- Шаг 2: сохранение артефактов страницы поиска ---")
        if search_html:
            try:
                search_html_path = DEBUG_DIR / "search.html"
                search_html_path.write_text(search_html, encoding="utf-8")
                logger.info("Сохранено: %s", search_html_path.resolve())
            except Exception as exc:
                logger.error("Ошибка сохранения search.html: %s", exc)

        # Скриншот — уже после рендера, виден результат с карточками
        try:
            search_png_path = DEBUG_DIR / "search.png"
            await page.screenshot(path=str(search_png_path), full_page=True)
            logger.info("Сохранено: %s", search_png_path.resolve())
        except Exception as exc:
            logger.error("Ошибка сохранения search.png: %s", exc)

        # -----------------------------------------------------------------------
        # Шаг 3: проверка блокировки
        # -----------------------------------------------------------------------
        logger.info("--- Шаг 3: проверка блокировки ---")
        if search_html:
            try:
                # Передаём актуальный заголовок страницы для точного детекта
                try:
                    _search_title = await page.title()
                except Exception:
                    _search_title = ""
                logger.info("Заголовок страницы: %r", _search_title)
                _check_block(search_html, search_url, page_title=_search_title)
                logger.info("Блокировка не обнаружена")
            except AvitoBlockedError as exc:
                blocked = True
                logger.warning("БЛОКИРОВКА: %s", exc)
                if use_cdp:
                    logger.warning(
                        "Авито заблокировал даже Chrome пользователя. "
                        "Возможно, нужно прогреть сессию вручную в браузере."
                    )
                    print(
                        "\n[!] Авито заблокировал запрос даже в Chrome пользователя.\n"
                        "    Откройте avito.ru вручную в этом же Chrome и подождите,\n"
                        "    пока сайт не загрузится без ошибок. Затем повторите.\n"
                    )
                else:
                    logger.warning(
                        "Авито заблокировал IP. "
                        "HTML/скрин сохранены в debug/ для анализа"
                    )
                    print(
                        "\n[!] Авито заблокировал IP. "
                        "HTML/скрин сохранены в debug/ для анализа\n"
                    )
                # Продолжаем — вдруг частично что-то есть
        else:
            logger.warning("HTML пустой — пропускаем проверку блокировки")

        # -----------------------------------------------------------------------
        # Шаг 4: извлечение ссылок на объявления
        # -----------------------------------------------------------------------
        logger.info("--- Шаг 4: извлечение ссылок на объявления ---")
        items: list[dict[str, Any]] = []

        if search_html:
            try:
                # Основной метод — CSS из отрисованного DOM (после ожидания рендера).
                # JSON-блок (mime/invalid) — резерв, если CSS не сработал.
                css_items = _extract_items_from_html(search_html)
                if css_items:
                    items = css_items
                    logger.info("CSS-метод: найдено %d карточек", len(css_items))
                else:
                    logger.info(
                        "CSS-метод не дал результатов — пробуем JSON-резерв"
                    )
                    raw_json_items = _extract_items_from_json(search_html)
                    if raw_json_items:
                        logger.info(
                            "JSON-резерв: найдено %d карточек", len(raw_json_items)
                        )
                        for raw in raw_json_items:
                            if isinstance(raw, dict) and raw.get("url", "").startswith("http"):
                                items.append(raw)
                            else:
                                try:
                                    items.append(_item_from_json(raw))
                                except Exception as conv_exc:
                                    logger.debug(
                                        "Ошибка конвертации JSON-записи: %s", conv_exc
                                    )
                    else:
                        logger.warning("Оба метода не нашли карточек")

            except Exception as exc:
                logger.error("Ошибка извлечения карточек: %s", exc)

        # Логируем первые 3 URL
        if items:
            logger.info("Итого карточек: %d", len(items))
            for i, it in enumerate(items[:3], start=1):
                logger.info("  URL %d: %s", i, it.get("url", "(нет url)"))
        else:
            logger.warning("Карточки не найдены")

        # -----------------------------------------------------------------------
        # Шаг 5: открываем первое объявление
        # -----------------------------------------------------------------------
        logger.info("--- Шаг 5: загрузка страницы первого объявления ---")
        first_item: Optional[dict[str, Any]] = None

        if items:
            first_url = items[0].get("url", "")
            if first_url:
                logger.info("Открываем: %s", first_url)
                item_html: str = ""
                try:
                    await _delay(1.5)
                    await page.goto(
                        first_url, wait_until="domcontentloaded", timeout=30_000
                    )
                    # Ждём рендера ключевого элемента объявления (тоже React-SPA)
                    _item_ready_selectors = [
                        "[data-marker='item-view/total-views']",
                        "[data-marker='item-view/today-views']",
                        "h1[class*='title']",
                        "h1",
                    ]
                    _rendered = False
                    for _sel in _item_ready_selectors:
                        try:
                            await page.wait_for_selector(
                                _sel, timeout=10_000, state="attached"
                            )
                            logger.debug("Объявление отрисовано (%s)", _sel)
                            _rendered = True
                            break
                        except Exception:
                            pass
                    if not _rendered:
                        logger.warning(
                            "Ключевые элементы объявления не появились — читаем как есть"
                        )
                    # Небольшая пауза — подгружаются счётчики просмотров
                    await asyncio.sleep(1.0)
                    item_html = await page.content()
                    logger.info(
                        "Страница объявления загружена ПОСЛЕ рендера (%d байт)",
                        len(item_html),
                    )
                except Exception as exc:
                    logger.error("Не удалось загрузить объявление: %s", exc)

                # Сохраняем HTML объявления (уже с данными, не спиннер)
                if item_html:
                    try:
                        item_html_path = DEBUG_DIR / "item.html"
                        item_html_path.write_text(item_html, encoding="utf-8")
                        logger.info("Сохранено: %s", item_html_path.resolve())
                    except Exception as exc:
                        logger.error("Ошибка сохранения item.html: %s", exc)

                # Сохраняем скриншот объявления (уже с данными)
                try:
                    item_png_path = DEBUG_DIR / "item.png"
                    await page.screenshot(
                        path=str(item_png_path), full_page=True
                    )
                    logger.info("Сохранено: %s", item_png_path.resolve())
                except Exception as exc:
                    logger.error("Ошибка сохранения item.png: %s", exc)

                first_item = items[0]
            else:
                logger.warning("У первой карточки нет URL — пропускаем")
        else:
            logger.warning("Нет карточек — пропускаем шаг 5 и 6")

        # -----------------------------------------------------------------------
        # Шаг 6: извлечение полей объявления
        # -----------------------------------------------------------------------
        logger.info("--- Шаг 6: извлечение полей объявления ---")

        if first_item and first_item.get("url"):
            try:
                # Переиспользуем _parse_item_page из parser.py
                enriched = await _parse_item_page(page, dict(first_item))
                logger.info("Извлечённые поля объявления:")
                fields = [
                    "url", "title", "price", "address",
                    "metro", "views_total", "views_today",
                    "published_at", "age_hours",
                ]
                for field in fields:
                    val = enriched.get(field)
                    logger.info("  %-15s = %r", field, val)
            except AvitoBlockedError as exc:
                logger.warning("Блокировка при парсинге объявления: %s", exc)
            except Exception as exc:
                logger.error("Ошибка извлечения полей: %s", exc)
        else:
            logger.warning("Первое объявление недоступно — шаг 6 пропущен")

        # -----------------------------------------------------------------------
        # Завершение: закрываем только свою страницу
        # -----------------------------------------------------------------------
        try:
            await page.close()
        except Exception:
            pass

        if use_cdp:
            # CDP: НЕ закрываем context/browser — это Chrome пользователя
            logger.info("CDP: свои вкладки закрыты, Chrome пользователя сохранён")
        else:
            # Обычный режим: persistent context закрываем полностью
            await context.close()

    logger.info("=== Диагностика завершена ===")

    # Финальная инструкция пользователю — через print, чтобы точно увидел
    print(
        "\n"
        "Готово. Передай Claude файлы из папки debug/:\n"
        "  search.html, item.html, diag.log\n"
        "  (скриншоты search.png, item.png — по желанию)\n"
        "— по ним он поправит селекторы."
    )


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def _usage() -> None:
    """Выводит подсказку по использованию и завершает программу."""
    print(
        "Использование:\n"
        "    python diag.py \"поисковый запрос\" [slug] [--cdp] [--port N]\n"
        "\n"
        "Аргументы:\n"
        "    поисковый запрос  — то, что ищем на Авито (обязательно, в кавычках)\n"
        "    slug              — город из cities.py (необязательно, по умолчанию moskva)\n"
        "    --cdp             — подключиться к Chrome пользователя по CDP\n"
        "                        (вместо запуска нового браузера)\n"
        "    --port N          — порт для CDP (необязательно, по умолчанию 9222)\n"
        "\n"
        "Доступные slugи:\n"
        + "\n".join(
            f"    {city.slug:<25} {city.name}" for city in CITIES
        )
        + "\n\n"
        "──────────────────────────────────────────────────────────────────────\n"
        "Режим --cdp: как пользоваться\n"
        "──────────────────────────────────────────────────────────────────────\n"
        "\n"
        "Шаг 1. Запусти Chrome с удалённой отладкой (в отдельном окне PowerShell):\n"
        "\n"
        '    & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'
        " --remote-debugging-port=9222"
        ' --user-data-dir="C:\\Users\\TBG\\avito-chrome-profile"\n'
        "\n"
        "Шаг 2. В открывшемся Chrome вручную зайди на avito.ru и подожди,\n"
        "        пока страница загрузится без ошибок (прогрев сессии).\n"
        "\n"
        "Шаг 3. Запусти diag.py с флагом --cdp:\n"
        "\n"
        "    python diag.py \"диван угловой\" --cdp\n"
        "    python diag.py \"диван угловой\" spb --cdp --port 9222\n"
        "\n"
        "Важно: НЕ закрывай Chrome между шагом 1 и 3.\n"
        "        diag.py откроет новую вкладку в твоём Chrome и закроет её\n"
        "        после завершения. Сам браузер останется открытым.\n"
        "──────────────────────────────────────────────────────────────────────\n"
        "\n"
        "Примеры без --cdp (собственный браузер):\n"
        "    python diag.py \"диван угловой\"\n"
        "    python diag.py \"диван угловой\" sankt-peterburg\n"
    )


def main() -> None:
    """Парсит аргументы командной строки и запускает диагностику."""
    args = sys.argv[1:]  # без имени скрипта

    if not args:
        _usage()
        sys.exit(0)

    # Выбираем из args: первый не-флаг — запрос, второй (если есть и не флаг) — slug
    # Флаги: --cdp, --port N
    positional: list[str] = []
    use_cdp: bool = False
    cdp_port: int = 9222

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--cdp":
            use_cdp = True
        elif arg == "--port":
            i += 1
            if i < len(args):
                try:
                    cdp_port = int(args[i])
                except ValueError:
                    print(f"Ошибка: --port ожидает целое число, получено {args[i]!r}\n")
                    _usage()
                    sys.exit(1)
            else:
                print("Ошибка: --port требует значение (номер порта).\n")
                _usage()
                sys.exit(1)
        else:
            positional.append(arg)
        i += 1

    if not positional:
        _usage()
        sys.exit(0)

    query = positional[0].strip()
    if not query:
        print("Ошибка: поисковый запрос не может быть пустым.\n")
        _usage()
        sys.exit(1)

    slug = positional[1].strip() if len(positional) > 1 else "moskva"

    # Формируем CDP URL, если нужен
    cdp_url: Optional[str] = None
    if use_cdp:
        cdp_url = f"http://localhost:{cdp_port}"
        logger.info("CDP-режим включён: %s", cdp_url)

    logger.debug("Аргументы: query=%r, slug=%r, cdp_url=%r", query, slug, cdp_url)
    asyncio.run(run_diagnostics(query, slug, cdp_url=cdp_url))


if __name__ == "__main__":
    main()
