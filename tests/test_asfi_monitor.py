import io
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import reportes_db
from asfi_monitor import (
    _es_error_validacion,
    analizar_reporte_nuevo,
    filtrar_reportes_para_revision,
    reconciliar_reportes_subsanados,
)
from asfi_monitor_app.application import monitor_service


ROOT = Path(__file__).resolve().parents[1]


class AnalisisReporteTests(unittest.TestCase):
    def test_monthly_cutoff_does_not_reactivate_historical_daily_rows(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            reportes_db.initialize_database(db_path, ROOT / "reportes_seed.json")
            conn = reportes_db.connect(db_path)
            try:
                reportes = [
                    {
                        "grupo": "D007 IF - Diario Operaciones Interbancarias",
                        "fecha_corte": "31/8/2026",
                    },
                    {
                        "grupo": "MI01-MI09 - Mensual Central de Información de Riesgo Operativo",
                        "fecha_corte": "31/8/2026",
                    },
                    {
                        "grupo": "D007 IF - Diario Operaciones Interbancarias",
                        "fecha_corte": "13/9/2026",
                    },
                ]
                result = filtrar_reportes_para_revision(
                    conn,
                    reportes,
                    date(2026, 9, 13),
                    {"2026-08-31": {"mensual"}},
                )
                self.assertEqual(
                    [row["grupo"] for row in result],
                    [
                        "MI01-MI09 - Mensual Central de Información de Riesgo Operativo",
                        "D007 IF - Diario Operaciones Interbancarias",
                    ],
                )
            finally:
                conn.close()

    def test_error_values_in_validation_column(self):
        for value in ("Error", "Detalle Error", "  detalle   error  "):
            with self.subTest(value=value):
                estado, _detalle = analizar_reporte_nuevo(value, "Envío 1")
                self.assertEqual(estado, "ERROR")

    def test_non_error_validation_can_be_successful(self):
        estado, _detalle = analizar_reporte_nuevo("Solicitar Apertura", "Envío 1")
        self.assertEqual(estado, "EXITOSO")

    def test_empty_detail_error_link_is_not_an_error(self):
        self.assertFalse(_es_error_validacion(""))
        self.assertFalse(_es_error_validacion("Solicitar Apertura"))

    def test_error_with_successful_duplicate_is_reclassified(self):
        reportes = [
            {
                "grupo": "Reporte de Prueba",
                "estado": "ERROR",
                "envio": "Envío 1",
                "detalle": "Validación: Error",
            },
            {
                "grupo": "  reporte   de prueba ",
                "estado": "EXITOSO",
                "envio": "Envío 2",
                "detalle": "Envío 2",
            },
        ]

        reconciliar_reportes_subsanados(reportes)

        self.assertEqual(reportes[0]["estado"], "EXITOSO")
        self.assertIn("Subsanado", reportes[0]["detalle"])
        self.assertIn("Envío 2", reportes[0]["detalle"])

    def test_errors_without_successful_duplicate_remain_errors(self):
        reportes = [
            {
                "grupo": "Reporte de Prueba",
                "estado": "ERROR",
                "envio": "Envío 1",
                "detalle": "Error 1",
            },
            {
                "grupo": "Reporte de Prueba",
                "estado": "ERROR",
                "envio": "Envío 2",
                "detalle": "Error 2",
            },
        ]

        reconciliar_reportes_subsanados(reportes)

        self.assertEqual([reporte["estado"] for reporte in reportes], ["ERROR", "ERROR"])

    def test_successful_different_name_does_not_resolve_error(self):
        reportes = [
            {
                "grupo": "Reporte con Error",
                "estado": "ERROR",
                "envio": "Envío 1",
                "detalle": "Error",
            },
            {
                "grupo": "Otro Reporte",
                "estado": "EXITOSO",
                "envio": "Envío 1",
                "detalle": "Correcto",
            },
        ]

        reconciliar_reportes_subsanados(reportes)

        self.assertEqual(reportes[0]["estado"], "ERROR")

    def test_different_occurrences_are_not_cross_subsanated(self):
        reportes = [
            {
                "grupo": "Reporte de Prueba",
                "fecha_corte": "2/9/2026",
                "estado": "ERROR",
                "envio": "Envío 1",
                "detalle": "Error en el primer envío",
            },
            {
                "grupo": "Reporte de Prueba",
                "fecha_corte": "2/9/2026",
                "estado": "EXITOSO",
                "envio": "Envío 2",
                "detalle": "Envío 2",
            },
        ]

        reconciliar_reportes_subsanados(
            reportes,
            {("reporte de prueba", "2026-09-02"): 2},
        )

        self.assertEqual(reportes[0]["estado"], "ERROR")
        self.assertEqual(reportes[1]["estado"], "EXITOSO")

    def test_retry_of_same_occurrence_is_subsanated(self):
        reportes = [
            {
                "grupo": "Reporte de Prueba",
                "fecha_corte": "2/9/2026",
                "estado": "ERROR",
                "envio": "Envío 1",
                "detalle": "Error inicial",
            },
            {
                "grupo": "Reporte de Prueba",
                "fecha_corte": "2/9/2026",
                "estado": "EXITOSO",
                "envio": "Envío 1",
                "detalle": "Envío 1",
            },
        ]

        reconciliar_reportes_subsanados(
            reportes,
            {("reporte de prueba", "2026-09-02"): 2},
        )

        self.assertEqual(reportes[0]["estado"], "EXITOSO")
        self.assertIn("misma ocurrencia", reportes[0]["detalle"])


class MonitorNotificationTests(unittest.TestCase):
    def test_late_success_is_not_notified_but_remains_overdue(self):
        current = datetime(
            2026,
            9,
            3,
            13,
            tzinfo=timezone(timedelta(hours=-4), "America/La_Paz"),
        )

        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            reportes_db.initialize_database(db_path, ROOT / "reportes_seed.json")
            conn = reportes_db.connect(db_path)
            try:
                reportes_db.ensure_obligations(conn, current)
                row = {
                    "grupo": "Reporte de transacciones relativas a la compra de activos virtuales",
                    "fecha_corte": "31/8/2026",
                    "fecha_llegada": "2/9/2026 08:48:42",
                    "tipo_entidad": "TEST",
                    "sigla": "TEST",
                    "email": "",
                    "resultado_raw": "",
                    "validacion": "",
                    "envio": "Envío 1",
                    "estado": "EXITOSO",
                    "detalle": "",
                    "timestamp_revision": "2026-09-03T13:00:00",
                }
                run_id = reportes_db.start_scrape_run(
                    conn, date(2026, 8, 31), date(2026, 8, 31)
                )
                reportes_db.store_observations(conn, run_id, [row])
            finally:
                conn.close()

            with patch.dict(
                monitor_service.CONFIG,
                {
                    "_usar_credenciales_db": False,
                    "fecha_inicio_corte": None,
                    "fecha_fin_corte": None,
                },
                clear=False,
            ), patch.object(
                monitor_service, "inicializar_base_datos", return_value=db_path
            ), patch.object(
                monitor_service, "cargar_configuracion_desde_db"
            ), patch.object(
                monitor_service,
                "cargar_estado",
                return_value={
                    "alertas_enviadas": {
                        monitor_service.clave_reporte(row): {"estado": "ERROR"}
                    }
                },
            ), patch.object(
                monitor_service, "guardar_estado"
            ), patch.object(
                monitor_service, "obtener_reportes_por_rangos", return_value=[row]
            ), patch.object(
                monitor_service, "filtrar_reportes_para_revision", return_value=[row]
            ), patch.object(
                monitor_service.reportes_db, "local_now", return_value=current
            ), patch.object(monitor_service, "notificar") as notificar:
                monitor_service.ejecutar_revision()

            titles = [call.args[0] for call in notificar.call_args_list]
            self.assertFalse(any("TARDÍOS" in title for title in titles))
            self.assertFalse(any("resuelto" in title.casefold() for title in titles))

            conn = reportes_db.connect(db_path)
            try:
                late = [
                    item
                    for item in reportes_db.list_overdue_obligations(conn, current)
                    if item["codigo"] == "ACTIVOS_VIRTUALES"
                ]
                self.assertEqual(len(late), 1)
                self.assertEqual(late[0]["estado"], "EXITOSO_TARDIO")
            finally:
                conn.close()


class ConsoleSummaryTests(unittest.TestCase):
    def test_summary_groups_periods_in_readable_order(self):
        output = io.StringIO()
        report = {
            "grupo": "Reporte diario",
            "estado": "EXITOSO",
            "fecha_corte": "14/9/2026",
        }

        with redirect_stdout(output):
            monitor_service.imprimir_resumen_consola(
                current=datetime(2026, 9, 15, 8, 30),
                fecha_corte="14/9/2026",
                reportes_revision=[report],
                exitosos=[report],
                errores=[],
                pendientes=[],
                reportes_faltantes={
                    "diario": [],
                    "semanal": ["Reporte semanal"],
                    "mensual": [],
                },
                reportes_tardios=[],
                sin_enviar={"diario": [], "semanal": [], "mensual": []},
                periodos_por_reporte={"reporte diario": "diario"},
            )

        text = output.getvalue()
        self.assertLess(text.index("[ DIARIOS ]"), text.index("[ SEMANALES ]"))
        self.assertLess(text.index("[ SEMANALES ]"), text.index("[ MENSUALES ]"))
        self.assertIn("Estado: TODO CORRECTO", text)
        self.assertIn("✅ Reporte diario (- | corte 14/9/2026)", text)
        self.assertIn("⚠️ Reporte semanal (SEMANAL VENCIDO SIN ENVIAR)", text)


if __name__ == "__main__":
    unittest.main()
