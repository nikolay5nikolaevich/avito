"""
Справочник городов для парсинга Авито.

Содержит список из 50 городов с их slug'ами для URL, флагом наличия метро
и численностью населения (в тысячах человек).
Для Москвы и Санкт-Петербурга предусмотрена дополнительная разбивка по станциям метро.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from filters import SearchFilters


@dataclass(frozen=True)
class City:
    """Описание города для парсинга.

    Поля:
        name:       Человекочитаемое название
        slug:       Slug для URL Авито: avito.ru/{slug}
        has_metro:  Нужна ли разбивка по станциям метро
        population: Численность населения в тысячах человек
    """

    name: str        # Человекочитаемое название
    slug: str        # Slug для URL Авито: avito.ru/{slug}
    has_metro: bool  # Нужна ли разбивка по станциям метро
    population: int  # Население в тысячах человек


# Список городов в порядке убывания населения
CITIES: list[City] = [
    City("Москва",            "moskva",             True,  13010),
    City("Санкт-Петербург",   "sankt-peterburg",    True,  5602),
    City("Новосибирск",       "novosibirsk",        False, 1634),
    City("Екатеринбург",      "ekaterinburg",       False, 1544),
    City("Казань",            "kazan",              False, 1309),
    City("Нижний Новгород",   "nizhniy_novgorod",   False, 1226),
    City("Челябинск",         "chelyabinsk",        False, 1190),
    City("Красноярск",        "krasnoyarsk",        False, 1188),
    City("Самара",            "samara",             False, 1173),
    City("Уфа",               "ufa",                False, 1145),
    City("Ростов-на-Дону",    "rostov-na-donu",     False, 1142),
    City("Омск",              "omsk",               False, 1126),
    City("Краснодар",         "krasnodar",          False, 1099),
    City("Воронеж",           "voronezh",           False, 1058),
    City("Пермь",             "perm",               False, 1034),
    City("Волгоград",         "volgograd",          False, 1028),
    City("Саратов",           "saratov",            False, 901),
    City("Тюмень",            "tyumen",             False, 847),
    City("Тольятти",          "tolyatti",           False, 685),
    City("Барнаул",           "barnaul",            False, 631),
    City("Махачкала",         "mahachkala",         False, 623),
    City("Ижевск",            "izhevsk",            False, 623),
    City("Хабаровск",         "habarovsk",          False, 617),
    City("Ульяновск",         "ulyanovsk",          False, 617),
    City("Иркутск",           "irkutsk",            False, 617),
    City("Владивосток",       "vladivostok",        False, 604),
    City("Ярославль",         "yaroslavl",          False, 577),
    City("Кемерово",          "kemerovo",           False, 557),
    City("Томск",             "tomsk",              False, 556),
    City("Севастополь",       "sevastopol",         False, 548),
    City("Набережные Челны",  "naberezhnye_chelny", False, 548),
    City("Ставрополь",        "stavropol",          False, 547),
    City("Оренбург",          "orenburg",           False, 544),
    City("Новокузнецк",       "novokuznetsk",       False, 537),
    City("Рязань",            "ryazan",             False, 529),
    City("Балашиха",          "balashiha",          False, 521),
    City("Пенза",             "penza",              False, 501),
    City("Чебоксары",         "cheboksary",         False, 498),
    City("Липецк",            "lipetsk",            False, 496),
    City("Калининград",       "kaliningrad",        False, 490),
    City("Астрахань",         "astrahan",           False, 476),
    City("Тула",              "tula",               False, 474),
    City("Киров",             "kirov",              False, 468),
    City("Сочи",              "sochi",              False, 466),
    City("Курск",             "kursk",              False, 440),
    City("Улан-Удэ",          "ulan-ude",           False, 438),
    City("Тверь",             "tver",               False, 416),
    City("Магнитогорск",      "magnitogorsk",       False, 411),
    City("Сургут",            "surgut",             False, 396),
    City("Грозный",           "groznyy",            False, 329),
]


def build_search_url(
    slug: str,
    query: str,
    filters: "SearchFilters | None" = None,
) -> str:
    """
    Строит URL поиска на Авито для заданного города и запроса.

    Аргументы:
        slug:    slug города, например «moskva»
        query:   поисковый запрос, например «диван угловой»
        filters: необязательные фильтры поиска (SearchFilters). Если задан:
                 - дополнительные параметры (pmin, pmax) добавляются к URL;
                 - если выбран пол, к запросу добавляется слово «мужские»
                   или «женские» через пробел перед URL-кодированием.

    Возвращает строку вида (без фильтров):
        https://www.avito.ru/moskva?q=%D0%B4%D0%B8%D0%B2%D0%B0%D0%BD

    Или с фильтрами, например SearchFilters(1000, 5000):
        https://www.avito.ru/moskva?q=%D0%B4%D0%B8%D0%B2%D0%B0%D0%BD&pmax=5000&pmin=1000
    """
    # Если задан фильтр пола — добавляем слово-суффикс к запросу до кодирования
    effective_query = query
    if filters is not None:
        suffix = filters.query_suffix()
        if suffix:
            effective_query = f"{query} {suffix}"

    encoded_query = quote(effective_query, safe="")
    url = f"https://www.avito.ru/{slug}?q={encoded_query}"

    if filters is not None:
        # Вызываем утиной типизацией — жёсткий импорт не нужен в рантайме
        params: dict[str, str] = filters.to_avito_params()
        # Детерминированный порядок ключей для стабильности URL (тесты, кэш)
        for key in sorted(params):
            url += f"&{key}={quote(params[key], safe='')}"

    return url


def get_city_by_slug(slug: str) -> City | None:
    """Возвращает объект City по slug или None, если город не найден."""
    for city in CITIES:
        if city.slug == slug:
            return city
    return None


def get_cities_by_slugs(slugs: list[str]) -> list[City]:
    """Возвращает подмножество CITIES по списку slug'ов, СОХРАНЯЯ порядок
    по убыванию населения (как в CITIES). Неизвестные slug'и игнорирует."""
    slug_set = set(slugs)
    return [city for city in CITIES if city.slug in slug_set]


def is_local_listing(url: str, city_slug: str) -> bool:
    """True, если объявление физически в городе city_slug: первый сегмент пути
    URL совпадает с city_slug. Пустой/без пути URL → False. Работает и с
    абсолютным (https://www.avito.ru/ufa/...), и с относительным (/ufa/...) URL."""
    if not url:
        return False
    from urllib.parse import urlparse
    path = urlparse(url).path
    first_seg = path.lstrip("/").split("/", 1)[0] if path else ""
    return first_seg == city_slug


if __name__ == "__main__":
    # Самотесты — запускается как: python cities.py

    # 1. Ровно 50 городов
    assert len(CITIES) == 50, f"Ожидалось 50 городов, получено {len(CITIES)}"
    print("[OK] len(CITIES) == 50")

    # 2. Все slug'и уникальны
    slugs = [c.slug for c in CITIES]
    assert len(set(slugs)) == 50, "Обнаружены дублирующиеся slug'и"
    print("[OK] Все slug'и уникальны (50 штук)")

    # 3. Сортировка по убыванию населения
    for i in range(len(CITIES) - 1):
        assert CITIES[i].population >= CITIES[i + 1].population, (
            f"Нарушена сортировка по населению: {CITIES[i].name} ({CITIES[i].population}) "
            f"< {CITIES[i + 1].name} ({CITIES[i + 1].population})"
        )
    print("[OK] CITIES отсортирован по убыванию population")

    # 4. has_metro True только у Москвы и Санкт-Петербурга
    metro_cities = [c for c in CITIES if c.has_metro]
    assert len(metro_cities) == 2, f"Ожидалось 2 города с метро, получено {len(metro_cities)}"
    metro_slugs = {c.slug for c in metro_cities}
    assert metro_slugs == {"moskva", "sankt-peterburg"}, (
        f"has_metro=True у неожиданных городов: {metro_slugs}"
    )
    print("[OK] has_metro=True только у moskva и sankt-peterburg")

    # 5. get_cities_by_slugs сохраняет порядок по населению и отбрасывает неизвестные
    result = get_cities_by_slugs(["kazan", "moskva", "нет_такого"])
    assert len(result) == 2, f"Ожидалось 2 города, получено {len(result)}"
    assert result[0].slug == "moskva", f"Первым должна быть Москва, получено {result[0].slug}"
    assert result[1].slug == "kazan", f"Вторым должна быть Казань, получено {result[1].slug}"
    print("[OK] get_cities_by_slugs(['kazan','moskva','net_takogo']) -> [moskva, kazan], dlina 2")

    # 6. get_city_by_slug возвращает правильное population
    ufa = get_city_by_slug("ufa")
    assert ufa is not None, "get_city_by_slug('ufa') вернул None"
    assert ufa.population == 1145, f"Ожидалось population=1145, получено {ufa.population}"
    print("[OK] get_city_by_slug('ufa').population == 1145")

    # 7. is_local_listing — единый хелпер локальности
    assert is_local_listing("https://www.avito.ru/ufa/odezhda/item_1", "ufa") is True, \
        "Абсолютный URL с совпадающим slug должен быть локальным"
    print("[OK] is_local_listing абсолютный URL ufa -> ufa is True")

    assert is_local_listing("https://www.avito.ru/moskva/odezhda/item_1", "ufa") is False, \
        "Абсолютный URL москва не локален для уфы"
    print("[OK] is_local_listing абсолютный URL moskva -> ufa is False")

    assert is_local_listing("/ufa/odezhda/item_1", "ufa") is True, \
        "Относительный URL с совпадающим slug должен быть локальным"
    print("[OK] is_local_listing относительный URL /ufa/... -> ufa is True")

    assert is_local_listing("", "ufa") is False, \
        "Пустой URL должен возвращать False"
    print("[OK] is_local_listing('', 'ufa') is False")

    # 8. build_search_url добавляет гендер-слово в запрос через query_suffix()
    import urllib.parse as _up

    class _FakeFilters:
        def query_suffix(self) -> str:
            return "женские"

        def to_avito_params(self) -> dict:
            return {}

    _u = build_search_url("moskva", "кроссовки nike", _FakeFilters())
    assert "женские" in _up.unquote(_u), (
        f"Гендер-слово не найдено в URL: {_u}"
    )
    print("[OK] гендер-слово добавлено в запрос")

    # 9. Без filters — URL не меняется
    _u_no = build_search_url("moskva", "кроссовки nike")
    assert "женские" not in _up.unquote(_u_no), (
        f"Гендер-слово не должно появляться без filters: {_u_no}"
    )
    print("[OK] без filters гендер-слово в URL отсутствует")

    # 10. Пустой query_suffix — URL не меняется
    class _FakeFiltersEmpty:
        def query_suffix(self) -> str:
            return ""

        def to_avito_params(self) -> dict:
            return {}

    _u_empty = build_search_url("moskva", "кроссовки nike", _FakeFiltersEmpty())
    assert _u_empty == _u_no, (
        f"Пустой suffix не должен изменять URL: {_u_empty!r} != {_u_no!r}"
    )
    print("[OK] пустой query_suffix не изменяет URL")

    print()
    print("=== Все тесты cities.py пройдены успешно ===")
