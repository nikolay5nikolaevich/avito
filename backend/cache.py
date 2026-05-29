"""
Модуль кэширования результатов в SQLite.

Сохраняет результаты парсинга на 24 часа, чтобы не долбить Авито повторно.
Используется стандартный модуль sqlite3 — без сторонних зависимостей.

Ключ кэша: нормализованный запрос + "|" + количество объявлений (count)
+ "|" + строка фильтров (filters_key) + "|" + набор городов (cities_key),
например:
  "диван угловой|150||"                        — без фильтра, все города
  "телефон|150|pmin=1000;pmax=5000|"           — с фильтром, все города
  "телефон|150||ekaterinburg,moskva"           — без фильтра, выбранные города
  "телефон|150|pmin=1000|ekaterinburg,moskva"  — с фильтром и городами
Это позволяет различать результаты для одного запроса при разных
значениях count, при разных активных фильтрах или при разных наборах городов.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Время жизни кэша в часах
CACHE_TTL_HOURS: int = 24


# ── Инициализация БД ──────────────────────────────────────────────────────────

def init_db(db_path: str = "cache.db") -> None:
    """
    Создаёт таблицу cache в SQLite, если она ещё не существует.

    Структура таблицы:
        query        — составной ключ вида "нормализованный_запрос|count|filters_key" (PRIMARY KEY)
        results_json — сериализованный JSON с результатами
        created_at   — ISO-8601 timestamp момента сохранения (UTC)

    Args:
        db_path: путь к файлу базы данных (по умолчанию "cache.db")
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    query        TEXT PRIMARY KEY,
                    results_json TEXT NOT NULL,
                    created_at   TEXT NOT NULL
                )
            """)
            conn.commit()
        logger.debug("База данных инициализирована: %s", db_path)
    except sqlite3.Error as exc:
        logger.error("Ошибка при инициализации БД %s: %s", db_path, exc)
        raise


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _normalize_query(query: str) -> str:
    """Нормализует запрос: trim + lowercase, чтобы кэш не дублировался."""
    return query.strip().lower()


def _now_utc() -> datetime:
    """Возвращает текущее время UTC."""
    return datetime.now(tz=timezone.utc)


def _parse_iso(ts: str) -> datetime:
    """Парсит ISO-8601 строку в datetime с timezone=UTC."""
    dt = datetime.fromisoformat(ts)
    # Если timestamp без таймзоны — считаем UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Публичный API ─────────────────────────────────────────────────────────────

def save_result(
    query: str,
    results: object,
    count: int = 150,
    filters_key: str = "",
    cities_key: str = "",
    db_path: str = "cache.db",
) -> None:
    """
    Сериализует results в JSON и сохраняет (или обновляет) запись в БД.

    datetime-объекты сериализуются через default=str.
    Ключ кэша формируется как:
        нормализованный запрос + "|" + count + "|" + filters_key + "|" + cities_key,
    например:
        "диван угловой|150||"                       — без фильтра, все города
        "телефон|150|pmin=1000;pmax=5000|"          — с фильтром, все города
        "телефон|150||ekaterinburg,moskva"          — без фильтра, выбранные города
        "телефон|150|pmin=1000|ekaterinburg,moskva" — с фильтром и городами
    Это позволяет различать кэш для одного запроса при разных count,
    фильтрах или наборах городов.

    Args:
        query:       поисковый запрос пользователя
        results:     любой JSON-сериализуемый объект (список городов и т.д.)
        count:       количество объявлений на город (по умолчанию 150)
        filters_key: строка активных фильтров (по умолчанию "" — без фильтра)
        cities_key:  отсортированный список slug-ов городов через запятую
                     (по умолчанию "" — набор по умолчанию, обратная совместимость)
        db_path:     путь к файлу БД
    """
    key = f"{_normalize_query(query)}|{count}|{filters_key}|{cities_key}"
    try:
        results_json = json.dumps(results, ensure_ascii=False, default=str)
        created_at = _now_utc().isoformat()

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO cache (query, results_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    results_json = excluded.results_json,
                    created_at   = excluded.created_at
                """,
                (key, results_json, created_at),
            )
            conn.commit()

        logger.info("Кэш сохранён для ключа '%s'", key)

    except (sqlite3.Error, TypeError, ValueError) as exc:
        logger.error("Ошибка при сохранении кэша для '%s': %s", key, exc)
        raise


def get_result(
    query: str,
    count: int = 150,
    filters_key: str = "",
    cities_key: str = "",
    db_path: str = "cache.db",
) -> object | None:
    """
    Возвращает десериализованный результат из кэша, если:
      - запись существует
      - запись не старше CACHE_TTL_HOURS часов

    Иначе возвращает None.

    Ключ кэша формируется как:
        нормализованный запрос + "|" + count + "|" + filters_key + "|" + cities_key,
    например "диван угловой|150||" (без фильтра, все города) или
    "телефон|150|pmin=1000;pmax=5000|ekaterinburg,moskva" (с фильтром и городами).
    Разные значения count, filters_key или cities_key дают разные ключи,
    поэтому кэш для разных настроек поиска не смешивается.

    Args:
        query:       поисковый запрос пользователя
        count:       количество объявлений на город (по умолчанию 150)
        filters_key: строка активных фильтров (по умолчанию "" — без фильтра)
        cities_key:  отсортированный список slug-ов городов через запятую
                     (по умолчанию "" — набор по умолчанию, обратная совместимость)
        db_path:     путь к файлу БД

    Returns:
        Десериализованный объект или None.
    """
    key = f"{_normalize_query(query)}|{count}|{filters_key}|{cities_key}"

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT results_json, created_at FROM cache WHERE query = ?",
                (key,),
            )
            row = cursor.fetchone()

        if row is None:
            logger.debug("Кэш не найден для ключа '%s'", key)
            return None

        results_json, created_at_str = row
        created_at = _parse_iso(created_at_str)
        age_hours = (_now_utc() - created_at).total_seconds() / 3600

        if age_hours > CACHE_TTL_HOURS:
            logger.info(
                "Кэш устарел для '%s' (возраст %.1f ч > %d ч)",
                key, age_hours, CACHE_TTL_HOURS,
            )
            return None

        logger.info("Кэш актуален для '%s' (возраст %.1f ч)", key, age_hours)
        return json.loads(results_json)

    except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        logger.error("Ошибка при чтении кэша для '%s': %s", key, exc)
        return None


# ── Самотест ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import timedelta

    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

    TEST_DB = "cache_test.db"

    # Убедимся, что старого тестового файла нет
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    # ── Тест 1: инициализация и базовое сохранение/чтение ────────────────────
    # count не передаётся явно → используется значение по умолчанию 150
    # ключ в БД: "диван угловой|150"
    init_db(TEST_DB)

    sample_data = [
        {"city_slug": "moskva", "avg_views_today": 12.5, "top3": []},
        {"city_slug": "kazan",  "avg_views_today": 7.3,  "top3": []},
    ]

    save_result("диван угловой", sample_data, db_path=TEST_DB)
    cached = get_result("диван угловой", db_path=TEST_DB)

    assert cached is not None, "get_result должен вернуть данные сразу после save_result"
    assert len(cached) == 2,   "Должно быть 2 города в кэше"
    assert cached[0]["city_slug"] == "moskva", "Первый город — Москва"
    print("[OK] Тест 1: сохранение и чтение из кэша")

    # ── Тест 2: нормализация запроса (разный регистр/пробелы) ────────────────
    cached_upper = get_result("  ДИВАН УГЛОВОЙ  ", db_path=TEST_DB)
    assert cached_upper is not None, "Нормализованный запрос должен найти кэш"
    print("[OK] Тест 2: нормализация запроса (trim + lower)")

    # ── Тест 3: несуществующий запрос → None ─────────────────────────────────
    missing = get_result("холодильник двухкамерный", db_path=TEST_DB)
    assert missing is None, "Несуществующий запрос должен вернуть None"
    print("[OK] Тест 3: несуществующий запрос возвращает None")

    # ── Тест 4: перезапись (upsert) ──────────────────────────────────────────
    updated_data = [{"city_slug": "spb", "avg_views_today": 99.0, "top3": []}]
    save_result("диван угловой", updated_data, db_path=TEST_DB)
    cached_upd = get_result("диван угловой", db_path=TEST_DB)

    assert cached_upd is not None,                          "После upsert данные должны быть"
    assert cached_upd[0]["city_slug"] == "spb",             "Должны быть обновлённые данные"
    assert cached_upd[0]["avg_views_today"] == 99.0,        "Среднее должно обновиться"
    print("[OK] Тест 4: upsert — перезапись существующего кэша")

    # ── Тест 5: устаревший кэш → None ────────────────────────────────────────
    # Подделываем created_at, выставляя его в прошлое (25 часов назад).
    # Ключ в БД — "диван угловой|150||" (count=150, filters_key="" и cities_key="" по умолчанию).
    old_ts = (_now_utc() - timedelta(hours=25)).isoformat()
    conn5 = sqlite3.connect(TEST_DB)
    conn5.execute(
        "UPDATE cache SET created_at = ? WHERE query = ?",
        (old_ts, "диван угловой|150||"),
    )
    conn5.commit()
    conn5.close()  # явно закрываем, чтобы Windows отпустила файл

    expired = get_result("диван угловой", db_path=TEST_DB)
    assert expired is None, "Устаревший кэш (25ч) должен вернуть None"
    print("[OK] Тест 5: устаревший кэш (TTL превышен) возвращает None")

    # ── Тест 6: datetime в данных сериализуется без ошибок ───────────────────
    from datetime import datetime as dt
    data_with_dt = {"published_at": dt(2024, 6, 15, 12, 0, 0)}
    save_result("сериализация datetime", data_with_dt, db_path=TEST_DB)
    cached_dt = get_result("сериализация datetime", db_path=TEST_DB)
    assert cached_dt is not None,                                    "Данные с datetime должны сохраняться"
    assert cached_dt["published_at"] == "2024-06-15 12:00:00",      "datetime преобразован в str через default=str"
    print("[OK] Тест 6: datetime сериализуется корректно")

    # ── Тест 7: изоляция по count — разные count дают разные записи ──────────
    # Сохраняем одинаковый запрос "телефон" с count=10 и count=30.
    # Каждый должен лежать под своим ключом и не перекрываться.
    save_result(
        "телефон",
        [{"city_slug": "x", "avg_views_today": 1.0, "top3": []}],
        count=10,
        db_path=TEST_DB,
    )
    save_result(
        "телефон",
        [{"city_slug": "y", "avg_views_today": 2.0, "top3": []}],
        count=30,
        db_path=TEST_DB,
    )

    result_10 = get_result("телефон", count=10, db_path=TEST_DB)
    result_30 = get_result("телефон", count=30, db_path=TEST_DB)

    assert result_10 is not None,                  "Кэш для count=10 должен существовать"
    assert result_10[0]["city_slug"] == "x",        "count=10 должен отдавать город 'x'"
    assert result_30 is not None,                  "Кэш для count=30 должен существовать"
    assert result_30[0]["city_slug"] == "y",        "count=30 должен отдавать город 'y'"
    # Убеждаемся, что они не смешались
    assert result_10[0]["city_slug"] != result_30[0]["city_slug"], \
        "count=10 и count=30 не должны возвращать одни и те же данные"
    print("[OK] Тест 7: изоляция по count — кэш не путает 10 и 30 объявлений")

    # ── Тест 8: изоляция по filters_key — разные фильтры дают разные записи ──
    # Один и тот же запрос "телефон" с count=150, но с двумя разными filters_key.
    # Каждый должен лежать под своим ключом и не перекрываться.
    save_result("телефон", [{"city_slug": "cheap"}], count=150, filters_key="pmax=5000", db_path=TEST_DB)
    save_result("телефон", [{"city_slug": "pricey"}], count=150, filters_key="pmin=50000", db_path=TEST_DB)

    result_cheap  = get_result("телефон", count=150, filters_key="pmax=5000",  db_path=TEST_DB)
    result_pricey = get_result("телефон", count=150, filters_key="pmin=50000", db_path=TEST_DB)

    assert result_cheap  is not None,                    "Кэш для pmax=5000 должен существовать"
    assert result_cheap[0]["city_slug"]  == "cheap",     "pmax=5000 должен отдавать 'cheap'"
    assert result_pricey is not None,                    "Кэш для pmin=50000 должен существовать"
    assert result_pricey[0]["city_slug"] == "pricey",    "pmin=50000 должен отдавать 'pricey'"
    assert result_cheap[0]["city_slug"] != result_pricey[0]["city_slug"], \
        "Разные filters_key не должны возвращать одни и те же данные"
    print("[OK] Тест 8: изоляция по filters_key — кэш не путает разные фильтры")

    # ── Тест 9: изоляция по cities_key — разные наборы городов не смешиваются ─
    # Один и тот же запрос "ноут" с одинаковым count=150, но с разными cities_key.
    # Каждый должен лежать под своим ключом и не перекрываться.
    save_result("ноут", [{"set": "a"}], count=150, cities_key="moskva", db_path=TEST_DB)
    save_result("ноут", [{"set": "b"}], count=150, cities_key="moskva,spb", db_path=TEST_DB)

    result_moskva     = get_result("ноут", count=150, cities_key="moskva",     db_path=TEST_DB)
    result_moskva_spb = get_result("ноут", count=150, cities_key="moskva,spb", db_path=TEST_DB)

    assert result_moskva is not None,                   "Кэш для cities_key='moskva' должен существовать"
    assert result_moskva[0]["set"] == "a",              "cities_key='moskva' должен отдавать набор 'a'"
    assert result_moskva_spb is not None,               "Кэш для cities_key='moskva,spb' должен существовать"
    assert result_moskva_spb[0]["set"] == "b",          "cities_key='moskva,spb' должен отдавать набор 'b'"
    assert result_moskva[0]["set"] != result_moskva_spb[0]["set"], \
        "Разные cities_key не должны возвращать одни и те же данные"
    print("[OK] Тест 9: изоляция по cities_key — кэш не путает разные наборы городов")

    # ── Удаляем временный файл ────────────────────────────────────────────────
    # Принудительно освобождаем все объекты SQLite перед удалением файла (нужно для Windows)
    import gc
    gc.collect()
    os.remove(TEST_DB)
    assert not os.path.exists(TEST_DB), "Тестовая БД должна быть удалена"
    print("[OK] Временный файл cache_test.db удалён")

    print("\n=== Все тесты cache.py пройдены успешно ===")
