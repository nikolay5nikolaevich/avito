@echo off
REM Запуск Chrome в режиме отладки для подключения парсера (CDP).
REM Открывает отдельное окно Chrome с выделенным профилем, обычный Chrome не трогает.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\avito-chrome-profile"
