import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import reportes_db
from asfi_monitor import (
    _es_error_validacion,
    analizar_reporte_nuevo,
    filtrar_reportes_para_revision,
    reconciliar_reportes_subsanados,
)


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


if __name__ == "__main__":
    unittest.main()
