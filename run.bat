@echo off
REM NeoChat v10.0 Ultimate - Скрипт запуска для Windows

echo.
echo   ███╗   ██╗███████╗ ██████╗  ██████╗██╗  ██╗ █████╗ ████████╗
echo   ████╗  ██║██╔════╝██╔═══██╗██╔════╝██║  ██║██╔══██╗╚══██╔══╝
echo   ██╔██╗ ██║█████╗  ██║   ██║██║     ███████║███████║   ██║   
echo   ██║╚██╗██║██╔══╝  ██║   ██║██║     ██╔══██║██╔══██║   ██║   
echo   ██║ ╚████║███████╗╚██████╔╝╚██████╗██║  ██║██║  ██║   ██║   
echo   ╚═╝  ╚═══╝╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   
echo.
echo   v10.0 Ultimate - Мессенджер с видеозвонками и опросами
echo.

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python не установлен!
    echo Скачайте Python с https://www.python.org
    pause
    exit /b 1
)

echo [+] Python найден

REM Проверка websockets
pip list | find "websockets" >nul 2>&1
if errorlevel 1 (
    echo [*] Установка websockets...
    pip install websockets
    if errorlevel 1 (
        echo [!] Ошибка при установке websockets
        pause
        exit /b 1
    )
)

echo [+] Зависимости установлены

REM Запуск сервера
echo.
echo [*] Запуск NeoChat сервера на http://localhost:5001
echo [*] Откройте в браузере: http://localhost:5001
echo.
echo Для тестирования нескольких пользователей откройте несколько вкладок!
echo.
echo Нажмите Ctrl+C для остановки сервера
echo.

python websocket_server.py

pause
