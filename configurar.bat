@echo off
REM ============================================================
REM  Abrir configuración de ASFI/SCIP Monitor
REM  Permite administrar reportes y credenciales SQLite
REM ============================================================

title ASFI/SCIP Monitor - Configuración
cd /d "%~dp0"

for %%V in (3.13 3.12 3.11 3.10) do (
    py -%%V -c "import tkinter, sqlite3" >nul 2>&1
    if not errorlevel 1 (
        py -%%V gestionar_reportes.py
        exit /b
    )
)

python -c "import tkinter, sqlite3" >nul 2>&1
if not errorlevel 1 (
    python gestionar_reportes.py
    exit /b
)

echo.
echo No se encontro un Python compatible con tkinter y sqlite3.
echo Ejecuta primero instalar.bat o instala Python con tkinter incluido.
pause
