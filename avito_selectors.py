"""
CSS-селекторы и атрибуты для парсинга Авито.

Сгруппированы по двум контекстам:
  - SEARCH: страница поиска (список карточек объявлений)
  - ITEM:   страница отдельного объявления

ВАЖНО: Авито регулярно меняет вёрстку. Все атрибуты вида data-marker
стабильнее CSS-классов (классы меняются чаще). Именно data-marker
предпочтительны для выборки.

СТАТУС ПОДТВЕРЖДЕНИЯ:
  Селекторы помечены как «ПОДТВЕРЖДЕНО» или «НЕ ПОДТВЕРЖДЕНО».
  «ПОДТВЕРЖДЕНО на живой странице» — проверено лично по сохранённым
  debug/search.html и debug/item.html с реального сеанса CDP.
  «ПОДТВЕРЖДЕНО» (без уточнения) — из исходного кода работающих парсеров
  (xailiry/avito-parser-sdk, Duff89/parser_avito).

АЛЬТЕРНАТИВНЫЙ МЕТОД (рекомендованный Duff89/parser_avito):
  Авито встраивает весь каталог объявлений в тег:
      <script type="mime/invalid" data-mfe-state="true">
  в виде JSON-объекта. Поле catalog.items содержит массив объявлений
  с полями id, title, urlPath, priceDetailed, addressDetailed, geo,
  sortTimeStamp. Этот метод надёжнее HTML-селекторов, но требует
  разбора JSON (реализован в parser.py).
"""

# ---------------------------------------------------------------------------
# Страница поиска — список карточек объявлений
# ---------------------------------------------------------------------------

# Контейнер одной карточки объявления.
# Содержит все данные по конкретному лоту.
# ПОДТВЕРЖДЕНО на живой странице: debug/search.html — 50 карточек
SEARCH_CARD: str = "[data-marker='item']"

# Идентификатор объявления (числовой ID).
# Получается как атрибут: card.get("data-item-id")
# ПОДТВЕРЖДЕНО: xailiry/avito-parser-sdk, Duff89/parser_avito
SEARCH_CARD_ID_ATTR: str = "data-item-id"

# Заголовок карточки — одновременно является ссылкой <a> на объявление.
# Атрибут href содержит относительный путь вида /moskva/...
# ПОДТВЕРЖДЕНО: data-marker="item-title" используется в xailiry/avito-parser-sdk
SEARCH_CARD_TITLE: str = "[data-marker='item-title']"

# Цена в карточке поиска.
# ПОДТВЕРЖДЕНО: data-marker="item-price-value" — xailiry/avito-parser-sdk
SEARCH_CARD_PRICE: str = "[data-marker='item-price-value']"

# Гео-блок карточки поиска: адрес/метро (район, улица, станция метро).
# ПОДТВЕРЖДЕНО на живой странице: debug/search.html — атрибут data-marker="item-location".
# Классы вида geo-root-XXXX меняются с каждой сборкой — НЕ использовать.
# Текст содержит район/улицу и, возможно, станцию метро (формат уточняется
# в diag.py при следующем прогоне — см. логирование первых 3 карточек).
SEARCH_CARD_LOCATION: str = "[data-marker='item-location']"

# Адрес/метро в карточке поиска (старый селектор — оставлен как резерв).
# ПОДТВЕРЖДЕНО: data-marker="item-address" — xailiry/avito-parser-sdk.
# На живой странице (debug/search.html) адрес/метро лежит в item-location,
# а не в item-address — используй SEARCH_CARD_LOCATION как основной.
SEARCH_CARD_ADDRESS: str = "[data-marker='item-address']"

# ---------------------------------------------------------------------------
# Страница объявления — детальная карточка
# ---------------------------------------------------------------------------

# Заголовок (h1) объявления.
# НЕ ПОДТВЕРЖДЕНО на живой странице — возможный вариант по общей практике.
# В новой вёрстке Авито заголовок может быть внутри блока title-info.
ITEM_TITLE: str = "h1[class*='title']"

# Цена объявления.
# НЕ ПОДТВЕРЖДЕНО на живой странице — общий вариант.
ITEM_PRICE: str = "[class*='price-value']"

# Адрес/метро на странице объявления.
# НЕ ПОДТВЕРЖДЕНО на живой странице — общий вариант.
ITEM_ADDRESS: str = "[class*='item-address']"

# Описание объявления (длинный текст).
# ПОДТВЕРЖДЕНО: data-marker="item-view/item-description" — xailiry/avito-parser-sdk
ITEM_DESCRIPTION: str = "[data-marker='item-view/item-description']"

# Просмотры ВСЕГО (суммарные за всё время).
# ПОДТВЕРЖДЕНО на живой странице: debug/item.html — views_total=841
ITEM_VIEWS_TOTAL: str = "[data-marker='item-view/total-views']"

# Просмотры СЕГОДНЯ / за последние сутки.
# ПОДТВЕРЖДЕНО на живой странице: debug/item.html — views_today=42
# ВАЖНО: Авито показывает «просмотров сегодня», а не «за день».
# В нашей логике это и есть «просмотров за день» (нужный показатель).
ITEM_VIEWS_TODAY: str = "[data-marker='item-view/today-views']"

# Дата публикации объявления.
# ПОДТВЕРЖДЕНО на живой странице: debug/item.html — текст вида "· 14 мая в 19:20"
# (ведущий «·» и неразрывные пробелы \xa0 — очищаются в parse_avito_date).
ITEM_DATE: str = "[data-marker='item-view/item-date']"

# Дата публикации — запасной вариант через тег <time>.
# НЕ ПОДТВЕРЖДЕНО на живой странице — общий HTML-стандарт.
ITEM_DATE_TIME_TAG: str = "time[datetime]"

# Имя продавца.
# ПОДТВЕРЖДЕНО: data-marker="seller-info/name" — xailiry/avito-parser-sdk
ITEM_SELLER_NAME: str = "[data-marker='seller-info/name']"

# ---------------------------------------------------------------------------
# JSON-блок на странице поиска (альтернативный способ получения данных)
# ---------------------------------------------------------------------------

# Тег скрипта, содержащего встроенный JSON с данными каталога.
# ПОДТВЕРЖДЕНО: Duff89/parser_avito (метод find_json_on_page):
#   soup.select('script[type="mime/invalid"][data-mfe-state="true"]')
# JSON содержит поля:
#   state.data.catalog.items[] — массив объявлений
#   state.data.catalog.items[].id
#   state.data.catalog.items[].title
#   state.data.catalog.items[].urlPath  (относительная ссылка)
#   state.data.catalog.items[].priceDetailed.value (цена в копейках)
#   state.data.catalog.items[].priceDetailed.string (строка «12 000 ₽»)
#   state.data.catalog.items[].addressDetailed.locationName
#   state.data.catalog.items[].geo.formattedAddress
#   state.data.catalog.items[].sortTimeStamp  (UNIX-время в миллисекундах)
#   state.data.searchCore — параметры поиска для пагинации
#   state.data.context    — контекст для API-запросов следующих страниц
SEARCH_JSON_SCRIPT: str = "script[type='mime/invalid'][data-mfe-state='true']"

# ---------------------------------------------------------------------------
# Пагинация (страница поиска)
# ---------------------------------------------------------------------------

# Кнопка «Следующая страница».
# НЕ ПОДТВЕРЖДЕНО на живой странице — наиболее вероятный вариант.
SEARCH_NEXT_PAGE: str = "[data-marker='pagination-button/nextPage']"

# ---------------------------------------------------------------------------
# Удобные агрегаты для использования в parser.py
# ---------------------------------------------------------------------------

# Словарь всех селекторов страницы поиска
SEARCH: dict[str, str] = {
    "card":         SEARCH_CARD,
    "card_id_attr": SEARCH_CARD_ID_ATTR,
    "title":        SEARCH_CARD_TITLE,
    "price":        SEARCH_CARD_PRICE,
    "location":     SEARCH_CARD_LOCATION,   # основной (ПОДТВЕРЖДЕНО на живой странице)
    "address":      SEARCH_CARD_ADDRESS,    # резерв
    "json_script":  SEARCH_JSON_SCRIPT,
    "next_page":    SEARCH_NEXT_PAGE,
}

# Словарь всех селекторов страницы объявления
ITEM: dict[str, str] = {
    "title":        ITEM_TITLE,
    "price":        ITEM_PRICE,
    "address":      ITEM_ADDRESS,
    "description":  ITEM_DESCRIPTION,
    "views_total":  ITEM_VIEWS_TOTAL,
    "views_today":  ITEM_VIEWS_TODAY,
    "date":         ITEM_DATE,
    "date_time":    ITEM_DATE_TIME_TAG,
    "seller_name":  ITEM_SELLER_NAME,
}
