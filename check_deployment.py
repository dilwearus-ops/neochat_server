#!/usr/bin/env python3
"""
NeoChat Render Deployment Checker
Проверяет готовность проекта к развёртыванию на Render
"""

import os
import sys
import subprocess

def check_file(filepath, description):
    """Проверяет наличие файла"""
    exists = os.path.isfile(filepath)
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {filepath}")
    return exists

def check_content(filepath, content, description):
    """Проверяет наличие содержимого в файле"""
    if not os.path.isfile(filepath):
        print(f"❌ {description}: файл не найден")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        file_content = f.read()
        exists = content in file_content
        status = "✅" if exists else "⚠️"
        print(f"{status} {description}")
        return exists

def check_python_packages():
    """Проверяет установленные пакеты"""
    try:
        import websockets
        print(f"✅ websockets установлен: {websockets.__version__}")
    except ImportError:
        print(f"❌ websockets: не установлен")
    
    try:
        import aiohttp
        print(f"✅ aiohttp установлен: {aiohttp.__version__}")
    except ImportError:
        print(f"❌ aiohttp: не установлен")

def main():
    print("=" * 60)
    print("🔍 NeoChat Deployment Readiness Check")
    print("=" * 60)
    print()
    
    all_ok = True
    
    # Проверка файлов конфигурации
    print("📋 Проверка файлов конфигурации:")
    all_ok &= check_file("render.yaml", "Render конфигурация")
    all_ok &= check_file("requirements.txt", "Python зависимости")
    all_ok &= check_file("runtime.txt", "Python версия")
    all_ok &= check_file("websocket_server.py", "WebSocket сервер")
    all_ok &= check_file(".gitignore", "Git ignore файл")
    print()
    
    # Проверка содержимого
    print("📝 Проверка содержимого конфигурации:")
    all_ok &= check_content("requirements.txt", "websockets", "websockets в requirements.txt")
    all_ok &= check_content("requirements.txt", "aiohttp", "aiohttp в requirements.txt")
    all_ok &= check_content("render.yaml", "websocket_server.py", "startCommand в render.yaml")
    all_ok &= check_content("websocket_server.py", "os.environ.get(\"PORT\"", "PORT из переменных окружения")
    all_ok &= check_content("websocket_server.py", "0.0.0.0", "Host 0.0.0.0")
    print()
    
    # Проверка Python пакетов
    print("📦 Проверка установленных пакетов (локально):")
    check_python_packages()
    print()
    
    # Финальный результат
    print("=" * 60)
    if all_ok:
        print("✅ Проект готов к развёртыванию на Render!")
        print()
        print("Следующие шаги:")
        print("1. Загрузите код на GitHub")
        print("2. На render.com создайте новый Web Service")
        print("3. Свяжите репозиторий GitHub")
        print("4. Используйте URL вашего сервера в клиенте")
        print()
        print(f"📌 URL сервера: https://neochat-server-b1jq.onrender.com")
        print(f"🔗 WebSocket URL: wss://neochat-server-b1jq.onrender.com")
        return 0
    else:
        print("⚠️  Найдены проблемы. Пожалуйста, исправьте их перед развёртыванием.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
