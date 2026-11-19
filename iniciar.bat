@echo off
chcp 65001 > nul
title Automação de Certidões

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    echo Iniciando com o ambiente virtual .venv...
    ".venv\Scripts\python.exe" main.py
) else (
    echo Iniciando com o Python do sistema...
    python main.py
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo Ocorreu um erro ao executar a automacao.
    pause
)

