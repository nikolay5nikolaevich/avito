#!/usr/bin/env bash
# Скрипт первичной установки проекта «Анализ спроса на Авито»
# Запускать в bash (Linux / macOS / WSL).
# Для Windows PowerShell — см. README.md, раздел «Установка на Windows».

set -e  # Прерываем выполнение при любой ошибке

echo "=== Шаг 1: создаём виртуальное окружение ==="
python3 -m venv venv

echo "=== Шаг 2: активируем виртуальное окружение ==="
# shellcheck disable=SC1091
source venv/bin/activate

echo "=== Шаг 3: обновляем pip до актуальной версии ==="
pip install --upgrade pip

echo "=== Шаг 4: устанавливаем зависимости из requirements.txt ==="
pip install -r requirements.txt

echo "=== Шаг 5: устанавливаем браузер Chromium для Playwright ==="
playwright install chromium

echo ""
echo "Установка завершена!"
echo "Чтобы запустить сервис:"
echo "  source venv/bin/activate"
echo "  python app.py"
echo "Затем открой http://localhost:8000 в браузере."
