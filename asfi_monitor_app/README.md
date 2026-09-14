# ASFI Monitor App

El paquete contiene la implementacion principal del monitor. Los archivos
Python de la raiz se mantienen como fachadas para no romper comandos, tareas
programadas ni imports existentes.

## Capas

- `application`: casos de uso, revision completa, CLI y scheduler.
- `domain`: analisis de estados y reconciliacion de reintentos.
- `integrations`: cliente Playwright SCIP y notificaciones Windows.
- `storage`: SQLite, esquema, migraciones, credenciales y estado JSON.
- `ui`: dashboard Tkinter, dialogos y formateadores.
- `tools`: herramientas manuales de depuracion y diagnostico.

## Compatibilidad

Los puntos de entrada publicos siguen siendo:

```powershell
python asfi_monitor.py
python gestionar_reportes.py
python debug_reportes.py
python probar_notificaciones.py
```

La base de datos, el estado JSON, el catalogo semilla y los lanzadores `.bat`
conservan sus rutas actuales.
