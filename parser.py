"""
Асинхронный парсер объявлений Авито на базе Playwright.

Публичный API:
    parse_city(city, query, *, max_items=150, headless=False, cdp_url=None) -> list[dict]
    parse_all(query, *, progress_cb=None, headless=False, cdp_url=None) -> dict[str, list[dict]]

Контракт каждого объявления (dict):
    url          : str
    title        : str
    price        : int | None         — в рублях
    address      : str
    metro        : str | None         — станция метро или None
    views_total  : int | None
    views_today  : int | None         — ключевая метрика
    published_at : datetime | None
    age_hours    : float | None       — (now - published_at) в часах

ВАЖНО: Селекторы для страницы отдельного объявления (title, price, address,
date) помечены в selectors.py как «НЕ ПОДТВЕРЖДЕНО». Они написаны
по общей практике и могут потребовать правки после первого живого теста.
Просмотры (views_total, views_today) — «ПОДТВЕРЖДЕНО» из двух источников.
JSON-метод на странице поиска — «ПОДТВЕРЖДЕНО» (Duff89/parser_avito).

Антидетект-меры:
    - Запуск реального Chrome (channel="chrome") с fallback на Chromium.
    - Persistent-контекст (.pw-profile/) — куки/сессия сохраняются между запусками.
    - headless=False по умолчанию — видимое окно позволяет решить капчу руками.
    - Аргумент --disable-blink-features=AutomationControlled.
    - Init-скрипт маскирует navigator.webdriver, plugins, permissions и пр.
    - Реалистичный locale, timezone, viewport, extra_http_headers.

CDP-режим (cdp_url задан):
    - Подключаемся к уже запущенному пользовательским Chrome через CDP.
    - Chrome запускается ВРУЧНУЮ пользователем с флагом --remote-debugging-port.
    - Такой Chrome не содержит флагов автоматизации Playwright и выглядит
      для Авито как обычный пользователь.
    - При CDP НЕ закрываем браузер пользователя — только свои страницы.
"""

import asyncio
import json
import logging
import pathlib
import random
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from bs4 import BeautifulSoup
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

import avito_selectors as sel  # переименовано: selectors.py конфликтует со stdlib selectors
from cities import CITIES, City, build_search_url, is_local_listing

if TYPE_CHECKING:
    from filters import SearchFilters

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

MAX_ITEMS: int = 150

# Предохранитель пагинации: при узких запросах местные объявления могут
# закончиться раньше, чем наберётся max_items — ограничиваем число страниц.
MAX_SEARCH_PAGES: int = 20

# Базовый URL Авито
BASE_URL: str = "https://www.avito.ru"

# Задержки между запросами — имитируем человека
DELAY_MIN: float = 1.0
DELAY_MAX: float = 3.0

# User-Agent — актуальный Chrome 124 Desktop на Windows.
# При запуске реального Chrome (channel="chrome") этот UA не подставляется —
# Chrome сам отдаёт корректный UA. Используется только с Chromium-fallback.
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Папка для persistent-профиля браузера (куки, localStorage, сессия Авито).
# Добавьте .pw-profile/ в .gitignore, чтобы не коммитить личные данные.
_PROFILE_DIR: pathlib.Path = pathlib.Path(".pw-profile")

# Маркеры ЗАГОЛОВКОВ страниц-заглушек Авито.
# Сравниваем с page.title() — это точный, не подстрочный матч.
# Реальная выдача имеет title «Авито — Объявления…» → сюда не попадает.
BLOCK_TITLE_MARKERS: list[str] = [
    "Доступ ограничен",
    "Доступ временно ограничен",
    "проблема с IP",
]

# Маркеры, которые встречаются ТОЛЬКО в служебном HTML страниц-заглушек
# (firewall/капча-страницы Авито очень маленькие, ~30 КБ, без каталога).
# «captcha» и «robot» нарочно УБРАНЫ отсюда — они встречаются в обычном
# служебном JS настоящей страницы каталога (Авито добавляет аналитику
# антибот-систем в JS даже на нормальной выдаче).
BLOCK_HTML_MARKERS: list[str] = [
    "robot-checkbox",
    "cf-challenge",
    "Подтвердите, что вы не робот",
]

# Для обратной совместимости: объединённый список (используется в diag.py)
BLOCK_MARKERS: list[str] = BLOCK_TITLE_MARKERS + BLOCK_HTML_MARKERS

# Init-скрипт для маскировки автоматизации Playwright.
# Выполняется в каждой новой странице ДО загрузки HTML.
_STEALTH_SCRIPT: str = """
// Убираем флаг автоматизации
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true
});

// Русские языки — как у обычного пользователя из России
Object.defineProperty(navigator, 'languages', {
    get: () => ['ru-RU', 'ru'],
    configurable: true
});

// Эмулируем объект window.chrome, который есть в настоящем Chrome
if (!window.chrome) {
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };
}

// Эмулируем плагины — пустой массив выдаёт headless-режим
const pluginData = [
    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
];
const fakePlugins = Object.create(PluginArray.prototype);
Object.defineProperty(fakePlugins, 'length', { get: () => pluginData.length });
pluginData.forEach((p, i) => {
    const plugin = Object.create(Plugin.prototype);
    Object.defineProperty(plugin, 'name', { get: () => p.name });
    Object.defineProperty(plugin, 'filename', { get: () => p.filename });
    Object.defineProperty(plugin, 'description', { get: () => p.description });
    Object.defineProperty(fakePlugins, i, { get: () => plugin });
});
Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins, configurable: true });

// Патч navigator.permissions.query — chrome headless возвращает 'denied' для notifications
const originalQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
window.navigator.permissions.query = (parameters) => {
    if (parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission });
    }
    return originalQuery(parameters);
};
"""

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Исключение бана
# ---------------------------------------------------------------------------

class AvitoBlockedError(Exception):
    """Авито заблокировал запрос (капча или ограничение по IP)."""


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

async def _random_delay() -> None:
    """Случайная пауза между запросами — имитация человека."""
    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    await asyncio.sleep(delay)


def _check_block(html: str, url: str, *, page_title: str = "") -> None:
    """
    Проверяет страницу на признаки блокировки/капчи Авито.

    Алгоритм — двухэтапный, намеренно СТРОГИЙ, чтобы реальная выдача
    каталога («Авито — Объявления…») никогда не определялась как бан:

    1. Проверяем заголовок страницы (page_title) — самый надёжный сигнал.
       Страницы-заглушки Авито имеют title вида «Доступ ограничен» или
       «Доступ временно ограничен: проблема с IP».
       Если page_title не передан — пробуем найти <title> в HTML.

    2. Проверяем HTML на узкие маркеры firewall-страниц: robot-checkbox,
       cf-challenge, «Подтвердите, что вы не робот».
       «captcha» и «robot» — НЕ проверяем: они встречаются в служебном JS
       реальной выдачи (Авито подключает антибот-аналитику на обычных страницах).

    Поднимает AvitoBlockedError, если найден хотя бы один маркер.
    """
    # --- Шаг 1: проверка заголовка ---
    title_to_check = page_title

    # Если title не передан явно — извлекаем из HTML через BeautifulSoup
    if not title_to_check:
        try:
            _soup = BeautifulSoup(html, "html.parser")
            _title_el = _soup.find("title")
            if _title_el:
                title_to_check = _title_el.get_text(strip=True)
        except Exception:
            pass

    if title_to_check:
        title_lower = title_to_check.lower()
        for marker in BLOCK_TITLE_MARKERS:
            if marker.lower() in title_lower:
                logger.warning(
                    "Блокировка (заголовок) на %s: title=%r, маркер=%r",
                    url, title_to_check, marker,
                )
                raise AvitoBlockedError(
                    f"Авито заблокировал (title: {title_to_check!r}) на {url}"
                )

    # --- Шаг 2: проверка HTML-маркеров firewall-страниц ---
    html_lower = html.lower()
    for marker in BLOCK_HTML_MARKERS:
        if marker.lower() in html_lower:
            logger.warning(
                "Блокировка (HTML-маркер) на %s: маркер=%r", url, marker
            )
            raise AvitoBlockedError(
                f"Авито заблокировал (HTML-маркер: {marker!r}) на {url}"
            )


def _extract_int(text: str) -> Optional[int]:
    """Извлекает первое целое число из строки. Возвращает None, если не найдено."""
    digits = re.sub(r"\s", "", text)  # убираем пробелы-разделители тысяч
    match = re.search(r"\d+", digits)
    return int(match.group()) if match else None


def _extract_metro(address: str) -> Optional[str]:
    """
    Пытается выделить станцию метро из строки адреса/локации.

    Поддерживаемые форматы:
        «м. Арбатская» / «метро Арбатская»
        «Багратионовская , до 5 мин.» / «Смоленская , 6–10 мин.»
            — формат карточки выдачи Авито: станция идёт голым именем
              перед запятой, дальше — время пешком («мин»).
              ПОДТВЕРЖДЕНО на живой странице (debug/search.html).
    """
    if not address:
        return None

    # Явный префикс «м.»/«метро»
    match = re.search(r"(?:м\.|метро)\s+([А-Яа-яЁё\w\s\-]+)", address)
    if match:
        return match.group(1).strip()

    # Формат карточки: «<Станция> , <время> мин.» — наличие «мин» означает,
    # что это привязка к метро, а станция — текст до первой запятой.
    if re.search(r"\bмин", address):
        station = address.split(",")[0].strip(" .,")
        if station:
            return station

    return None


def parse_avito_date(raw: str) -> Optional[datetime]:
    """
    Разбирает относительные и абсолютные даты в формате Авито.

    Поддерживаемые форматы (русский язык):
        «· 14 мая в 19:20»          — реальный формат с живой страницы (item-view/item-date)
        «сегодня в 14:30»
        «вчера в 09:05»
        «14 мая в 11:00»
        «14 мая 2023 в 11:00»
        «2 дня назад» / «3 часа назад» / «15 минут назад»
        ISO-дата «2024-05-14T11:00:00» (из атрибута datetime)

    ПОДТВЕРЖДЕНО на живой странице: ведущие «·», \xa0, лишние пробелы —
    очищаются перед разбором.
    """
    if not raw:
        return None

    now = datetime.now(tz=timezone.utc)

    # --- Очистка: \xa0 → пробел, убираем ведущие «·» и «.»-разделители ---
    cleaned = raw.replace("\xa0", " ")   # неразрывный пробел → обычный
    cleaned = re.sub(r"^[\s·•\-–—.]+", "", cleaned)  # ведущие разделители
    cleaned = cleaned.strip()

    text = cleaned.lower()

    # ISO-формат из атрибута datetime (самый надёжный)
    iso_match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", cleaned)
    if iso_match:
        try:
            return datetime.fromisoformat(iso_match.group(1)).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass

    # «сегодня в 14:30»
    today_match = re.match(r"сегодня\s+в\s+(\d{1,2}):(\d{2})", text)
    if today_match:
        h, m = int(today_match.group(1)), int(today_match.group(2))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    # «вчера в 09:05»
    yesterday_match = re.match(r"вчера\s+в\s+(\d{1,2}):(\d{2})", text)
    if yesterday_match:
        h, m = int(yesterday_match.group(1)), int(yesterday_match.group(2))
        base = now - timedelta(days=1)
        return base.replace(hour=h, minute=m, second=0, microsecond=0)

    # «N минут/часов/дней/недель назад»
    delta_match = re.match(
        r"(\d+)\s+(мин(?:уту?|ут[аы]?)?|час(?:а|ов)?|"
        r"день|дня|дней|д\.|недел[юи]|недель)\s+назад",
        text,
    )
    if delta_match:
        n = int(delta_match.group(1))
        unit = delta_match.group(2)
        if unit.startswith("мин"):
            return now - timedelta(minutes=n)
        elif unit.startswith("час"):
            return now - timedelta(hours=n)
        elif unit.startswith("недел"):
            return now - timedelta(weeks=n)
        else:
            return now - timedelta(days=n)

    # Резерв: «N дней/часов» без слова «назад» (старый формат)
    delta_match2 = re.match(r"(\d+)\s+(мин|час|день|дня|дней|д\.)", text)
    if delta_match2:
        n = int(delta_match2.group(1))
        unit = delta_match2.group(2)
        if unit.startswith("мин"):
            return now - timedelta(minutes=n)
        elif unit.startswith("час"):
            return now - timedelta(hours=n)
        else:
            return now - timedelta(days=n)

    # «14 мая в 11:00» или «14 мая 2023 в 11:00»
    months_ru = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    full_date_match = re.match(
        r"(\d{1,2})\s+([а-я]+)\s+(?:(\d{4})\s+)?в\s+(\d{1,2}):(\d{2})", text
    )
    if full_date_match:
        day = int(full_date_match.group(1))
        month_name = full_date_match.group(2)
        year_str = full_date_match.group(3)
        h = int(full_date_match.group(4))
        m = int(full_date_match.group(5))
        month = months_ru.get(month_name)
        if month:
            # Если год не указан — берём текущий; если дата в будущем — прошлый год
            year = int(year_str) if year_str else now.year
            try:
                result = datetime(year, month, day, h, m, tzinfo=timezone.utc)
                # Дата без явного года попала в будущее → вычитаем год
                if not year_str and result > now:
                    result = result.replace(year=year - 1)
                return result
            except ValueError:
                pass

    logger.debug("Не удалось разобрать дату: %r", raw)
    return None


# ---------------------------------------------------------------------------
# Работа с JSON-блоком страницы поиска
# ---------------------------------------------------------------------------

def _extract_items_from_json(html: str) -> list[dict[str, Any]]:
    """
    Извлекает список объявлений из встроенного JSON-блока каталога.

    Авито встраивает данные в:
        <script type="mime/invalid" data-mfe-state="true">...</script>
    Путь в JSON: state.data.catalog.items[]

    ПОДТВЕРЖДЕНО (Duff89/parser_avito): структура существует, путь к items
    может незначительно варьироваться — проверяем несколько вариантов.
    """
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.select(sel.SEARCH_JSON_SCRIPT)

    for script_tag in scripts:
        raw_json = script_tag.string or ""
        if not raw_json.strip():
            continue
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.debug("Не удалось разобрать JSON из script-тега")
            continue

        # Пробуем разные пути к массиву объявлений
        items = (
            _deep_get(data, "state", "data", "catalog", "items")
            or _deep_get(data, "data", "catalog", "items")
            or _deep_get(data, "catalog", "items")
        )
        if isinstance(items, list) and items:
            logger.debug("JSON-метод: найдено %d объявлений", len(items))
            return items

    return []


def _deep_get(obj: Any, *keys: str) -> Any:
    """Безопасно достаёт вложенное значение по цепочке ключей."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _item_from_json(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Строит частичный словарь объявления из JSON-данных каталога.

    Возвращает поля: url, title, price, address.
    Поля views_total, views_today, published_at, age_hours, metro
    будут заполнены после парсинга страницы объявления.
    """
    url_path: str = raw.get("urlPath") or raw.get("url") or ""
    url = BASE_URL + url_path if url_path.startswith("/") else url_path

    title: str = raw.get("title") or ""

    # Цена: Авито хранит в копейках (priceDetailed.value) или строке
    price: Optional[int] = None
    price_detailed = raw.get("priceDetailed") or {}
    price_val = price_detailed.get("value")
    if price_val is not None:
        try:
            price = int(price_val) // 100  # копейки → рубли
        except (ValueError, TypeError):
            pass
    if price is None:
        # Резерв: вытащить из строки «12 000 ₽»
        price_str = price_detailed.get("string") or raw.get("price") or ""
        price = _extract_int(price_str)

    # Адрес: несколько возможных полей
    address: str = (
        _deep_get(raw, "addressDetailed", "locationName")
        or _deep_get(raw, "geo", "formattedAddress")
        or raw.get("address")
        or ""
    )

    return {
        "url": url,
        "title": title,
        "price": price,
        "address": address,
        # остальные поля дополнит _parse_item_page
        "metro": None,
        "views_total": None,
        "views_today": None,
        "published_at": None,
        "age_hours": None,
    }


# ---------------------------------------------------------------------------
# Парсинг страницы поиска (сбор ссылок)
# ---------------------------------------------------------------------------

def _extract_items_from_html(html: str) -> list[dict[str, Any]]:
    """
    Резервный метод: извлекает список объявлений через CSS-селекторы
    data-marker из selectors.py.

    ПОДТВЕРЖДЕНО на живой странице:
      - data-marker="item"           — карточка объявления
      - data-marker="item-title"     — заголовок + ссылка
      - data-marker="item-price-value" — цена
      - data-marker="item-location"  — гео-блок с адресом/метро (основной)
      data-marker="item-address" оставлен как резерв.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(sel.SEARCH_CARD)
    items: list[dict[str, Any]] = []

    # Счётчик для логирования сырого текста item-location первых карточек
    _location_log_count = 0

    for card in cards:
        try:
            # URL и заголовок
            title_el = card.select_one(sel.SEARCH_CARD_TITLE)
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href") or ""
            url = BASE_URL + href if href.startswith("/") else href

            # Цена
            price_el = card.select_one(sel.SEARCH_CARD_PRICE)
            price = _extract_int(price_el.get_text()) if price_el else None

            # --- Адрес/метро: берём из data-marker="item-location" (ПОДТВЕРЖДЕНО) ---
            address: str = ""
            metro: Optional[str] = None

            location_el = card.select_one(sel.SEARCH_CARD_LOCATION)
            if location_el:
                # Нормализуем: \xa0 → пробел, collapse whitespace
                raw_location = location_el.get_text(separator=" ")
                raw_location = raw_location.replace("\xa0", " ")
                address = " ".join(raw_location.split())

                # Логируем сырой текст первых 3 карточек — чтобы увидеть
                # реальный формат метро на следующем прогоне diag.py
                if _location_log_count < 3:
                    logger.info(
                        "item-location сырой текст (карточка %d): %r",
                        _location_log_count + 1,
                        address,
                    )
                    _location_log_count += 1

                # Метро: пробуем найти вложенный элемент с отдельным гео-значением.
                # Авито часто делит на несколько span: [район/улица] [станция метро].
                # Собираем все дочерние текстовые блоки — станция обычно последний.
                sub_texts = [
                    " ".join(child.get_text(separator=" ").replace("\xa0", " ").split())
                    for child in location_el.children
                    if hasattr(child, "get_text")
                ]
                # Ищем метро в каждом сегменте (паттерн «м. Арбатская»)
                for seg in sub_texts:
                    m = _extract_metro(seg)
                    if m:
                        metro = m
                        break
                # Если в под-элементах не нашли — ищем в полном тексте
                if metro is None:
                    metro = _extract_metro(address)

            else:
                # Резерв: старый data-marker="item-address"
                addr_el = card.select_one(sel.SEARCH_CARD_ADDRESS)
                if addr_el:
                    raw_addr = addr_el.get_text(separator=" ").replace("\xa0", " ")
                    address = " ".join(raw_addr.split())
                    metro = _extract_metro(address)

            items.append({
                "url": url,
                "title": title,
                "price": price,
                "address": address,
                "metro": metro,
                "views_total": None,
                "views_today": None,
                "published_at": None,
                "age_hours": None,
            })
        except Exception as exc:
            logger.debug("Ошибка парсинга карточки: %s", exc)
            continue

    logger.debug("CSS-резерв: найдено %d карточек", len(items))
    return items


async def _wait_for_cards_and_scroll(page: Page, url: str) -> None:
    """
    Ждёт появления карточек [data-marker='item'] в отрисованном DOM,
    затем несколько раз прокручивает страницу вниз, чтобы вызвать
    ленивый рендер оставшихся карточек.

    Авито — React-SPA: карточки отрисовываются клиентским JS ПОСЛЕ
    выполнения скриптов. Снимать page.content() нужно только после
    появления хотя бы одной карточки.

    Если карточки не появились за 20 сек — логируем и продолжаем
    (не падаем), потому что страница может содержать «нет результатов».
    """
    # Ждём первую карточку (основной рендер)
    try:
        await page.wait_for_selector(
            "[data-marker='item']", timeout=20_000, state="attached"
        )
        logger.debug("Карточки обнаружены в DOM: %s", url)
    except Exception as exc:
        logger.warning(
            "Карточки [data-marker='item'] не появились за 20 сек на %s: %s",
            url, exc,
        )
        return  # Продолжаем — читаем что есть (может быть «нет результатов»)

    # Прокрутка вниз для ленивого рендера оставшихся карточек.
    # 5 прокруток с паузой 0.8 сек обычно достаточно для подгрузки страницы.
    for scroll_step in range(5):
        try:
            prev_count = await page.locator("[data-marker='item']").count()
            await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            await asyncio.sleep(0.8)
            new_count = await page.locator("[data-marker='item']").count()
            logger.debug(
                "Прокрутка %d: карточек %d → %d", scroll_step + 1, prev_count, new_count
            )
            # Если карточек больше не прибавляется — ленивый рендер завершён
            if new_count == prev_count:
                break
        except Exception as scroll_exc:
            logger.debug("Ошибка при прокрутке шаг %d: %s", scroll_step + 1, scroll_exc)
            break

    # Прокручиваем обратно вверх — на всякий случай перед снятием контента
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


async def _collect_listing_items(
    page: Page,
    city: City,
    query: str,
    max_items: int,
    filters: "SearchFilters | None" = None,
) -> list[dict[str, Any]]:
    """
    Открывает страницы выдачи и собирает сырые данные МЕСТНЫХ объявлений
    (url, title, price, address) до достижения max_items МЕСТНЫХ или срабатывания
    предохранителя.

    Местное объявление — первый сегмент пути URL совпадает с city.slug.
    Чужие (из другого города/региона) пропускаются без захода на их страницы.

    Пагинация через параметр URL &p=N.

    Предохранители остановки (break из цикла по страницам):
      * нет карточек на странице (страница пустая или конец выдачи);
      * local_on_page == 0 при непустой странице — местные, видимо, закончились;
      * page_num > MAX_SEARCH_PAGES — жёсткий лимит страниц для узких запросов.

    Аргументы:
        filters: необязательные фильтры поиска (SearchFilters). Передаются в
                 build_search_url и попадают в параметры URL Авито.

    Порядок извлечения:
      1. Ждём рендера карточек в DOM (wait_for_selector + прокрутка).
      2. Основной метод — CSS-селекторы из отрисованного DOM.
         JSON-блок (mime/invalid) на каталоге НЕ появляется в раннем HTML
         (вставляется тем же React-рендером), поэтому пробуем его как доп.
         резерв ПО РЕЗУЛЬТАТУ page.content() после ожидания.
    """
    # collected содержит ТОЛЬКО местные объявления
    collected: list[dict[str, Any]] = []
    page_num: int = 1

    while len(collected) < max_items and page_num <= MAX_SEARCH_PAGES:
        # Передаём filters в build_search_url для добавления pmin/pmax в URL
        url = build_search_url(city.slug, query, filters)
        if page_num > 1:
            url += f"&p={page_num}"

        logger.info("Страница поиска %s, стр. %d: %s", city.name, page_num, url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            logger.warning(
                "Ошибка загрузки страницы %s стр. %d: %s", city.name, page_num, exc
            )
            break

        # Ждём рендера карточек + прокрутка для ленивой загрузки
        await _wait_for_cards_and_scroll(page, url)

        # Снимаем контент ПОСЛЕ рендера
        html = await page.content()

        # Проверяем блокировку — передаём актуальный заголовок страницы
        try:
            page_title = await page.title()
        except Exception:
            page_title = ""
        _check_block(html, url, page_title=page_title)  # поднимет AvitoBlockedError при бане

        # Основной метод — CSS из отрисованного DOM (карточки уже в HTML)
        raw_items = _extract_items_from_html(html)

        # Резерв — JSON-блок (может присутствовать в отрисованном HTML)
        if not raw_items:
            logger.debug(
                "CSS-метод не дал результатов для %s стр. %d, пробую JSON",
                city.name, page_num,
            )
            json_raw = _extract_items_from_json(html)
            if json_raw:
                raw_items = [_item_from_json(r) for r in json_raw]

        # Предохранитель 1: страница без карточек — конец выдачи
        if not raw_items:
            logger.info(
                "Объявления не найдены на стр. %d для %s, пагинация остановлена",
                page_num, city.name,
            )
            break

        # Отбираем только местные объявления
        local_on_page: int = 0
        for item in raw_items:
            if not item.get("url"):
                continue
            # Пропускаем объявления из чужих регионов
            if not is_local_listing(item["url"], city.slug):
                continue
            local_on_page += 1
            collected.append(item)
            if len(collected) >= max_items:
                break

        logger.info(
            "%s: стр. %d — местных на странице %d, всего собрано %d/%d",
            city.name, page_num, local_on_page, len(collected), max_items,
        )

        # Предохранитель 2: были карточки, но ни одной местной — местные закончились
        if local_on_page == 0:
            logger.info(
                "%s: местных на стр. %d нет — вероятно местные закончились, "
                "пагинация остановлена",
                city.name, page_num,
            )
            break

        page_num += 1
        await _random_delay()

    return collected[:max_items]


# ---------------------------------------------------------------------------
# Парсинг страницы отдельного объявления
# ---------------------------------------------------------------------------

async def _parse_item_page(
    page: Page,
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    Открывает страницу объявления и дополняет словарь полями:
    views_total, views_today, address (если не заполнен), metro, published_at, age_hours.

    ВАЖНО: Селекторы title, price, address, date на странице объявления
    помечены как НЕ ПОДТВЕРЖДЕНО в selectors.py. Прогон на живой странице
    до первого реального теста невозможен (IP заблокирован).
    """
    url: str = item["url"]

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:
        logger.warning("Ошибка загрузки объявления %s: %s", url, exc)
        return item

    # Ждём ключевой элемент страницы объявления — заголовок или блок просмотров.
    # Страница объявления тоже React-SPA: контент рисуется клиентским JS.
    # Пробуем несколько надёжных маркеров по очереди.
    _item_ready_selectors = [
        "[data-marker='item-view/total-views']",
        "[data-marker='item-view/today-views']",
        "h1[class*='title']",
        "h1",
    ]
    _rendered = False
    for _sel in _item_ready_selectors:
        try:
            await page.wait_for_selector(_sel, timeout=10_000, state="attached")
            logger.debug("Страница объявления отрисована (%s): %s", _sel, url)
            _rendered = True
            break
        except Exception:
            pass
    if not _rendered:
        logger.warning(
            "Ключевые элементы объявления не появились за 10 сек: %s — читаем как есть",
            url,
        )

    # Дополнительная пауза для завершения подзагрузок (счётчики просмотров)
    await asyncio.sleep(1.0)

    html = await page.content()

    # Проверяем бан — передаём актуальный заголовок страницы
    try:
        _page_title = await page.title()
    except Exception:
        _page_title = ""
    _check_block(html, url, page_title=_page_title)

    soup = BeautifulSoup(html, "html.parser")

    # --- Просмотры (ПОДТВЕРЖДЕНО) ---
    views_total_el = soup.select_one(sel.ITEM_VIEWS_TOTAL)
    if views_total_el:
        item["views_total"] = _extract_int(views_total_el.get_text())

    views_today_el = soup.select_one(sel.ITEM_VIEWS_TODAY)
    if views_today_el:
        item["views_today"] = _extract_int(views_today_el.get_text())

    # Резерв: попробовать достать просмотры из JSON-состояния объявления
    if item["views_today"] is None:
        item["views_today"] = _extract_views_today_from_json(html)

    # --- Адрес (НЕ ПОДТВЕРЖДЕНО — общий вариант) ---
    if not item.get("address"):
        addr_el = soup.select_one(sel.ITEM_ADDRESS)
        if addr_el:
            item["address"] = addr_el.get_text(strip=True)

    # Метро — извлекаем из адреса
    if item.get("address"):
        item["metro"] = _extract_metro(item["address"])

    # --- Дата публикации (НЕ ПОДТВЕРЖДЕНО) ---
    published_at: Optional[datetime] = None

    # Вариант 1: data-marker="item-view/item-date"
    date_el = soup.select_one(sel.ITEM_DATE)
    if date_el:
        raw_date = date_el.get("datetime") or date_el.get_text(strip=True)
        published_at = parse_avito_date(raw_date)

    # Вариант 2: тег <time datetime="...">
    if published_at is None:
        time_el = soup.select_one(sel.ITEM_DATE_TIME_TAG)
        if time_el:
            raw_date = time_el.get("datetime") or time_el.get_text(strip=True)
            published_at = parse_avito_date(raw_date)

    item["published_at"] = published_at

    # Возраст в часах
    if published_at is not None:
        now = datetime.now(tz=timezone.utc)
        # published_at может быть без tzinfo — нормализуем
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        delta = now - published_at
        item["age_hours"] = max(delta.total_seconds() / 3600, 0.0)

    return item


def _extract_views_today_from_json(html: str) -> Optional[int]:
    """
    Резервная попытка вытащить «просмотров сегодня» из JSON-состояния
    страницы объявления (если CSS-селектор не сработал).

    Путь в JSON предположительный: ищем ключи 'todayViews', 'viewsToday',
    'today_views'. НЕ ПОДТВЕРЖДЕНО — паттерн поиска по ключевым словам.
    """
    # Ищем JSON-блок объявления
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.select(sel.SEARCH_JSON_SCRIPT)  # тот же тип тега
    for script_tag in scripts:
        raw_json = script_tag.string or ""
        if not raw_json.strip():
            continue
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        # Рекурсивный поиск по ключам
        result = _find_key_in_json(data, ("todayViews", "viewsToday", "today_views"))
        if result is not None:
            try:
                return int(result)
            except (ValueError, TypeError):
                pass
    return None


def _find_key_in_json(obj: Any, keys: tuple[str, ...]) -> Any:
    """Рекурсивно ищет первый из указанных ключей в JSON-структуре."""
    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                return obj[key]
        for val in obj.values():
            result = _find_key_in_json(val, keys)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for element in obj:
            result = _find_key_in_json(element, keys)
            if result is not None:
                return result
    return None


# ---------------------------------------------------------------------------
# Создание браузера (persistent context с антидетект-мерами)
# ---------------------------------------------------------------------------

async def _launch_persistent_context(
    pw: Any,
    headless: bool,
) -> BrowserContext:
    """
    Запускает браузер как persistent context с антидетект-настройками.

    Сначала пытается использовать реальный установленный Chrome
    (channel="chrome"). Если Chrome не найден — fallback на встроенный
    Chromium с логом WARNING.

    Persistent context (.pw-profile/) сохраняет куки и сессию между
    запусками — Авито реже гоняет проверки у «знакомого» браузера.
    """
    # Убеждаемся, что папка профиля существует
    _PROFILE_DIR.mkdir(exist_ok=True)
    profile_path = str(_PROFILE_DIR.resolve())

    # Общие kwargs для launch_persistent_context
    ctx_kwargs: dict[str, Any] = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        # Реалистичный viewport — самый популярный у десктоп-пользователей
        viewport={"width": 1366, "height": 768},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        extra_http_headers={
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
        },
    )

    # Попытка 1: реальный Chrome — он сам даёт корректный UA, не переопределяем
    try:
        context = await pw.chromium.launch_persistent_context(
            profile_path,
            channel="chrome",
            **ctx_kwargs,
        )
        logger.info("Браузер запущен: реальный Chrome (channel='chrome')")
    except Exception as chrome_err:
        # Chrome не установлен или не найден Playwright — используем Chromium
        logger.warning(
            "Реальный Chrome не найден (%s) — fallback на встроенный Chromium",
            chrome_err,
        )
        # При Chromium устанавливаем UA явно, чтобы не светить headless-строкой
        ctx_kwargs["user_agent"] = USER_AGENT
        context = await pw.chromium.launch_persistent_context(
            profile_path,
            **ctx_kwargs,
        )
        logger.info("Браузер запущен: встроенный Chromium (fallback)")

    # Регистрируем stealth-скрипт — выполнится в каждой новой странице
    await context.add_init_script(_STEALTH_SCRIPT)
    logger.debug("Stealth init-скрипт зарегистрирован на контексте")

    return context


async def _connect_over_cdp(
    pw: Any,
    cdp_url: str,
) -> tuple[Browser, BrowserContext]:
    """
    Подключается к уже запущенному пользовательскому Chrome через CDP.

    Пользователь должен запустить Chrome ВРУЧНУЮ командой:
        chrome.exe --remote-debugging-port=9222 --user-data-dir=<путь>

    Затем зайти на avito.ru вручную (прогреть сессию) и только потом
    вызывать этот метод.

    Возвращает (browser, context):
        browser  — объект подключения (НЕ закрывать! это чужой Chrome)
        context  — существующий контекст браузера (contexts[0]) или новый.

    ВАЖНО: не закрывай browser/context — это Chrome пользователя.
    Закрывай только страницы (page), которые сам открыл.

    При неудаче подключения поднимает RuntimeError с инструкцией.
    """
    logger.info("CDP: подключаемся к браузеру по адресу %s", cdp_url)
    try:
        browser: Browser = await pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        # Понятное сообщение, если Chrome не запущен с нужным флагом
        raise RuntimeError(
            f"Не удалось подключиться к Chrome по CDP ({cdp_url}).\n"
            "Убедитесь, что Chrome запущен с флагом --remote-debugging-port.\n"
            "Пример команды:\n"
            '  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            "--remote-debugging-port=9222 "
            '--user-data-dir="C:\\Users\\TBG\\avito-chrome-profile"\n'
            f"Исходная ошибка: {exc}"
        ) from exc

    # Берём существующий контекст (вкладки пользователя) или создаём новый
    if browser.contexts:
        context: BrowserContext = browser.contexts[0]
        logger.info("CDP: используем существующий контекст (contexts[0])")
    else:
        context = await browser.new_context()
        logger.info("CDP: создан новый контекст (contexts[0] не было)")

    # Stealth-скрипт — дополнительная мера, хотя Chrome пользователя уже «чистый»
    try:
        await context.add_init_script(_STEALTH_SCRIPT)
        logger.debug("CDP: stealth init-скрипт добавлен в контекст")
    except Exception as exc:
        # В режиме CDP add_init_script может не поддерживаться — не критично
        logger.debug("CDP: add_init_script не удалось применить: %s", exc)

    return browser, context


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

async def parse_city(
    city: City,
    query: str,
    *,
    max_items: int = MAX_ITEMS,
    headless: bool = False,
    cdp_url: Optional[str] = None,
    filters: "SearchFilters | None" = None,
) -> list[dict]:
    """
    Парсит объявления Авито по одному городу.

    Аргументы:
        city      : объект City из cities.py
        query     : поисковый запрос (например «диван угловой»)
        max_items : максимальное количество объявлений (по умолчанию 150)
        headless  : запускать браузер без GUI (по умолчанию False).
                    Видимое окно позволяет решить капчу вручную.
                    Игнорируется при cdp_url — браузер уже запущен пользователем.
        cdp_url   : если задан (например «http://localhost:9222»), подключаемся
                    к уже запущенному Chrome пользователя через CDP вместо
                    запуска нового браузера. Chrome пользователя НЕ закрывается.
        filters   : необязательные фильтры поиска (SearchFilters). Передаются
                    в URL Авито через build_search_url.

    Возвращает список словарей согласно контракту данных.
    Фильтрация и расчёт метрик — на стороне analytics.py.

    При обнаружении блокировки Авито поднимает AvitoBlockedError.
    Ошибка отдельного объявления — логируется и пропускается.
    """
    results: list[dict] = []

    async with async_playwright() as pw:
        # Определяем режим работы: CDP или собственный браузер
        use_cdp = cdp_url is not None

        if use_cdp:
            # CDP-режим: подключаемся к Chrome пользователя
            browser, context = await _connect_over_cdp(pw, cdp_url)  # type: ignore[arg-type]
        else:
            # Обычный режим: запускаем persistent context с антидетект-мерами
            context = await _launch_persistent_context(pw, headless=headless)

        # Открываем свою страницу для работы
        page = await context.new_page()

        try:
            # Шаг 1: собираем базовые данные со страниц поиска (с учётом filters)
            raw_items = await _collect_listing_items(page, city, query, max_items, filters)
            logger.info(
                "%s: собрано %d объявлений со страниц поиска", city.name, len(raw_items)
            )

            # Шаг 2: заходим на каждое объявление за просмотрами/датой
            for idx, item in enumerate(raw_items, start=1):
                try:
                    enriched = await _parse_item_page(page, item)
                    results.append(enriched)
                    logger.debug(
                        "%s: обработано объявление %d/%d — %s",
                        city.name, idx, len(raw_items), item.get("url", "?"),
                    )
                except AvitoBlockedError:
                    # Блокировка — прерываем весь парсинг города
                    raise
                except Exception as exc:
                    logger.warning(
                        "%s: ошибка обработки объявления %s: %s",
                        city.name, item.get("url", "?"), exc,
                    )
                    # Добавляем то, что уже есть (без просмотров/даты)
                    results.append(item)

        finally:
            # Закрываем только свою страницу
            try:
                await page.close()
            except Exception:
                pass

            if use_cdp:
                # CDP-режим: НЕ закрываем context/browser — это Chrome пользователя.
                # Закрыли только page выше.
                logger.debug("CDP: страница закрыта, браузер пользователя сохранён")
            else:
                # Обычный режим: persistent context закрываем полностью
                await context.close()

    logger.info(
        "%s: парсинг завершён, итого объявлений: %d", city.name, len(results)
    )
    return results


async def parse_all(
    query: str,
    *,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    headless: bool = False,
    cdp_url: Optional[str] = None,
    max_items: int = MAX_ITEMS,
    filters: "SearchFilters | None" = None,
    cities: "list[City] | None" = None,
) -> dict[str, list[dict]]:
    """
    Парсит объявления Авито по выбранным городам.

    Аргументы:
        query       : поисковый запрос
        progress_cb : необязательный callback(done, total, city_name).
                      Вызывается ДО начала парсинга каждого города.
                      Пример: lambda d, t, c: print(f"Парсим {c}… {d}/{t}")
        headless    : запускать браузер без GUI (по умолчанию False).
                      Видимое окно позволяет решить капчу вручную.
                      Игнорируется при cdp_url — браузер уже запущен пользователем.
        cdp_url     : если задан (например «http://localhost:9222»), все города
                      парсятся через CDP-подключение к Chrome пользователя.
                      Chrome пользователя НЕ закрывается после парсинга.
        filters     : необязательные фильтры поиска (SearchFilters). Передаются
                      во все вызовы parse_city и далее в URL Авито.
        cities      : список городов для парсинга (объекты City из cities.py).
                      Если None — парсятся все города из cities.CITIES.
                      Если передан список — парсятся только указанные города.

    Возвращает словарь {city.slug: list[dict]}.
    При блокировке — логирует ERROR и прекращает обход городов.
    Ошибка одного города — логируется, парсинг остальных продолжается.
    """
    # Определяем список городов: переданные или все по умолчанию
    target_cities = cities if cities is not None else CITIES
    total = len(target_cities)
    all_results: dict[str, list[dict]] = {}

    for done, city in enumerate(target_cities, start=1):
        if progress_cb is not None:
            try:
                progress_cb(done - 1, total, city.name)
            except Exception as cb_exc:
                logger.debug("progress_cb вызвал исключение: %s", cb_exc)

        logger.info("=== Начинаем парсинг: %s (%d/%d) ===", city.name, done, total)

        try:
            results = await parse_city(
                city, query,
                max_items=max_items, headless=headless, cdp_url=cdp_url,
                filters=filters,
            )
            all_results[city.slug] = results
        except AvitoBlockedError as exc:
            logger.error(
                "Авито заблокировал на городе %s — останавливаем парсинг. %s",
                city.name, exc,
            )
            # Возвращаем то, что успели собрать
            break
        except Exception as exc:
            logger.error(
                "Непредвиденная ошибка при парсинге %s: %s", city.name, exc
            )
            all_results[city.slug] = []

        if progress_cb is not None:
            try:
                progress_cb(done, total, city.name)
            except Exception as cb_exc:
                logger.debug("progress_cb вызвал исключение: %s", cb_exc)

    logger.info(
        "parse_all завершён. Городов обработано: %d/%d", len(all_results), total
    )
    return all_results


# ---------------------------------------------------------------------------
# Быстрая проверка parse_avito_date (запуск: python parser.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
    now = datetime.now(tz=timezone.utc)
    errors: list[str] = []

    def _check(label: str, raw: str, *, expect_none: bool = False) -> Optional[datetime]:
        """Вызывает parse_avito_date, печатает результат, собирает ошибки."""
        result = parse_avito_date(raw)
        status = "OK" if (result is None) == expect_none else "FAIL"
        print(f"  [{status}] {label!r:35s} => {result}")
        if status == "FAIL":
            errors.append(f"{label!r} → ожидали {'None' if expect_none else 'datetime'}, получили {result!r}")
        return result

    print("\n=== Тест parse_avito_date ===\n")

    # Реальный формат с живой страницы (data-marker="item-view/item-date")
    r1 = _check("· 14 мая в 19:20", "· 14 мая в 19:20")
    if r1 is not None:
        assert r1.month == 5, f"месяц должен быть 5, получили {r1.month}"
        assert r1.day == 14, f"день должен быть 14, получили {r1.day}"
        assert r1.hour == 19 and r1.minute == 20, f"время должно быть 19:20, получили {r1.hour}:{r1.minute}"

    # Неразрывные пробелы
    r2 = _check("· 14\xa0мая в 19:20 (\\xa0)", "·\xa014\xa0мая\xa0в\xa019:20")
    if r2 is not None:
        assert r2.month == 5 and r2.day == 14

    # Сегодня
    r3 = _check("сегодня в 09:05", "сегодня в 09:05")
    if r3 is not None:
        assert r3.date() == now.date(), f"сегодня: дата должна совпадать с now.date()"
        assert r3.hour == 9 and r3.minute == 5

    # Вчера
    r4 = _check("вчера в 23:10", "вчера в 23:10")
    if r4 is not None:
        yesterday = (now - timedelta(days=1)).date()
        assert r4.date() == yesterday, f"вчера: ожидали {yesterday}, получили {r4.date()}"
        assert r4.hour == 23 and r4.minute == 10

    # Дата с явным годом
    r5 = _check("14 мая 2023 в 11:00", "14 мая 2023 в 11:00")
    if r5 is not None:
        assert r5.year == 2023 and r5.month == 5 and r5.day == 14

    # Относительные форматы
    _check("3 дня назад", "3 дня назад")
    _check("2 часа назад", "2 часа назад")
    _check("15 минут назад", "15 минут назад")
    _check("1 неделю назад", "1 неделю назад")

    # ISO из datetime-атрибута
    r6 = _check("ISO 2024-05-14T11:00", "2024-05-14T11:00:00")
    if r6 is not None:
        assert r6.year == 2024 and r6.month == 5 and r6.day == 14

    # Мусор → None
    _check("мусор → None", "непонятная строка", expect_none=True)
    _check("пустая строка → None", "", expect_none=True)

    print()
    if errors:
        print(f"ПРОВАЛЕНО {len(errors)} тест(ов):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("Все тесты прошли успешно.")
        sys.exit(0)
