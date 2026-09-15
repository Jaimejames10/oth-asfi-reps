"""Esquema SQLite versionado del monitor."""

SCHEMA_VERSION = 7

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feriados (
    fecha TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    tipo_periodo TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1,
    validacion_activa INTEGER NOT NULL DEFAULT 1,
    descripcion TEXT NOT NULL DEFAULT '',
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reglas_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id INTEGER NOT NULL REFERENCES reportes(id),
    regla_fecha_corte TEXT NOT NULL,
    dia_semana_corte INTEGER,
    dia_mes_corte INTEGER,
    mes_ancla INTEGER,
    frecuencia_meses INTEGER NOT NULL DEFAULT 1,
    dias_envio TEXT NOT NULL DEFAULT '[]',
    ocurrencias_requeridas INTEGER NOT NULL DEFAULT 1,
    hora_limite TEXT,
    dias_plazo INTEGER,
    tipo_plazo TEXT,
    excluir_ultimo_dia_mes INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alias_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id INTEGER NOT NULL REFERENCES reportes(id),
    alias TEXT NOT NULL,
    alias_normalizado TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS obligaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id INTEGER NOT NULL REFERENCES reportes(id),
    regla_id INTEGER NOT NULL REFERENCES reglas_reportes(id),
    periodo_clave TEXT NOT NULL,
    fecha_corte TEXT NOT NULL,
    fecha_inicio_envio TEXT,
    fecha_hora_limite TEXT,
    ocurrencia INTEGER NOT NULL DEFAULT 1,
    estado TEXT NOT NULL DEFAULT 'ABIERTO',
    ultima_revision TEXT,
    creada_en TEXT NOT NULL,
    actualizada_en TEXT NOT NULL,
    UNIQUE(regla_id, periodo_clave, ocurrencia)
);

CREATE TABLE IF NOT EXISTS ejecuciones_scraping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_en TEXT NOT NULL,
    finalizado_en TEXT,
    fecha_inicio_consulta TEXT NOT NULL,
    fecha_fin_consulta TEXT NOT NULL,
    cantidad_resultados INTEGER NOT NULL DEFAULT 0,
    resultado TEXT NOT NULL,
    detalle_error TEXT
);

CREATE TABLE IF NOT EXISTS observaciones_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id INTEGER NOT NULL REFERENCES ejecuciones_scraping(id),
    reporte_id INTEGER REFERENCES reportes(id),
    nombre_raw TEXT NOT NULL,
    nombre_normalizado TEXT NOT NULL,
    fecha_corte_raw TEXT,
    fecha_corte TEXT,
    fecha_llegada TEXT,
    tipo_entidad TEXT,
    sigla TEXT,
    email TEXT,
    resultado_raw TEXT,
    validacion TEXT,
    envio TEXT,
    estado TEXT NOT NULL,
    detalle TEXT,
    observado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obligacion_observacion (
    obligacion_id INTEGER NOT NULL REFERENCES obligaciones(id),
    observacion_id INTEGER NOT NULL REFERENCES observaciones_reportes(id),
    PRIMARY KEY(obligacion_id, observacion_id)
);

CREATE TABLE IF NOT EXISTS incumplimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligacion_id INTEGER REFERENCES obligaciones(id),
    reporte_id INTEGER REFERENCES reportes(id),
    nombre_snapshot TEXT NOT NULL,
    tipo_periodo TEXT NOT NULL,
    fecha_corte TEXT NOT NULL,
    ocurrencia INTEGER NOT NULL DEFAULT 1,
    detectado_en TEXT NOT NULL,
    resuelto_en TEXT,
    estado TEXT NOT NULL DEFAULT 'FALTANTE'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_incumplimiento_obligacion
    ON incumplimientos(obligacion_id)
    WHERE obligacion_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS eventos_notificacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligacion_id INTEGER REFERENCES obligaciones(id),
    tipo_evento TEXT NOT NULL,
    huella TEXT NOT NULL UNIQUE,
    enviado_en TEXT NOT NULL,
    resuelto_en TEXT,
    detalle TEXT
);

CREATE TABLE IF NOT EXISTS credenciales (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    usuario TEXT NOT NULL,
    password_protegida TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_obligaciones_fecha
    ON obligaciones(fecha_corte, estado);
CREATE INDEX IF NOT EXISTS ix_observaciones_fecha
    ON observaciones_reportes(fecha_corte, reporte_id);
CREATE INDEX IF NOT EXISTS ix_alias_normalizado
    ON alias_reportes(alias_normalizado);
"""
