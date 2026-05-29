"""
Модуль аналитики: фильтрация объявлений и расчёт метрик по городам.

Принимает сырые данные от парсера, применяет фильтр «свежести»,
фильтр локальности (отбрасываем объявления из чужих регионов),
считает среднее просмотров/день и формирует топ-3 по уникальным адресам.
"""

import logging
from datetime import datetime
from typing import Any

from cities import City, is_local_listing

logger = logging.getLogger(__name__)

# ── Константы ────────────────────────────────────────────────────────────────

# TARGET_COUNT оставлен для справки, но больше не является знаменателем среднего.
# Знаменатель теперь = число местных объявлений после фильтра свежести (local_count).
TARGET_COUNT: int = 150  # больше не знаменатель

# Параметры фильтра «свежих» объявлений
FRESH_HOURS: float = 24.0          # объявление считается «свежим», если моложе этого порога
FRESH_MIN_VIEWS: int = 1           # нижняя граница диапазона «подозрительных» просмотров
FRESH_MAX_VIEWS: int = 30          # верхняя граница диапазона «подозрительных» просмотров


# ── Фильтрация ────────────────────────────────────────────────────────────────

def filter_listings(listings: list[dict]) -> list[dict]:
    """
    Фильтрует объявления по правилу из CLAUDE.md.

    Исключает объявление, если:
        age_hours < FRESH_HOURS  И  FRESH_MIN_VIEWS <= views_today <= FRESH_MAX_VIEWS

    Объявления с None в age_hours или views_today считаются валидными (включаем).
    Никогда не бросает исключений — ошибка одного объявления логируется и пропускается.

    Args:
        listings: список объявлений в формате контракта данных

    Returns:
        Отфильтрованный список объявлений.
    """
    result: list[dict] = []

    for item in listings:
        try:
            age: float | None = item.get("age_hours")
            views: int | None = item.get("views_today")

            # Если хотя бы одно из полей None — включаем объявление
            if age is None or views is None:
                result.append(item)
                continue

            # Исключаем только свежие объявления с подозрительно малым числом просмотров
            is_fresh: bool = age < FRESH_HOURS
            is_suspicious: bool = FRESH_MIN_VIEWS <= views <= FRESH_MAX_VIEWS

            if is_fresh and is_suspicious:
                logger.debug(
                    "Объявление исключено (свежее + мало просмотров): "
                    "age_hours=%.1f, views_today=%d, url=%s",
                    age, views, item.get("url", "—"),
                )
                continue

            result.append(item)

        except Exception as exc:
            logger.warning(
                "Ошибка при фильтрации объявления %s: %s",
                item.get("url", "—"), exc,
            )
            # Включаем объявление при ошибке, чтобы не терять данные
            result.append(item)

    logger.debug("Фильтрация: из %d осталось %d объявлений", len(listings), len(result))
    return result


# ── Фильтр локальности ────────────────────────────────────────────────────────

def _is_local(item: dict, city_slug: str) -> bool:
    """True, если объявление физически в этом городе.
    Делегирует в cities.is_local_listing — единый источник истины."""
    return is_local_listing(item.get("url") or "", city_slug)


# ── Вспомогательные функции ───────────────────────────────────────────────────

def top3_distinct(listings: list[dict], key_field: str) -> list[dict]:
    """
    Возвращает топ-3 объявления по убыванию views_today,
    обязательно с разными значениями поля key_field.

    Переиспользуется для address (по всему городу) и для metro (внутри станции).
    None в views_today считается 0 при сортировке.

    Args:
        listings:  список объявлений
        key_field: имя поля для проверки уникальности (например "address" или "metro")

    Returns:
        Список до 3 объявлений в формате {title, url, address, views_today}.
    """
    # Сортируем по убыванию просмотров (None → 0)
    sorted_items = sorted(
        listings,
        key=lambda x: x.get("views_today") or 0,
        reverse=True,
    )

    top: list[dict] = []
    seen_keys: set[str] = set()

    for item in sorted_items:
        key_value: Any = item.get(key_field)

        # Нормализуем ключ: None и пустая строка объединяются
        normalized_key: str = str(key_value).strip() if key_value else ""

        if normalized_key in seen_keys:
            continue

        seen_keys.add(normalized_key)
        top.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "address": item.get("address"),
            "views_today": item.get("views_today"),
        })

        if len(top) >= 3:
            break

    return top


def _calc_avg(listings: list[dict], denominator: int) -> float:
    """
    Считает среднее views_today.

    Args:
        listings:    список объявлений (уже отфильтрованных)
        denominator: делитель (local_count для города, len(group) для метро)

    Returns:
        Среднее с округлением до 2 знаков после запятой. 0.0, если denominator==0.
    """
    if denominator == 0:
        return 0.0

    total: int = sum(item.get("views_today") or 0 for item in listings)
    return round(total / denominator, 2)


# ── Группировка по метро ──────────────────────────────────────────────────────

def _metro_breakdown(listings: list[dict]) -> list[dict]:
    """
    Группирует объявления по станции метро и считает метрики внутри каждой группы.

    Объявления с metro=None или metro="" попадают в группу «Без метро».
    Среднее для каждой станции = сумма views_today / число объявлений в группе
    (НЕ TARGET_COUNT=150, потому что на уровне станции выборка ограничена).

    Args:
        listings: отфильтрованные объявления города с has_metro=True

    Returns:
        Список dict'ов {metro, avg_views_today, top3}, отсортированный
        по убыванию avg_views_today.
    """
    groups: dict[str, list[dict]] = {}

    for item in listings:
        metro: str | None = item.get("metro")
        group_key: str = metro.strip() if metro and metro.strip() else "Без метро"
        groups.setdefault(group_key, []).append(item)

    breakdown: list[dict] = []

    for metro_name, group in groups.items():
        # Делитель — число объявлений в группе (не 150!)
        avg = _calc_avg(group, denominator=len(group))
        breakdown.append({
            "metro": metro_name,
            "avg_views_today": avg,
            "top3": top3_distinct(group, key_field="address"),
        })

    # Сортируем по убыванию среднего просмотров
    breakdown.sort(key=lambda x: x["avg_views_today"], reverse=True)
    return breakdown


# ── Главные функции ───────────────────────────────────────────────────────────

def compute_city_result(city: City, listings: list[dict]) -> dict:
    """
    Вычисляет итоговый результат по одному городу.

    Шаги:
    1. Отбирает местные объявления: первый сегмент URL совпадает с city.slug.
    2. Применяет фильтр свежести к местным объявлениям.
    3. Считает avg_views_today = сумма views_today местных / local_count
       (знаменатель = число местных объявлений после фильтра, НЕ фиксированное N).
    4. Формирует топ-3 с разными address среди местных.
    5. Если has_metro=True — добавляет разбивку по метро среди местных.

    Args:
        city:     объект City из cities.py
        listings: сырой список объявлений от парсера

    Returns:
        dict по контракту данных (city_slug, city_name, avg_views_today,
        local_count, top3, metro_breakdown).
    """
    # Шаг 1: оставляем только местные объявления
    local = [it for it in listings if _is_local(it, city.slug)]

    # Шаг 2: фильтр свежести применяем к местным
    filtered = filter_listings(local)

    local_count = len(filtered)

    logger.info(
        "Город %s: всего %d объявлений, местных %d, после фильтра свежести %d",
        city.name, len(listings), len(local), local_count,
    )

    # Шаг 3: среднее = сумма / local_count (при 0 → 0.0)
    avg = _calc_avg(filtered, denominator=local_count)

    # Шаг 4: топ-3 с разными адресами среди местных
    top3 = top3_distinct(filtered, key_field="address")

    # Шаг 5: разбивка по метро только для городов с has_metro=True
    metro_bd: list[dict] | None = None
    if city.has_metro:
        metro_bd = _metro_breakdown(filtered)

    return {
        "city_slug": city.slug,
        "city_name": city.name,
        "avg_views_today": avg,
        "local_count": local_count,
        "top3": top3,
        "metro_breakdown": metro_bd,
    }


def compute_all(results_by_city: dict[str, list[dict]], cities_map: dict[str, "City"]) -> list[dict]:
    """
    Обрабатывает все города и возвращает список результатов,
    отсортированных по убыванию avg_views_today.

    Args:
        results_by_city: {city_slug: [listings...]}
        cities_map:      {city_slug: City}

    Returns:
        Список dict по контракту, отсортированный по убыванию avg_views_today.
    """
    results: list[dict] = []

    for slug, listings in results_by_city.items():
        city = cities_map.get(slug)
        if city is None:
            logger.warning("Город со slug='%s' не найден в справочнике, пропускаем", slug)
            continue
        try:
            result = compute_city_result(city, listings)
            results.append(result)
        except Exception as exc:
            logger.error("Ошибка при обработке города %s: %s", slug, exc)

    results.sort(key=lambda x: x["avg_views_today"], reverse=True)
    return results


# ── Самотест ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

    from cities import City

    # ── Вспомогательные данные ────────────────────────────────────────────────

    _counter = 0

    def make_listing(
        address: str,
        metro: str | None,
        views_today: int | None,
        age_hours: float | None,
        city_slug: str = "test",
    ) -> dict:
        """Формирует тестовое объявление с реальным URL нужного города."""
        global _counter
        _counter += 1
        url = f"https://www.avito.ru/{city_slug}/item_{_counter}"
        return {
            "url": url,
            "title": f"Объявление {_counter}",
            "price": 1000,
            "address": address,
            "metro": metro,
            "views_total": (views_today or 0) * 10,
            "views_today": views_today,
            "published_at": datetime(2024, 1, 1),
            "age_hours": age_hours,
        }

    # ── Тест 1: фильтр — свежее с 5 просмотрами исключается ──────────────────
    fresh_suspicious = make_listing("Ленина 1",    None, views_today=5,    age_hours=10.0)
    old_suspicious   = make_listing("Мира 1",      None, views_today=5,    age_hours=30.0)
    fresh_ok         = make_listing("Советская 1", None, views_today=40,   age_hours=10.0)
    none_age         = make_listing("Победы 1",    None, views_today=5,    age_hours=None)
    none_views       = make_listing("Гагарина 1",  None, views_today=None, age_hours=5.0)

    filtered_t1 = filter_listings([fresh_suspicious, old_suspicious, fresh_ok, none_age, none_views])

    assert fresh_suspicious not in filtered_t1, "Свежее + мало просмотров должно быть исключено"
    assert old_suspicious in filtered_t1,       "Старое + мало просмотров должно быть включено"
    assert fresh_ok in filtered_t1,             "Свежее + много просмотров должно быть включено"
    assert none_age in filtered_t1,             "None в age_hours → включаем"
    assert none_views in filtered_t1,           "None в views_today → включаем"
    print("[OK] Тест 1: фильтрация пройдена")

    # ── Тест 2: среднее = среднее по местным ─────────────────────────────────
    # 3 местных объявления (slug "test") с views_today=150,90,60
    # сумма=300, local_count=3, avg=100.0
    city_no_metro = City(name="Тест", slug="test", has_metro=False, population=100)
    local_three = [
        make_listing("Адрес 1", None, views_today=150, age_hours=100.0, city_slug="test"),
        make_listing("Адрес 2", None, views_today=90,  age_hours=100.0, city_slug="test"),
        make_listing("Адрес 3", None, views_today=60,  age_hours=100.0, city_slug="test"),
    ]
    result_avg = compute_city_result(city_no_metro, local_three)

    assert result_avg["avg_views_today"] == 100.0, (
        f"Ожидалось 100.0, получено {result_avg['avg_views_today']}"
    )
    assert result_avg["local_count"] == 3, (
        f"Ожидалось local_count=3, получено {result_avg['local_count']}"
    )
    assert "local_count" in result_avg, "В результате должен быть ключ local_count"
    print("[OK] Тест 2: среднее по местным пройдено (300/3=100.0)")

    # ── Тест 2б: фильтр локальности — чужие отбрасываются ───────────────────
    # 3 местных (slug "test") + 2 чужих (slug "moskva")
    # Проверяем: local_count==3, avg считается только по местным, чужие не попали
    local_items = [
        make_listing("Улица A", None, views_today=90, age_hours=100.0, city_slug="test"),
        make_listing("Улица B", None, views_today=60, age_hours=100.0, city_slug="test"),
        make_listing("Улица C", None, views_today=30, age_hours=100.0, city_slug="test"),
    ]
    foreign_items = [
        make_listing("Тверская 1", None, views_today=500, age_hours=100.0, city_slug="moskva"),
        make_listing("Тверская 2", None, views_today=400, age_hours=100.0, city_slug="moskva"),
    ]
    result_local = compute_city_result(city_no_metro, local_items + foreign_items)

    assert result_local["local_count"] == 3, (
        f"Ожидалось local_count=3 (чужие отброшены), получено {result_local['local_count']}"
    )
    # Среднее только по местным: (90+60+30)/3 = 60.0
    assert result_local["avg_views_today"] == 60.0, (
        f"Ожидалось avg=60.0 (без чужих), получено {result_local['avg_views_today']}"
    )
    # URL чужих объявлений не должны попасть в топ-3
    top3_urls = {it["url"] for it in result_local["top3"]}
    for fi in foreign_items:
        assert fi["url"] not in top3_urls, f"Чужое объявление {fi['url']} не должно быть в топ-3"
    print("[OK] Тест 2б: фильтр локальности — чужие отброшены")

    # ── Тест 2в: нет местных объявлений ──────────────────────────────────────
    # Все объявления из «moskva», city_slug="test" → local_count==0, avg==0.0
    only_foreign = [
        make_listing("Невский пр.", None, views_today=200, age_hours=100.0, city_slug="moskva"),
        make_listing("Арбат",       None, views_today=300, age_hours=100.0, city_slug="moskva"),
    ]
    result_empty = compute_city_result(city_no_metro, only_foreign)

    assert result_empty["local_count"] == 0, (
        f"Ожидалось local_count=0, получено {result_empty['local_count']}"
    )
    assert result_empty["avg_views_today"] == 0.0, (
        f"Ожидалось avg=0.0, получено {result_empty['avg_views_today']}"
    )
    assert result_empty["top3"] == [], (
        f"Ожидалось top3=[], получено {result_empty['top3']}"
    )
    print("[OK] Тест 2в: нет местных — local_count=0, avg=0.0, top3=[]")

    # ── Тест 3: топ-3 с разными адресами ─────────────────────────────────────
    # 4 объявления: 2 с адресом «Ленина», 1 с «Мира», 1 с «Советской»
    # Топ по просмотрам: Ленина-100, Мира-90, Ленина-80, Советская-70
    # Ожидаемый топ-3: Ленина-100, Мира-90, Советская-70 (Ленина-80 пропускается)
    dup_listings = [
        make_listing("Ленина",    None, views_today=100, age_hours=100.0),
        make_listing("Мира",      None, views_today=90,  age_hours=100.0),
        make_listing("Ленина",    None, views_today=80,  age_hours=100.0),
        make_listing("Советская", None, views_today=70,  age_hours=100.0),
    ]
    top3_result = top3_distinct(dup_listings, key_field="address")

    assert len(top3_result) == 3, f"Ожидалось 3 элемента, получено {len(top3_result)}"
    assert top3_result[0]["address"] == "Ленина",    "Первое место — Ленина-100"
    assert top3_result[1]["address"] == "Мира",      "Второе место — Мира-90"
    assert top3_result[2]["address"] == "Советская", "Третье место — Советская-70 (Ленина-80 пропущена)"
    print("[OK] Тест 3: топ-3 с разными адресами пройден")

    # ── Тест 4: разбивка по метро ─────────────────────────────────────────────
    # Объявления с метро Арбатская (views=200,100), Тверская (views=150), None (→ «Без метро», views=50)
    # Все объявления с slug "moskva", чтобы _is_local пропустил их
    city_metro = City(name="Москва", slug="moskva", has_metro=True, population=13010)
    metro_listings = [
        make_listing("Улица 1", "Арбатская", views_today=200, age_hours=100.0, city_slug="moskva"),
        make_listing("Улица 2", "Арбатская", views_today=100, age_hours=100.0, city_slug="moskva"),
        make_listing("Улица 3", "Тверская",  views_today=150, age_hours=100.0, city_slug="moskva"),
        make_listing("Улица 4", None,         views_today=50,  age_hours=100.0, city_slug="moskva"),
    ]
    result_metro = compute_city_result(city_metro, metro_listings)

    assert result_metro["metro_breakdown"] is not None, "metro_breakdown должен быть заполнен"
    metro_names = {g["metro"] for g in result_metro["metro_breakdown"]}
    assert "Арбатская" in metro_names, "Должна быть группа Арбатская"
    assert "Тверская"  in metro_names, "Должна быть группа Тверская"
    assert "Без метро" in metro_names, "Должна быть группа Без метро"

    # Среднее Арбатской: (200+100)/2 = 150.0
    arb = next(g for g in result_metro["metro_breakdown"] if g["metro"] == "Арбатская")
    assert arb["avg_views_today"] == 150.0, (
        f"Арбатская: ожидалось 150.0, получено {arb['avg_views_today']}"
    )
    # Топ-3 внутри Арбатской: 2 разных адреса
    assert len(arb["top3"]) == 2, "В Арбатской 2 объявления с разными адресами"
    assert "local_count" in result_metro, "В результате с метро тоже должен быть ключ local_count"
    print("[OK] Тест 4: разбивка по метро пройдена")

    # ── Тест 5: города без метро не имеют metro_breakdown ────────────────────
    assert result_avg["metro_breakdown"] is None, "Город без метро: metro_breakdown должен быть None"
    print("[OK] Тест 5: отсутствие metro_breakdown для городов без метро")

    print("\n=== Все тесты analytics.py пройдены успешно ===")
