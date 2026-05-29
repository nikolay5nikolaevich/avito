"""
Фильтры поиска для запросов на Авито.

Содержит дата-класс SearchFilters — иммутабельный контейнер параметров фильтрации.
Первый фильтр — диапазон цены (pmin/pmax). Второй — пол (gender).
Гендерный фильтр реализуется добавлением слова к поисковому запросу
(«кроссовки nike» → «кроссовки nike мужские»), а не URL-параметром Авито.
Архитектура рассчитана на дальнейшее расширение.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchFilters:
    """
    Параметры фильтрации поиска Авито.

    Атрибуты:
        price_min: минимальная цена в рублях (None — не задана)
        price_max: максимальная цена в рублях (None — не задана)
        gender:    пол ("male" / "female" / None)
    """

    price_min: int | None = None
    price_max: int | None = None
    gender: str | None = None

    # ── Фабричный метод ───────────────────────────────────────────────────────

    @classmethod
    def from_form(cls, price_min, price_max, gender=None) -> "SearchFilters":
        """
        Создаёт SearchFilters из сырых значений HTML-формы.

        Нормализация цены:
            - Пустая строка, None или непарсируемое значение → None.
            - Отрицательное число → None.
            - Иначе → int.
            Если оба значения заданы и price_min > price_max — значения меняются местами.

        Нормализация gender:
            - "male" / "м" / "муж" / "мужское" / "man" → "male"
            - "female" / "ж" / "жен" / "женское" / "woman" → "female"
            - Всё остальное (None, пусто, неизвестное) → None

        Никогда не бросает исключений.

        Аргументы:
            price_min: сырое значение минимальной цены (str, int, None и т.д.)
            price_max: сырое значение максимальной цены (str, int, None и т.д.)
            gender:    сырое значение пола (str, None и т.д.)

        Возвращает:
            Экземпляр SearchFilters с нормализованными значениями.
        """
        pmin = _parse_price(price_min)
        pmax = _parse_price(price_max)

        # Если оба заданы и порядок перепутан — меняем местами
        if pmin is not None and pmax is not None and pmin > pmax:
            pmin, pmax = pmax, pmin

        gen = _parse_gender(gender)

        return cls(price_min=pmin, price_max=pmax, gender=gen)

    # ── Публичные методы ──────────────────────────────────────────────────────

    def to_avito_params(self) -> dict[str, str]:
        """
        Возвращает словарь параметров URL для Авито.

        Включает только заданные значения:
            price_min → ключ "pmin"
            price_max → ключ "pmax"

        gender сюда НЕ включается — он добавляется к тексту запроса
        через query_suffix(), а не как URL-параметр.

        Возвращает пустой словарь {}, если оба значения None.
        """
        params: dict[str, str] = {}
        if self.price_min is not None:
            params["pmin"] = str(self.price_min)
        if self.price_max is not None:
            params["pmax"] = str(self.price_max)
        return params

    def query_suffix(self) -> str:
        """
        Возвращает слово-суффикс для добавления к поисковому запросу.

        Использование: query + " " + filters.query_suffix() (если суффикс непустой).

        Возвращает:
            "мужские"  — если gender == "male"
            "женские"  — если gender == "female"
            ""         — если gender не задан
        """
        if self.gender == "male":
            return "мужские"
        if self.gender == "female":
            return "женские"
        return ""

    def cache_key_part(self) -> str:
        """
        Возвращает стабильную строку для включения в ключ кэша.

        Части собираются в фиксированном порядке (pmin, pmax, gender)
        и соединяются через «;». Если ни один фильтр не задан — пустая строка.
        Gender добавляется только когда задан (обратная совместимость: ключи
        без пола не изменились).

        Примеры:
            (1000, 5000, None)     → "pmin=1000;pmax=5000"
            (None, 5000, None)     → "pmax=5000"
            (None, None, "female") → "gender=female"
            (1000, None, "male")   → "pmin=1000;gender=male"
            (None, None, None)     → ""
        """
        parts: list[str] = []
        if self.price_min is not None:
            parts.append(f"pmin={self.price_min}")
        if self.price_max is not None:
            parts.append(f"pmax={self.price_max}")
        if self.gender is not None:
            parts.append(f"gender={self.gender}")
        return ";".join(parts)

    def describe(self) -> str:
        """
        Возвращает объединённое человекочитаемое описание фильтров для отображения на сайте.

        Ценовая часть:
            (1000, 5000) → "от 1000 ₽ до 5000 ₽"
            (1000, None) → "от 1000 ₽"
            (None, 5000) → "до 5000 ₽"
        Гендерная часть:
            "male"   → "мужское"
            "female" → "женское"
        Части соединяются через «, », пустые пропускаются.

        Примеры:
            (1000, 5000, "male")   → "от 1000 ₽ до 5000 ₽, мужское"
            (None, None, "female") → "женское"
            (None, None, None)     → ""
        """
        parts: list[str] = []

        # Ценовая часть
        if self.price_min is not None and self.price_max is not None:
            parts.append(f"от {self.price_min} ₽ до {self.price_max} ₽")
        elif self.price_min is not None:
            parts.append(f"от {self.price_min} ₽")
        elif self.price_max is not None:
            parts.append(f"до {self.price_max} ₽")

        # Гендерная часть
        if self.gender == "male":
            parts.append("мужское")
        elif self.gender == "female":
            parts.append("женское")

        return ", ".join(parts)


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _parse_price(value) -> int | None:
    """
    Нормализует одно значение цены из формы.

    Возвращает int если значение парсится как целое положительное число,
    иначе None. Никогда не бросает исключений.
    """
    if value is None:
        return None
    try:
        stripped = str(value).strip()
        if not stripped:
            return None
        parsed = int(stripped)
        if parsed < 0:
            return None
        return parsed
    except (ValueError, TypeError):
        return None


def _parse_gender(value) -> str | None:
    """
    Нормализует значение пола из формы.

    Синонимы:
        "male" / "м" / "муж" / "мужское" / "man" → "male"
        "female" / "ж" / "жен" / "женское" / "woman" → "female"
    Всё остальное (None, пустая строка, неизвестное) → None.
    Никогда не бросает исключений.
    """
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in ("male", "м", "муж", "мужское", "man"):
        return "male"
    if normalized in ("female", "ж", "жен", "женское", "woman"):
        return "female"
    return None


# ── Самотест ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── Тест 1: пустые/None значения → оба None ───────────────────────────────
    f = SearchFilters.from_form(None, None)
    assert f.price_min is None and f.price_max is None, "Оба None"
    assert f.to_avito_params() == {}, "to_avito_params для None/None == {}"
    assert f.cache_key_part() == "", "cache_key_part для None/None == ''"
    assert f.describe() == "", "describe для None/None == ''"
    print("[OK] Тест 1: None/None пройден")

    # ── Тест 2: пустые строки → оба None ─────────────────────────────────────
    f2 = SearchFilters.from_form("", "")
    assert f2.price_min is None and f2.price_max is None, "Пустые строки == None"
    assert f2.to_avito_params() == {}
    assert f2.cache_key_part() == ""
    assert f2.describe() == ""
    print("[OK] Тест 2: пустые строки пройден")

    # ── Тест 3: корректные строковые значения ─────────────────────────────────
    f3 = SearchFilters.from_form("1000", "5000")
    assert f3.price_min == 1000, f"price_min ожидалось 1000, получено {f3.price_min}"
    assert f3.price_max == 5000, f"price_max ожидалось 5000, получено {f3.price_max}"
    print("[OK] Тест 3: from_form('1000', '5000') пройден")

    # ── Тест 4: swap — min > max ──────────────────────────────────────────────
    f4 = SearchFilters.from_form("5000", "1000")
    assert f4.price_min == 1000, f"После swap price_min == 1000, получено {f4.price_min}"
    assert f4.price_max == 5000, f"После swap price_max == 5000, получено {f4.price_max}"
    print("[OK] Тест 4: swap (5000, 1000) -> (1000, 5000) пройден")

    # ── Тест 5: отрицательное значение → None ────────────────────────────────
    f5 = SearchFilters.from_form("-100", "5000")
    assert f5.price_min is None, f"Отрицательное == None, получено {f5.price_min}"
    assert f5.price_max == 5000, f"price_max == 5000, получено {f5.price_max}"
    print("[OK] Тест 5: отрицательный price_min отброшен")

    # ── Тест 6: непарсируемые строки → оба None ──────────────────────────────
    f6 = SearchFilters.from_form("abc", "")
    assert f6.price_min is None and f6.price_max is None, "Непарсируемые == None"
    print("[OK] Тест 6: from_form('abc', '') -> оба None пройден")

    # ── Тест 7: to_avito_params ───────────────────────────────────────────────
    params_both = SearchFilters(price_min=1000, price_max=5000).to_avito_params()
    assert params_both == {"pmin": "1000", "pmax": "5000"}, (
        f"Ожидалось {{'pmin':'1000','pmax':'5000'}}, получено {params_both}"
    )
    params_max_only = SearchFilters(price_min=None, price_max=5000).to_avito_params()
    assert params_max_only == {"pmax": "5000"}, (
        f"Ожидалось {{'pmax':'5000'}}, получено {params_max_only}"
    )
    # gender не должен попадать в URL-параметры
    params_with_gender = SearchFilters(price_min=1000, price_max=None, gender="male").to_avito_params()
    assert params_with_gender == {"pmin": "1000"}, (
        f"gender не в URL-параметрах, получено {params_with_gender}"
    )
    print("[OK] Тест 7: to_avito_params пройден")

    # ── Тест 8: cache_key_part ────────────────────────────────────────────────
    assert SearchFilters(price_min=1000, price_max=5000).cache_key_part() == "pmin=1000;pmax=5000"
    assert SearchFilters(price_min=None, price_max=5000).cache_key_part() == "pmax=5000"
    assert SearchFilters(price_min=None, price_max=None).cache_key_part() == ""
    print("[OK] Тест 8: cache_key_part (цена) пройден")

    # ── Тест 9: describe (цена) ───────────────────────────────────────────────
    assert SearchFilters(price_min=1000, price_max=5000).describe() == "от 1000 ₽ до 5000 ₽"
    assert SearchFilters(price_min=1000, price_max=None).describe() == "от 1000 ₽"
    assert SearchFilters(price_min=None, price_max=5000).describe() == "до 5000 ₽"
    assert SearchFilters(price_min=None, price_max=None).describe() == ""
    print("[OK] Тест 9: describe (цена) пройден")

    # ── Тест 10: gender — базовые значения ───────────────────────────────────
    fg_f = SearchFilters.from_form(None, None, "female")
    assert fg_f.gender == "female", f"gender == 'female', получено {fg_f.gender}"
    assert fg_f.query_suffix() == "женские", f"query_suffix == 'женские', получено {fg_f.query_suffix()}"
    print("[OK] Тест 10: from_form(None, None, 'female') пройден")

    fg_m = SearchFilters.from_form(None, None, "male")
    assert fg_m.query_suffix() == "мужские", f"query_suffix == 'мужские', получено {fg_m.query_suffix()}"
    print("[OK] Тест 11: from_form(None, None, 'male').query_suffix() пройден")

    # ── Тест 12: синоним «женское» → "female" ────────────────────────────────
    fg_syn = SearchFilters.from_form(None, None, "женское")
    assert fg_syn.gender == "female", f"Синоним 'женское' == 'female', получено {fg_syn.gender}"
    print("[OK] Тест 12: синоним 'женское' == 'female' пройден")

    # ── Тест 13: gender == None → query_suffix == "" ─────────────────────────
    fg_none = SearchFilters.from_form(None, None, None)
    assert fg_none.gender is None, f"gender is None, получено {fg_none.gender}"
    assert fg_none.query_suffix() == "", f"query_suffix == '', получено {fg_none.query_suffix()}"
    print("[OK] Тест 13: gender=None, query_suffix='' пройден")

    # ── Тест 14: cache_key_part с gender ─────────────────────────────────────
    ck1 = SearchFilters.from_form(None, None, "female").cache_key_part()
    assert ck1 == "gender=female", f"Ожидалось 'gender=female', получено '{ck1}'"

    ck2 = SearchFilters.from_form("1000", None, "male").cache_key_part()
    assert ck2 == "pmin=1000;gender=male", f"Ожидалось 'pmin=1000;gender=male', получено '{ck2}'"

    ck3 = SearchFilters.from_form(None, None, None).cache_key_part()
    assert ck3 == "", f"Ожидалось '', получено '{ck3}'"
    print("[OK] Тест 14: cache_key_part с gender пройден")

    # ── Тест 15: describe с gender ────────────────────────────────────────────
    d1 = SearchFilters.from_form("1000", "5000", "male").describe()
    assert d1 == "от 1000 ₽ до 5000 ₽, мужское", f"Ожидалось 'от 1000 ₽ до 5000 ₽, мужское', получено '{d1}'"

    d2 = SearchFilters.from_form(None, None, "female").describe()
    assert d2 == "женское", f"Ожидалось 'женское', получено '{d2}'"

    d3 = SearchFilters.from_form(None, None, None).describe()
    assert d3 == "", f"Ожидалось '', получено '{d3}'"
    print("[OK] Тест 15: describe с gender пройден")

    print("\n=== Все тесты filters.py пройдены успешно ===")
