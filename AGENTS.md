# AGENTS.md - Configuración de Agentes IA para ASFI Monitor

Este archivo contiene toda la información que necesitas para trabajar con el proyecto ASFI Monitor usando cualquier agente IA (GitHub Copilot, Claude, ChatGPT u Opencode).

**Usa este archivo cuando trabajes con cualquiera de los agentes** - contiene contexto completo, mejores prácticas y instrucciones específicas adaptadas a cada plataforma.

---

## 📋 Tabla de Contenidos
1. [Descripción General](#descripción-general)
2. [Stack Tecnológico](#stack-tecnológico)
3. [Estructura del Proyecto](#estructura-del-proyecto)
4. [Configuración](#configuración)
5. [Flujo de Ejecución](#flujo-de-ejecución)
6. [Instrucciones por Agente](#instrucciones-por-agente)
7. [Solución de Problemas](#solución-de-problemas)
8. [Mejores Prácticas](#mejores-prácticas)

---

## 📖 Descripción General

### ¿Qué es ASFI Monitor?
Monitor automatizado que controla el envío de reportes al sistema ASFI/SCIP (plataforma estatal boliviana).

**Función principal:**
- Se conecta automáticamente a ASFI/SCIP
- Extrae datos de reportes enviados
- Analiza si hay errores o fallos en los envíos
- Notifica al usuario mediante alertas visuales Windows (Toast)
- Mantiene un caché de estados para detectar cambios

**Usuarios objetivo:** Personal de TI / Operaciones que necesita monitoreo continuo de envíos críticos.

**Criticidad:** Media-Alta (errores en producción, requiere recuperación automática).

---

## 🔧 Stack Tecnológico

| Componente | Versión | Propósito |
|-----------|---------|----------|
| **Python** | 3.8+ | Lenguaje principal |
| **Playwright** | ≥1.40.0 | Web scraping y automatización |
| **Plyer** | ≥2.1.0 | Notificaciones del sistema Windows |
| **Schedule** | ≥1.2.0 | Planificación de tareas periódicas |

**Requisitos adicionales:**
- Navegador Chromium (instalado automáticamente por Playwright)
- Windows 10+ (para notificaciones Toast)
- Acceso a red (conexión a ASFI/SCIP)

---

## 📁 Estructura del Proyecto

```
Reports_ASFI_monitor/
├── asfi_monitor.py              # Script principal - lógica de monitoreo
├── debug_reportes.py            # Herramienta interactiva para debugging
├── probar_notificaciones.py      # Script para probar notificaciones
├── asfi_estado.json             # Caché de último estado conocido
├── reportes_debug.json          # Datos debuggueados de reportes
├── instalar.bat                 # Script de instalación de dependencias
├── programar_tarea.bat          # Configuración de tareas programadas Windows
├── AGENTS.md                    # Este archivo
└── tools/
    ├── ejecutar.bat             # Lanzador rápido del monitor
    ├── diagnostico.bat          # Script de diagnóstico del sistema
    └── asfi_monitor-old.py      # Versión anterior (backup)
```

### Archivos Principales

**asfi_monitor.py**
- Punto de entrada principal
- Contiene configuración (CONFIG dict)
- Lógica de autenticación con Playwright
- Scraping y análisis de reportes
- Generación de notificaciones
- Manejo de estado (JSON)

**debug_reportes.py**
- Herramienta interactiva para debugging
- Permite inspeccionar reportes sin notificaciones
- Útil para desarrollar/probar nuevos selectores CSS

**probar_notificaciones.py**
- Script simple para verificar que las notificaciones funcionan
- Ayuda a diagnosticar problemas con Plyer/Windows

**asfi_estado.json**
- Caché persistente del último estado
- Se compara en cada ejecución para detectar cambios
- Se actualiza automáticamente

---

## ⚙️ Configuración

La configuración se encuentra en `asfi_monitor.py` en la sección `CONFIG` (línea ~50):

```python
CONFIG = {
    "url_base": "https://appweb.asfi.gob.bo/SCIP",
    "usuario": os.environ.get("ASFI_USUARIO", "usuario-default"),
    "password": os.environ.get("ASFI_PASSWORD", "pass-default"),
    "dias_atras": 1,  # (Deprecado - siempre consulta "ayer")
    "intervalo_minutos": 15,  # Intervalo entre chequeos
    "palabras_exito": [  # Patrones que indican ÉXITO
        "proceso finalizado",
        "enviado exitosamente",
        # ... más patrones
    ],
    "palabras_error": [  # Patrones que indican ERROR
        "error",
        "rechazo",
        # ... más patrones
    ],
}
```

### Configuración de Credenciales

**Opción 1: Variables de entorno (RECOMENDADO)**
```powershell
# En PowerShell
$env:ASFI_USUARIO = "tu_usuario"
$env:ASFI_PASSWORD = "tu_contraseña"
python asfi_monitor.py
```

**Opción 2: Variables de entorno permanentes**
```powershell
[Environment]::SetEnvironmentVariable("ASFI_USUARIO", "tu_usuario", "User")
[Environment]::SetEnvironmentVariable("ASFI_PASSWORD", "tu_contraseña", "User")
```

⚠️ **SEGURIDAD**: Nunca hardcodees credenciales en el código fuente.

---

## 🔄 Flujo de Ejecución

```
┌─────────────────────────────────────────┐
│ 1. Inicializar                          │
│    - Leer CONFIG                        │
│    - Configurar logging                 │
│    - Crear sesión Playwright            │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│ 2. Autenticar en ASFI                   │
│    - Ir a URL base                      │
│    - Completar login                    │
│    - Esperar carga de página            │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│ 3. Navegar a reportes                   │
│    - Ir a sección de reportes           │
│    - Seleccionar fecha "ayer"           │
│    - Esperar carga de tabla             │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│ 4. Extraer datos                        │
│    - Scraping de filas de reportes      │
│    - Parsear estados, referencias, etc. │
│    - Guardar en estructura de datos     │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│ 5. Comparar con último estado           │
│    - Leer asfi_estado.json              │
│    - Detectar cambios/nuevos errores    │
│    - Analizar palabras clave            │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│ 6. Notificar si hay cambios             │
│    - Generar mensaje alerta             │
│    - Enviar Toast notification          │
│    - Registrar en logs                  │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│ 7. Guardar estado                       │
│    - Actualizar asfi_estado.json        │
│    - Registrar timestamp                │
└──────────────┬──────────────────────────┘
               │
        ¿Modo continuo?
        /            \
      Sí              No
      │               │
      │          FIN (salir)
      │
┌──────────────▼──────────────────────────┐
│ 8. Esperar intervalo                    │
│    - Sleep(intervalo_minutos)           │
│    - Volver a paso 2                    │
└─────────────────────────────────────────┘
```

---

## 🚀 Uso Común

### Monitoreo continuo (cada 15 minutos por defecto)
```bash
python asfi_monitor.py
```

### Monitoreo con intervalo personalizado
```bash
python asfi_monitor.py --intervalo 10
```

### Ejecución única (sin loop)
```bash
python asfi_monitor.py --una-vez
```

### Con UI visible (útil para debugging)
```bash
python asfi_monitor.py --visible
```

### Debugging interactivo
```bash
python debug_reportes.py
```

### Probar notificaciones
```bash
python probar_notificaciones.py
```

---

## 📋 Instrucciones por Agente

### GitHub Copilot

**Mejor para:** Autocompletado en tiempo real, quick fixes, refactorización inline, generación de tests.

**Instrucciones específicas:**

1. **Completado de código**
   - Los archivos mantienen convenciones claras de naming
   - Type hints están presentes en funciones críticas
   - Logging usa `logging.info()`, no `print()`

2. **Debugging inline**
   - Para problemas de Playwright: busca timeouts en las llamadas a `page.wait_for_selector()`
   - Para problemas de notificaciones: verifica que Plyer esté bien importado y Windows esté soportado
   - Para credenciales: siempre refiere a variables de entorno

3. **Quick fixes a priorizar**
   - Manejo de excepciones específicas (no bare `except:`)
   - Type hints en parámetros de función
   - Docstrings en funciones públicas

4. **Refactorización**
   - Mantén la sección CONFIG intacta para que el usuario pueda editarla fácilmente
   - Los loops infinitos con `schedule` deben seguir el patrón actual
   - Preserva compatibilidad con argumentos CLI (`--intervalo`, `--una-vez`, `--visible`)

5. **Restricciones**
   - ❌ No hardcodees credenciales
   - ❌ No expongas secretos en logs
   - ❌ No cambies el formato de `asfi_estado.json` sin migración
   - ✅ Compatible con Windows (paths con `\`, notificaciones Toast)
   - ✅ Mantén retro-compatibilidad con CLI existente

### Claude (Anthropic)

**Mejor para:** Análisis profundo, refactorización arquitectónica, resolución de problemas complejos, documentación técnica.

**Instrucciones específicas:**

1. **Contexto del proyecto**
   - **Objetivo principal**: Monitorear envíos a ASFI/SCIP sin intervención manual
   - **Usuarios**: Personal de TI / Operaciones
   - **Criticidad**: Media-Alta (notificaciones de errores en producción)
   - **Tolerancia de fallos**: Bajo (debe recuperarse automáticamente)

2. **Análisis de código**
   - Identifica anti-patrones: reintentos mal implementados, memory leaks en loops
   - Sugiere mejoras arquitectónicas: separar lógica de scraping, notificaciones, cacheo
   - Documenta decisiones de diseño: por qué se usa JSON vs base de datos, por qué Playwright vs Selenium

3. **Problemas frecuentes que abordar**
   - Timeouts en conexión a ASFI por servidor lento
   - Cambios no documentados en estructura HTML de ASFI
   - Sesiones expiradas de Playwright durante monitoreo continuo
   - Falsos positivos en detección de errores por palabras clave ambiguas

4. **Formato de respuesta esperado**
   - Análisis estructurado con causas raíz
   - Código de ejemplo cuando sea relevante
   - Documentación de decisiones tomadas
   - Rutas alternativas consideradas con trade-offs

5. **Preguntas que Claude debería hacerse**
   - ¿Cuál es la causa raíz, no solo el síntoma?
   - ¿Hay patrones subyacentes en los errores recurrentes?
   - ¿Existe una solución más elegante o escalable?
   - ¿Cómo se podría mejorar la observabilidad del sistema?

6. **Restricciones**
   - ❌ No simplificar excesivamente sin considerar edge cases
   - ❌ No introducir dependencias externas sin justificación
   - ❌ No cambiar flujo de ejecución sin compatibilidad hacia atrás
   - ✅ Considera performance en loops infinitos
   - ✅ Documenta cualquier cambio de API o comportamiento

### ChatGPT (OpenAI)

**Mejor para:** Soluciones rápidas y prácticas, generación de código, testing, explicaciones de librerías.

**Instrucciones específicas:**

1. **Rol esperado**
   - Asistente de desarrollo práctico para implementación rápida de features y debugging
   - Enfoque en soluciones funcionales como punto de partida
   - Proporcionar ejemplos de uso inmediatos

2. **Comportamiento esperado**
   - Respuestas directas y concisas
   - Código funcional y ejecutable
   - Ejemplos prácticos antes de teoría
   - Warnings sobre edge cases y limitaciones

3. **Tareas típicas**
   - "¿Cómo hacer un retry con backoff exponencial?"
   - "Refactoriza esta función para ser más legible"
   - "¿Qué librería uso para parsear HTML mejor?"
   - "¿Cómo testear una función que hace web scraping?"
   - "¿Cuál es la mejor forma de manejar timeouts?"

4. **Contexto mínimo requerido para entender consultas**
   - Versión de Python (3.8+)
   - Librerías principales: Playwright, Plyer, Schedule
   - Estructura de archivos JSON y config
   - Formato de credenciales (variables de entorno)

5. **Consideraciones específicas**
   - No sugerir soluciones sin considerar Windows (SO objetivo)
   - Mantener compatibilidad con versiones actuales de librerías
   - Evitar dependencias externas no necesarias
   - Considerar que el código corre en loops infinitos (memory leaks)

6. **Restricciones**
   - ❌ No hardcodear credenciales en ejemplos
   - ❌ No usar imports no documentados
   - ❌ No ignorar exceptions genéricas
   - ✅ Código ready-to-run
   - ✅ Tipo hints cuando sea relevante

### Opencode

**Mejor para:** Exploración visual de código, mapeo de dependencias, análisis de flujo de ejecución, identificar código muerto.

**Instrucciones específicas:**

1. **Propósito en este proyecto**
   - Exploración visual de la base de código y estructura
   - Mapeo de dependencias entre módulos
   - Visualización del flujo de ejecución
   - Identificación de cuellos de botella y código no utilizado

2. **Navegación recomendada**
   ```
   asfi_monitor.py (punto de entrada)
     ├── Lectura de configuración
     ├── Autenticación (Playwright)
     ├── Scraping de reportes
     ├── Análisis de resultados
     └── Notificaciones (Plyer)
   
   asfi_estado.json (datos persistentes)
     └── Seguimiento de cambios entre ejecuciones
   ```

3. **Vistas útiles a generar**
   - Mapa de dependencias entre módulos
   - Flujo de ejecución paso a paso (diagrama)
   - Puntos de entrada (argumentos CLI: `--intervalo`, `--una-vez`, `--visible`)
   - Estado compartido (archivo JSON y variables globales)
   - Llamadas a funciones externas (Playwright, Plyer)

4. **Preguntas visuales a responder**
   - "¿Dónde se usa la variable X?"
   - "¿Cuál es el flujo de ejecución exacto?"
   - "¿Qué funciones llaman a Y?"
   - "¿Hay código muerto o no utilizado?"
   - "¿Cuál es la ruta crítica?"

5. **Integración con análisis**
   - Exportar mapas de dependencias
   - Generar diagramas de flujo
   - Identificar posibles cuellos de botella
   - Visualizar cambios de estado (JSON) entre ciclos

6. **Restricciones**
   - ❌ No modificar código desde visualizaciones
   - ❌ Mantener referencias de archivo y línea exactas
   - ✅ Proporcionar salida visual clara
   - ✅ Exportar en formato standard (SVG, PNG, DOT)

---

## � Solución de Problemas

### Timeout en conexión a ASFI
**Causa**: Servidor lento o no disponible  
**Síntomas**: Playwright espera indefinidamente en `page.wait_for_selector()`  
**Solución**: 
- Aumentar timeout en Playwright context: `context = await browser.new_context(timeout=30000)`
- Verificar VPN y conexión a internet
- Revisar estado del servidor ASFI en horario distinto

### Credenciales inválidas
**Causa**: Variables de entorno no configuradas  
**Síntomas**: Login falla con mensaje de credenciales incorrectas  
**Solución**: 
```powershell
$env:ASFI_USUARIO = "tu_usuario"
$env:ASFI_PASSWORD = "tu_contraseña"
python asfi_monitor.py
```

### Cambios en estructura HTML
**Causa**: ASFI actualizó su interfaz sin notificación  
**Síntomas**: Selectores CSS no encuentran elementos  
**Solución**: 
- Abrir el navegador con `python asfi_monitor.py --visible`
- Usar herramientas de desarrollador (F12)
- Actualizar selectores CSS en `asfi_monitor.py`
- Documentar cambios

### Falsos positivos en notificaciones
**Causa**: Palabras clave muy genéricas en CONFIG  
**Síntomas**: Se notifica de errores que no son reales  
**Solución**: 
- Revisar lista `palabras_exito` y `palabras_error` en CONFIG
- Hacer más específicas las palabras clave
- Usar regex más precisas si es necesario

### Notificaciones no aparecen
**Causa**: Plyer no configurado o Windows no soportado  
**Síntomas**: Script corre pero no hay notificaciones  
**Solución**: 
- Ejecutar `python probar_notificaciones.py`
- Verificar Windows versión 10+
- Reinstalar Plyer: `pip install --force-reinstall plyer`

### Memory leak en monitoreo continuo
**Causa**: Sesión de Playwright no se limpia correctamente  
**Síntomas**: Consumo de RAM aumenta progresivamente  
**Solución**: 
- Agregar cierre de contexto: `await context.close()`
- Monitorear con `tools/diagnostico.bat`
- Reducir `intervalo_minutos` si es muy bajo

---

## 📚 Mejores Prácticas

### Convenciones de Código
1. **Logging**: Siempre usar `logging.info()`, `logging.error()` - nunca `print()`
2. **Errores**: Capturar excepciones específicas, no usar bare `except:`
3. **Type Hints**: Incluir en funciones públicas
4. **Docstrings**: Documentar funciones no triviales

### Manejo de Credenciales
- ✅ Usar variables de entorno (`ASFI_USUARIO`, `ASFI_PASSWORD`)
- ✅ Nunca commitear credenciales en git
- ✅ No loguear contraseñas
- ❌ No hardcodear credenciales en código

### Manejo de Estado (JSON)
- ✅ Validar estructura JSON antes de procesar
- ✅ Mantener timestamps de última actualización
- ✅ Hacer backup antes de cambios importantes
- ❌ No mezclar datos sensibles con estado público

### Performance en Loops Infinitos
- ✅ Monitorear uso de memoria regularmente
- ✅ Limpiar recursos explícitamente (`close()`)
- ✅ Usar context managers (`with` statements)
- ❌ No acumular datos en listas sin límite

### Testing
- Mantener archivos de test separados
- Usar `debug_reportes.py` para pruebas interactivas
- Documentar casos de prueba
- Incluir datos de ejemplo en repo

### Documentación
- Mantener AGENTS.md actualizado
- Documentar cambios en estructura HTML de ASFI
- Mantener comentarios en secciones complejas
- Incluir ejemplos de uso en docstrings

---

## 🔐 Consideraciones de Seguridad

- ✅ Todas las credenciales via variables de entorno
- ✅ No incluir ASFI_USUARIO ni ASFI_PASSWORD en el código
- ✅ No exponer tokens o sesiones en logs
- ✅ Validar entrada de usuario en CLI
- ✅ Usar HTTPS para todas las conexiones
- ✅ Revisar permisos de archivos (especialmente `asfi_estado.json`)

---

## 📞 Contacto y Soporte

Para problemas específicos del proyecto:
1. Ejecutar `python debug_reportes.py` para debugging interactivo
2. Revisar logs en la consola (configurados en `asfi_monitor.py`)
3. Verificar variables de entorno:
   ```powershell
   python -c "import os; print(os.environ.get('ASFI_USUARIO'))"
   ```
4. Ejecutar diagnóstico: `tools/diagnostico.bat`

---

**Última actualización**: 2026-09-01  
**Versión del documento**: 2.0  
**Compatible con**: ASFI Monitor v1.x  
**Plataformas soportadas**: GitHub Copilot, Claude, ChatGPT, Opencode
