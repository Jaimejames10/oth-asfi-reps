@echo off
REM ============================================================
REM  Iniciar ASFI Monitor
REM  Ejecuta asfi_monitor.py con el Python que tenga las
REM  dependencias instaladas (funciona desde cualquier ruta).
REM ============================================================
title ASFI Monitor

REM Cambiar al directorio donde esta este .bat (raiz del proyecto)
cd /d "%~dp0"

REM Detectar automaticamente el Python con los modulos instalados
for %%V in (3.13 3.12 3.11 3.10) do (
    py -%%V -c "import schedule, playwright, plyer" >nul 2>&1
    if not errorlevel 1 (
        echo Usando Python %%V
        echo Iniciando ASFI Monitor... (Ctrl+C para detener)
        echo.
        py -%%V asfi_monitor.py %*
        echo.
        echo Monitor finalizado.
        pause
        exit /b
    )
)

echo.
echo No se encontro un Python con todos los modulos instalados.
echo Ejecuta primero: instalar.bat
echo o bien:          py -3.13 -m pip install schedule playwright plyer
echo.
pause
