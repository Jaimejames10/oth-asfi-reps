@echo off
echo ============================================
echo  Diagnostico de Python - ASFI Monitor
echo ============================================
echo.

echo [1] Python que usa esta terminal:
where python
echo.

echo [2] Version:
python --version
echo.

echo [3] Ubicacion del ejecutable:
python -c "import sys; print(sys.executable)"
echo.

echo [4] Modulos instalados (schedule, playwright, plyer):
python -c "import schedule; print('  schedule OK:', schedule.__version__)" 2>nul || echo   schedule: NO INSTALADO
python -c "import playwright; print('  playwright OK')" 2>nul || echo   playwright: NO INSTALADO
python -c "import plyer; print('  plyer OK')" 2>nul || echo   plyer: NO INSTALADO
echo.

echo [5] Instalando modulos en el Python correcto:
python -m pip install schedule playwright plyer --upgrade
echo.

echo [6] Verificando de nuevo:
python -c "import schedule; import playwright; import plyer; print('TODO OK - ya puedes usar el monitor')" || echo "Aun hay errores, ver mensajes arriba"
echo.
pause
