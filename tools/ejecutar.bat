@echo off
REM ============================================================
REM  Ejecutar ASFI Monitor con el Python correcto (3.13)
REM ============================================================

REM Detectar automaticamente el Python que tiene los paquetes instalados
for %%V in (3.13 3.12 3.11 3.10) do (
    py -%%V -c "import schedule, playwright, plyer" >nul 2>&1
    if not errorlevel 1 (
        echo Usando Python %%V
        py -%%V asfi_monitor.py %*
        exit /b
    )
)

echo.
echo No se encontro un Python con todos los modulos instalados.
echo Ejecuta primero: py -3.13 -m pip install schedule playwright plyer
echo.
pause
