@echo off
REM ============================================================
REM  Instalador del Monitor ASFI/SCIP
REM  Ejecutar como Administrador la primera vez
REM ============================================================

echo.
echo  ============================================
echo   Instalador ASFI Monitor
echo  ============================================
echo.

REM Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no está instalado o no está en el PATH.
    echo         Descargar desde https://www.python.org/downloads/
    echo         Asegurarse de marcar "Add Python to PATH" durante instalación.
    pause
    exit /b 1
)

echo [OK] Python encontrado:
python --version

echo.
echo [1/4] Instalando dependencias pip...
pip install playwright plyer schedule --upgrade
if errorlevel 1 (
    echo [ERROR] Fallo instalando dependencias pip.
    pause
    exit /b 1
)

echo.
echo [2/4] Instalando navegador Chromium para Playwright...
playwright install chromium
if errorlevel 1 (
    echo [ADVERTENCIA] Fallo instalando Chromium via playwright.
    echo              Intentando con python -m playwright...
    python -m playwright install chromium
)

echo.
echo [3/4] Verificando instalación...
python -c "from playwright.sync_api import sync_playwright; from plyer import notification; import schedule; print('[OK] Todas las dependencias instaladas correctamente')"
if errorlevel 1 (
    echo [ERROR] Verificación fallida. Revisar errores anteriores.
    pause
    exit /b 1
)

echo.
echo [4/4] Creando acceso directo en el Escritorio...
set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=%DESKTOP%\ASFI Monitor.lnk"

REM Crear el acceso directo via PowerShell
powershell -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%SHORTCUT%'); " ^
    "$s.TargetPath = 'python'; " ^
    "$s.Arguments = '\"%SCRIPT_DIR%asfi_monitor.py\"'; " ^
    "$s.WorkingDirectory = '%SCRIPT_DIR%'; " ^
    "$s.WindowStyle = 1; " ^
    "$s.Description = 'Monitor de reportes ASFI/SCIP'; " ^
    "$s.Save()"

echo.
echo  ============================================
echo   Instalación completada
echo  ============================================
echo.
echo  PRÓXIMOS PASOS:
echo.
echo  1. Ejecutar configurar.bat para registrar:
echo       - Usuario y contraseña ASFI/SCIP (se guardan en SQLite protegido)
echo       - Reportes, periodicidad, ocurrencias y reglas de calendario
echo.
echo  2. Editar asfi_monitor.py solo para opciones técnicas:
echo       - "intervalo_minutos": cada cuántos minutos revisar (ej: 15)
echo.
echo  3. Ejecutar el monitor:
echo       python asfi_monitor.py
echo.
echo  Opciones de ejecución:
echo    python asfi_monitor.py                         (monitoreo continuo, cada 15 min)
echo    python asfi_monitor.py --intervalo 10          (revisar cada 10 min)
echo    python asfi_monitor.py --una-vez               (revisar ahora y salir)
echo    python asfi_monitor.py --visible               (mostrar navegador, para depuración)
echo    python asfi_monitor.py --usuario X --password Y (pasar credenciales por argumento)
echo.
echo  Alternativa segura con variables de entorno:
echo    set ASFI_USUARIO=mi_usuario
echo    set ASFI_PASSWORD=mi_contraseña
echo    python asfi_monitor.py
echo.
pause
