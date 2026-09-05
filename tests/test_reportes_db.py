import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import reportes_db


ROOT = Path(__file__).resolve().parents[1]
D008 = "D008 IF - Diario Tipo de Cambio"
D007 = "D007 IF - Diario Operaciones Interbancarias"


def scraped_row(name, cutoff, status="EXITOSO", envio="Envío 1", arrival=None):
    return {
        "grupo": name,
        "fecha_corte": cutoff,
        "fecha_llegada": arrival or cutoff,
        "tipo_entidad": "TEST",
        "sigla": "TEST",
        "email": "",
        "resultado_raw": "",
        "validacion": "",
        "envio": envio,
        "estado": status,
        "detalle": "",
        "timestamp_revision": "2026-09-04T10:00:00",
    }


class ReportesDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        reportes_db.initialize_database(self.db_path, ROOT / "reportes_seed.json")
        self.conn = reportes_db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_seed_and_d008_rules(self):
        catalog = reportes_db.get_catalog(self.conn)
        self.assertEqual(len(catalog), 17)
        d008 = next(item for item in catalog if item["codigo"] == "D008")
        self.assertEqual(
            sorted((rule["dia_semana_corte"], rule["ocurrencias_requeridas"])
                   for rule in d008["reglas"]),
            [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (7, 1)],
        )
        preamypes = next(item for item in catalog if item["codigo"] == "PREAMYPES_SEMANAL")
        self.assertEqual(preamypes["tipo_periodo"], "semanal")
        self.assertEqual(preamypes["reglas"][0]["dias_envio"], [7])
        monthly = [item for item in catalog if item["tipo_periodo"] == "mensual"]
        self.assertEqual(len(monthly), 4)
        self.assertTrue(all(not item["validacion_activa"] for item in monthly))

    def test_d008_requires_two_successful_occurrences(self):
        current = datetime(2026, 9, 3, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row_one = scraped_row(D008, "2/9/2026", envio="Envío 1", arrival="3/9/2026")
        run_id = reportes_db.start_scrape_run(self.conn, date(2026, 9, 2), date(2026, 9, 2))
        reportes_db.store_observations(self.conn, run_id, [row_one])

        after_deadline = datetime(2026, 9, 4, 0, 1)
        result = reportes_db.evaluate_obligations(self.conn, [row_one], after_deadline)
        self.assertEqual(result["diario"].count(D008), 1)

        row_two = scraped_row(D008, "2/9/2026", envio="Envío 2", arrival="3/9/2026")
        run_id = reportes_db.start_scrape_run(self.conn, date(2026, 9, 2), date(2026, 9, 2))
        reportes_db.store_observations(self.conn, run_id, [row_one, row_two])
        result = reportes_db.evaluate_obligations(self.conn, [row_one, row_two], after_deadline)
        self.assertNotIn(D008, result["diario"])
        unresolved = self.conn.execute(
            "SELECT COUNT(*) FROM incumplimientos WHERE nombre_snapshot = ? AND resuelto_en IS NULL",
            (D008,),
        ).fetchone()[0]
        self.assertEqual(unresolved, 0)

    def test_evaluation_ignores_historical_missing_obligations(self):
        before = datetime(2026, 9, 3, 13, 0)
        reportes_db.ensure_obligations(self.conn, before)
        historical = reportes_db.evaluate_obligations(self.conn, [], before)
        self.assertIn(D007, historical["diario"])

        current = datetime(2026, 9, 4, 10, 0)
        current_row = scraped_row(D007, "3/9/2026", arrival="3/9/2026")
        result = reportes_db.evaluate_obligations(self.conn, [current_row], current)

        self.assertNotIn(D007, result["diario"])

    def test_weekly_cutoff_and_preamypes_sunday_window(self):
        sunday_before = datetime(2026, 9, 6, 11, 59)
        reportes_db.ensure_obligations(self.conn, sunday_before)
        rows = self.conn.execute(
            """
            SELECT p.codigo, o.fecha_corte, o.fecha_inicio_envio, o.fecha_hora_limite
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.tipo_periodo = 'semanal' AND o.fecha_corte = '2026-09-04'
            ORDER BY p.codigo
            """
        ).fetchall()
        preamypes = next(row for row in rows if row["codigo"] == "PREAMYPES_SEMANAL")
        self.assertEqual(preamypes["fecha_corte"], "2026-09-04")
        self.assertEqual(preamypes["fecha_inicio_envio"], "2026-09-06")
        self.assertIn("2026-09-06T12:00", preamypes["fecha_hora_limite"])

        result_before = reportes_db.evaluate_obligations(self.conn, [], sunday_before)
        self.assertNotIn("Solicitud de Créditos - PREAMyPes - Semanal", result_before["semanal"])
        result_after = reportes_db.evaluate_obligations(self.conn, [], datetime(2026, 9, 6, 12, 1))
        self.assertIn("Solicitud de Créditos - PREAMyPes - Semanal", result_after["semanal"])

    def test_dismiss_overdue_obligations(self):
        current = datetime(2026, 9, 3, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        after = datetime(2026, 9, 3, 13, 0)
        reportes_db.evaluate_obligations(self.conn, [], after)
        overdue = reportes_db.list_overdue_obligations(self.conn, after)
        self.assertTrue(overdue)
        target = overdue[0]
        self.assertTrue(reportes_db.dismiss_obligation(self.conn, target["id"], after))
        remaining = reportes_db.list_overdue_obligations(self.conn, after)
        self.assertNotIn(target["id"], [row["id"] for row in remaining])
        estado = self.conn.execute(
            "SELECT estado FROM obligaciones WHERE id = ?", (target["id"],)
        ).fetchone()[0]
        self.assertEqual(estado, "DESCARTADO")
        unresolved = self.conn.execute(
            "SELECT COUNT(*) FROM incumplimientos WHERE obligacion_id = ? AND resuelto_en IS NULL",
            (target["id"],),
        ).fetchone()[0]
        self.assertEqual(unresolved, 0)
        reportes_db.evaluate_obligations(self.conn, [], after)
        estado = self.conn.execute(
            "SELECT estado FROM obligaciones WHERE id = ?", (target["id"],)
        ).fetchone()[0]
        self.assertEqual(estado, "DESCARTADO")
        cleared = reportes_db.dismiss_overdue_obligations(self.conn, after)
        self.assertEqual(cleared, len(remaining))
        self.assertEqual(reportes_db.list_overdue_obligations(self.conn, after), [])

    def test_query_date_range_ends_yesterday(self):
        viernes = datetime(2026, 9, 4, 10, 0)
        reportes_db.ensure_obligations(self.conn, viernes)
        start, end = reportes_db.get_query_date_range(self.conn, viernes)
        self.assertEqual(start, date(2026, 9, 3))
        self.assertEqual(end, date(2026, 9, 3))

    def test_same_day_send_counts_for_daily_obligation(self):
        current = datetime(2026, 9, 4, 13, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row("D007 IF - Diario Operaciones Interbancarias", "3/9/2026", arrival="3/9/2026")
        result = reportes_db.evaluate_obligations(self.conn, [row], current)
        self.assertNotIn("D007 IF - Diario Operaciones Interbancarias", result["diario"])

    def test_credentials_are_retrievable_without_plaintext_storage(self):
        reportes_db.save_credentials(self.conn, "usuario-prueba", "password-prueba")
        self.assertEqual(
            reportes_db.get_credentials(self.conn),
            ("usuario-prueba", "password-prueba"),
        )
        stored = self.conn.execute(
            "SELECT password_protegida FROM credenciales WHERE id = 1"
        ).fetchone()[0]
        self.assertNotEqual(stored, "password-prueba")
        self.assertNotIn("password-prueba", stored)

    def test_monthly_catalog_is_not_validated_until_configured(self):
        obligations = reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 3, 10, 0))
        self.assertFalse(any(item["tipo_periodo"] == "mensual" for item in obligations))

    def test_current_and_pending_obligation_views(self):
        reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 3, 10, 0))
        reportes_db.evaluate_obligations(self.conn, [], datetime(2026, 9, 3, 13, 0))
        reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 4, 10, 0))

        current = reportes_db.list_current_obligations(self.conn)
        daily = [row for row in current if row["tipo_periodo"] == "diario"]
        self.assertTrue(daily)
        self.assertTrue(all(row["fecha_corte"] in ("2026-09-02", "2026-09-03") for row in daily))
        d008 = [row for row in current if row["codigo"] == "D008"]
        self.assertEqual(len(d008), 4)
        self.assertEqual(len([row for row in d008 if row["fecha_corte"] == "2026-09-02"]), 2)
        self.assertEqual(len([row for row in d008 if row["fecha_corte"] == "2026-09-03"]), 2)

        overdue = reportes_db.list_overdue_obligations(self.conn, datetime(2026, 9, 4, 10, 0))
        self.assertTrue(overdue)
        self.assertTrue(all(row["fecha_corte"] < "2026-09-04" for row in overdue))
        self.assertTrue(all(row["estado"] != "EXITOSO" for row in overdue))
        self.assertTrue(all(row["fecha_limite"] <= "2026-09-04" for row in overdue))
        self.assertTrue(all(row["hora_limite"] <= "12:00" for row in overdue))
        self.assertTrue(any(row["estado"] == "FALTANTE" for row in overdue))
        # La obligación diaria del 3/9 vence a las 12:00 del 4/9: a las 10:00 aún no está vencida
        self.assertTrue(all(row["fecha_corte"] != "2026-09-03" for row in overdue))

        today = reportes_db.list_today_obligations(self.conn, datetime(2026, 9, 4, 10, 0))
        daily_today = [row for row in today if row["tipo_periodo"] == "diario"]
        self.assertTrue(daily_today)
        self.assertTrue(all(row["fecha_corte"] == "2026-09-03" for row in daily_today))

    def test_catalog_crud_and_semiannual_period(self):
        report_id = reportes_db.save_report(
            self.conn,
            None,
            "A001",
            "Reporte semestral de prueba",
            "semestral",
            True,
            True,
            "",
            [{
                "regla_fecha_corte": "FIN_PERIODO",
                "frecuencia_meses": 6,
                "mes_ancla": 1,
                "dias_envio": [],
                "hora_limite": "10:00",
                "dias_plazo": 2,
                "tipo_plazo": "calendario",
                "ocurrencias_requeridas": 1,
            }],
            [],
        )
        report = next(item for item in reportes_db.get_catalog(self.conn) if item["id"] == report_id)
        self.assertEqual(report["codigo"], "A001")
        self.assertEqual(len(report["reglas"]), 1)

        rule = dict(report["reglas"][0])
        rule.update({"codigo": "A001", "nombre": report["nombre"], "tipo_periodo": "semestral"})
        obligation = reportes_db.calculate_obligation(rule, date(2026, 9, 3))
        self.assertEqual(obligation["fecha_corte"], "2026-06-30")
        self.assertIn("2026-07-02T10:00", obligation["fecha_hora_limite"])

        reportes_db.save_report(
            self.conn,
            report_id,
            "A001",
            "Reporte semestral modificado",
            "semestral",
            True,
            True,
            "actualizado",
            report["reglas"],
            [],
        )
        updated = next(item for item in reportes_db.get_catalog(self.conn) if item["id"] == report_id)
        self.assertEqual(updated["nombre"], "Reporte semestral modificado")


if __name__ == "__main__":
    unittest.main()
