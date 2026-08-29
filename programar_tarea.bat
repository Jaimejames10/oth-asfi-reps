@echo off
REM ============================================================
REM  Programa el Monitor ASFI para ejecutarse automáticamente
REM  al iniciar sesión en Windows (Task Scheduler)
REM  Ejecutar como Administrador
REM ============================================================

echo.
echo  Programando ASFI Monitor en el Programador de Tareas...
echo.

set "SCRIPT_DIR=%~dp0"
set "TAREA_NOMBRE=ASFI_SCIP_Monitor"

REM Eliminar tarea anterior si existe
schtasks /delete /tn "%TAREA_NOMBRE%" /f >nul 2>&1

REM Crear nueva tarea: ejecutar al iniciar sesión + cada 15 minutos
schtasks /create ^
    /tn "%TAREA_NOMBRE%" ^
    /tr "python \"%SCRIPT_DIR%asfi_monitor.py\"" ^
    /sc ONLOGON ^
    /delay 0002:00 ^
    /ru "%USERNAME%" ^
    /f

if errorlevel 1 (
    echo [ERROR] No se pudo crear la tarea programada.
    echo         Asegurarse de ejecutar como Administrador.
    pause
    exit /b 1
)

echo [OK] Tarea "%TAREA_NOMBRE%" creada exitosamente.
echo.
echo La tarea se ejecutará automáticamente al iniciar sesión en Windows.
echo El monitor revisará los reportes cada 15 minutos (configurable en asfi_monitor.py).
echo.
echo Para administrar la tarea: Programador de Tareas ^> Biblioteca ^> %TAREA_NOMBRE%
echo Para eliminar la tarea: schtasks /delete /tn "%TAREA_NOMBRE%" /f
echo.
pause
