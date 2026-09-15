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
        self.assertEqual(len(catalog), 33)
        d008 = next(item for item in catalog if item["codigo"] == "D008")
        self.assertEqual(
            sorted((rule["dia_semana_corte"], rule["ocurrencias_requeridas"])
                   for rule in d008["reglas"]),
            [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (7, 1)],
        )
        d001 = next(item for item in catalog if item["codigo"] == "D001_D005")
        self.assertTrue(all(rule["excluir_ultimo_dia_mes"] for rule in d001["reglas"]))
        preamypes = next(item for item in catalog if item["codigo"] == "PREAMYPES_SEMANAL")
        self.assertEqual(preamypes["tipo_periodo"], "semanal")
        self.assertEqual(preamypes["reglas"][0]["regla_fecha_corte"], "SEMANAL")
        self.assertEqual(preamypes["reglas"][0]["dia_semana_corte"], 7)
        self.assertEqual(preamypes["reglas"][0]["dias_envio"], [])
        monthly = [item for item in catalog if item["tipo_periodo"] == "mensual"]
        self.assertEqual(len(monthly), 20)
        self.assertTrue(all(item["validacion_activa"] for item in monthly))
        m019 = next(item for item in monthly if item["codigo"] == "M019")
        self.assertEqual(
            (m019["reglas"][0]["dias_plazo"], m019["reglas"][0]["tipo_plazo"], m019["reglas"][0]["hora_limite"]),
            (1, "habil", "23:59"),
        )
        self.assertEqual(
            {item["codigo"] for item in monthly if item["codigo"].startswith(("MI", "MB"))},
            {"MI01_MI09", "MB01_MB20"},
        )
        self.assertIn("2026-05-01", {row["fecha"] for row in reportes_db.list_holidays(self.conn)})

    def test_legacy_weekly_rules_are_migrated_without_reusing_old_rule(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        legacy_conn = reportes_db.connect(legacy_path)
        legacy_conn.executescript(reportes_db.SCHEMA_SQL)
        timestamp = reportes_db.now_iso()
        legacy_conn.execute(
            """
            INSERT INTO reportes
              (codigo, nombre, tipo_periodo, activo, validacion_activa,
               descripcion, creado_en, actualizado_en)
            VALUES (?, ?, 'semanal', 1, 1, '', ?, ?)
            """,
            ("PREAMYPES_SEMANAL", "PREAMyPes semanal", timestamp, timestamp),
        )
        report_id = legacy_conn.execute("SELECT id FROM reportes").fetchone()[0]
        legacy_conn.execute(
            """
            INSERT INTO reglas_reportes
              (reporte_id, regla_fecha_corte, dia_semana_corte, dias_envio,
               ocurrencias_requeridas, hora_limite, activo, creado_en, actualizado_en)
            VALUES (?, 'VIERNES', 5, '[7]', 1, '12:00', 1, ?, ?)
            """,
            (report_id, timestamp, timestamp),
        )
        legacy_conn.execute("PRAGMA user_version = 2")
        legacy_conn.commit()
        legacy_conn.close()

        reportes_db.initialize_database(legacy_path)
        migrated = reportes_db.connect(legacy_path)
        rules = migrated.execute(
            """
            SELECT regla_fecha_corte, dia_semana_corte, dias_envio, hora_limite, activo
            FROM reglas_reportes
            ORDER BY id
            """
        ).fetchall()
        self.assertEqual(rules[0]["activo"], 0)
        self.assertEqual(
            tuple(rules[1][key] for key in ("regla_fecha_corte", "dia_semana_corte", "dias_envio", "hora_limite")),
            ("SEMANAL", 7, "[]", "12:00"),
        )
        self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 7)
        migrated.close()

    def test_d008_requires_two_successful_occurrences(self):
        current = datetime(2026, 9, 3, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row_one = scraped_row(D008, "2/9/2026", envio="Envío 1", arrival="3/9/2026")
        counts = reportes_db.get_required_occurrence_counts(self.conn, [row_one])
        self.assertEqual(counts[(reportes_db.normalize_name(D008), "2026-09-02")], 2)

        first_run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 9, 2), date(2026, 9, 2)
        )
        reportes_db.store_observations(self.conn, first_run_id, [row_one])

        after_deadline = datetime(2026, 9, 4, 0, 1)
        result = reportes_db.evaluate_obligations(self.conn, [row_one], after_deadline)
        self.assertEqual(result["diario"].count(D008), 1)
        statuses = self.conn.execute(
            """
            SELECT o.ocurrencia, o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'D008' AND o.fecha_corte = '2026-09-02'
            ORDER BY o.ocurrencia
            """
        ).fetchall()
        self.assertEqual(
            [(row["ocurrencia"], row["estado"]) for row in statuses],
            [(1, "EXITOSO"), (2, "FALTANTE")],
        )

        row_two = scraped_row(D008, "2/9/2026", envio="Envío 2", arrival="3/9/2026")
        second_run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 9, 2), date(2026, 9, 2)
        )
        reportes_db.store_observations(self.conn, second_run_id, [row_one, row_two])
        result = reportes_db.evaluate_obligations(self.conn, [row_one, row_two], after_deadline)
        self.assertNotIn(D008, result["diario"])
        statuses = self.conn.execute(
            """
            SELECT o.ocurrencia, o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'D008' AND o.fecha_corte = '2026-09-02'
            ORDER BY o.ocurrencia
            """
        ).fetchall()
        self.assertEqual(
            [(row["ocurrencia"], row["estado"]) for row in statuses],
            [(1, "EXITOSO"), (2, "EXITOSO")],
        )
        associations = self.conn.execute(
            """
            SELECT o.ocurrencia, obs.envio
            FROM obligacion_observacion oo
            JOIN obligaciones o ON o.id = oo.obligacion_id
            JOIN observaciones_reportes obs ON obs.id = oo.observacion_id
            WHERE obs.ejecucion_id IN (?, ?)
            ORDER BY obs.id
            """,
            (first_run_id, second_run_id),
        ).fetchall()
        self.assertEqual(
            [(row["ocurrencia"], row["envio"]) for row in associations],
            [(1, "Envío 1"), (1, "Envío 1"), (2, "Envío 2")],
        )
        unresolved = self.conn.execute(
            "SELECT COUNT(*) FROM incumplimientos WHERE nombre_snapshot = ? AND resuelto_en IS NULL",
            (D008,),
        ).fetchone()[0]
        self.assertEqual(unresolved, 0)

    def test_d008_without_submissions_marks_both_occurrences_missing(self):
        current = datetime(2026, 9, 3, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)

        result = reportes_db.evaluate_obligations(
            self.conn, [], datetime(2026, 9, 3, 13, 0)
        )
        self.assertEqual(result["diario"].count(D008), 2)
        statuses = self.conn.execute(
            """
            SELECT o.ocurrencia, o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'D008' AND o.fecha_corte = '2026-09-02'
            ORDER BY o.ocurrencia
            """
        ).fetchall()
        self.assertEqual(
            [(row["ocurrencia"], row["estado"]) for row in statuses],
            [(1, "FALTANTE"), (2, "FALTANTE")],
        )

    def test_configured_occurrences_scale_to_three(self):
        reportes_db.save_report(
            self.conn,
            None,
            "T003",
            "Reporte de tres envíos",
            "diario",
            True,
            True,
            "",
            [{
                "regla_fecha_corte": "AYER",
                "dia_semana_corte": 3,
                "dias_envio": [4],
                "hora_limite": "12:00",
                "ocurrencias_requeridas": 3,
            }],
            [],
        )

        reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 3, 10, 0))
        rows = self.conn.execute(
            """
            SELECT o.ocurrencia
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'T003' AND o.fecha_corte = '2026-09-02'
            ORDER BY o.ocurrencia
            """
        ).fetchall()
        self.assertEqual([row["ocurrencia"] for row in rows], [1, 2, 3])

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
            WHERE p.tipo_periodo = 'semanal'
            ORDER BY p.codigo
            """
        ).fetchall()
        preamypes = next(row for row in rows if row["codigo"] == "PREAMYPES_SEMANAL")
        self.assertEqual(preamypes["fecha_corte"], "2026-09-06")
        self.assertEqual(preamypes["fecha_inicio_envio"], "2026-09-07")
        self.assertIn("2026-09-07T12:00", preamypes["fecha_hora_limite"])
        cartera = next(row for row in rows if row["codigo"] == "CS_CARTERA")
        self.assertEqual(cartera["fecha_corte"], "2026-09-04")
        self.assertEqual(cartera["fecha_inicio_envio"], "2026-09-05")
        self.assertIn("2026-09-07T12:00", cartera["fecha_hora_limite"])

        result_before = reportes_db.evaluate_obligations(self.conn, [], sunday_before)
        self.assertNotIn("Solicitud de Créditos - PREAMyPes - Semanal", result_before["semanal"])
        result_after = reportes_db.evaluate_obligations(self.conn, [], datetime(2026, 9, 7, 12, 1))
        self.assertIn("Solicitud de Créditos - PREAMyPes - Semanal", result_after["semanal"])
        overdue = reportes_db.list_overdue_obligations(self.conn, datetime(2026, 9, 7, 12, 1))
        preamypes_overdue = next(
            row for row in overdue if row["codigo"] == "PREAMYPES_SEMANAL"
        )
        self.assertEqual(preamypes_overdue["estado"], "VENCIDO_SIN_ENVIAR")

    def test_weekly_success_from_cutoff_history_survives_later_query(self):
        current = datetime(2026, 9, 5, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row(
            "CS - Cartera Semanal",
            "4/9/2026",
            arrival="5/9/2026 09:00:00",
        )
        run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 9, 4), date(2026, 9, 4)
        )
        reportes_db.store_observations(self.conn, run_id, [row])
        reportes_db.evaluate_obligations(self.conn, [row], current)

        reportes_db.evaluate_obligations(self.conn, [], datetime(2026, 9, 7, 12, 1))
        state = self.conn.execute(
            """
            SELECT o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'CS_CARTERA' AND o.fecha_corte = '2026-09-04'
            """
        ).fetchone()[0]
        self.assertEqual(state, "EXITOSO")
        self.assertFalse(
            any(
                row["codigo"] == "CS_CARTERA"
                for row in reportes_db.list_overdue_obligations(
                    self.conn, datetime(2026, 9, 7, 12, 1)
                )
            )
        )

    def test_weekly_late_success_is_kept_separate_from_overdue_missing(self):
        current = datetime(2026, 9, 5, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row(
            "CS - Cartera Semanal",
            "4/9/2026",
            arrival="7/9/2026 12:30:00",
        )
        run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 9, 4), date(2026, 9, 4)
        )
        reportes_db.store_observations(self.conn, run_id, [row])
        after_deadline = datetime(2026, 9, 7, 13, 0)
        reportes_db.evaluate_obligations(self.conn, [], after_deadline)

        overdue = reportes_db.list_overdue_obligations(self.conn, after_deadline)
        late = next(item for item in overdue if item["codigo"] == "CS_CARTERA")
        self.assertEqual(late["estado"], "EXITOSO_TARDIO")
        self.assertEqual(late["fecha_envio"], "7/9/2026 12:30:00")

    def test_weekly_error_becomes_overdue_after_monday_deadline(self):
        current = datetime(2026, 9, 5, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row(
            "CS - Cartera Semanal",
            "4/9/2026",
            status="ERROR",
            envio="Envío 1",
            arrival="5/9/2026 09:00:00",
        )
        run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 9, 4), date(2026, 9, 4)
        )
        reportes_db.store_observations(self.conn, run_id, [row])
        reportes_db.evaluate_obligations(self.conn, [row], current)
        self.assertEqual(
            self.conn.execute(
                """
                SELECT o.estado
                FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
                WHERE p.codigo = 'CS_CARTERA' AND o.fecha_corte = '2026-09-04'
                """
            ).fetchone()[0],
            "ERROR",
        )

        reportes_db.evaluate_obligations(self.conn, [], datetime(2026, 9, 7, 12, 0))
        self.assertEqual(
            self.conn.execute(
                """
                SELECT o.estado
                FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
                WHERE p.codigo = 'CS_CARTERA' AND o.fecha_corte = '2026-09-04'
                """
            ).fetchone()[0],
            "ERROR",
        )

        reportes_db.evaluate_obligations(self.conn, [], datetime(2026, 9, 7, 12, 1))
        state = self.conn.execute(
            """
            SELECT o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'CS_CARTERA' AND o.fecha_corte = '2026-09-04'
            """
        ).fetchone()[0]
        self.assertEqual(state, "VENCIDO_SIN_ENVIAR")
        failure = self.conn.execute(
            """
            SELECT i.estado, i.resuelto_en
            FROM incumplimientos i JOIN obligaciones o ON o.id = i.obligacion_id
            JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'CS_CARTERA' AND o.fecha_corte = '2026-09-04'
            """
        ).fetchone()
        self.assertEqual(failure["estado"], "VENCIDO_SIN_ENVIAR")
        self.assertIsNone(failure["resuelto_en"])

    def test_weekly_cutoff_day_is_configurable_for_future_reports(self):
        report_id = reportes_db.save_report(
            self.conn,
            None,
            "SAB001",
            "Reporte semanal con corte sábado",
            "semanal",
            True,
            True,
            "",
            [{
                "regla_fecha_corte": "SEMANAL",
                "dia_semana_corte": 6,
                "dias_envio": [6, 7],
                "hora_limite": "09:00",
                "dias_plazo": 4,
                "tipo_plazo": "habil",
                "ocurrencias_requeridas": 1,
            }],
            [],
        )
        reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 6, 10, 0))
        row = self.conn.execute(
            """
            SELECT o.fecha_corte, o.fecha_inicio_envio, o.fecha_hora_limite
            FROM obligaciones o
            WHERE o.reporte_id = ?
            """,
            (report_id,),
        ).fetchone()
        self.assertEqual(row["fecha_corte"], "2026-09-05")
        self.assertEqual(row["fecha_inicio_envio"], "2026-09-06")
        self.assertIn("2026-09-07T12:00", row["fecha_hora_limite"])

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

    def test_query_date_range_includes_recent_weekly_cutoffs_and_ends_yesterday(self):
        viernes = datetime(2026, 9, 4, 10, 0)
        reportes_db.ensure_obligations(self.conn, viernes)
        start, end = reportes_db.get_query_date_range(self.conn, viernes)
        self.assertEqual(start, date(2026, 8, 31))
        self.assertEqual(end, date(2026, 9, 3))

    def test_query_date_range_expands_during_weekly_and_monthly_windows(self):
        saturday = datetime(2026, 9, 5, 10, 0)
        start, end = reportes_db.get_query_date_range(self.conn, saturday)
        self.assertEqual((start, end), (date(2026, 8, 31), date(2026, 9, 4)))

        thursday = datetime(2026, 9, 10, 10, 0)
        start, end = reportes_db.get_query_date_range(self.conn, thursday)
        self.assertEqual((start, end), (date(2026, 8, 31), date(2026, 9, 9)))

        after_monthly_deadlines = datetime(2026, 9, 16, 10, 0)
        start, end = reportes_db.get_query_date_range(self.conn, after_monthly_deadlines)
        self.assertEqual((start, end), (date(2026, 9, 15), date(2026, 9, 15)))

    def test_query_date_ranges_keep_monthly_cutoff_separate(self):
        ranges = reportes_db.get_query_date_ranges(
            self.conn, datetime(2026, 9, 14, 8, 0)
        )
        self.assertEqual(
            ranges,
            [
                (date(2026, 8, 31), date(2026, 8, 31)),
                (date(2026, 9, 11), date(2026, 9, 13)),
            ],
        )

    def test_manual_query_date_range_is_respected(self):
        start, end = reportes_db.get_query_date_range(
            self.conn,
            datetime(2026, 9, 10, 10, 0),
            configured_start="01/09/2026",
            configured_end="03/09/2026",
        )
        self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 3)))

    def test_app_settings_are_persisted_and_can_be_removed(self):
        reportes_db.set_app_setting(self.conn, "monitor_intervalo_minutos", "30")
        self.assertEqual(
            reportes_db.get_app_setting(self.conn, "monitor_intervalo_minutos"), "30"
        )
        reportes_db.set_app_setting(self.conn, "monitor_intervalo_minutos", None)
        self.assertIsNone(reportes_db.get_app_setting(self.conn, "monitor_intervalo_minutos"))

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

    def test_monthly_catalog_creates_active_obligations(self):
        obligations = reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 3, 10, 0))
        monthly = [item for item in obligations if item["tipo_periodo"] == "mensual"]
        self.assertEqual(len(monthly), 20)
        self.assertTrue(all(item["fecha_corte"] == "2026-08-31" for item in monthly))
        self.assertTrue(all(item["estado"] == "ABIERTO" for item in monthly))

    def test_business_deadline_skips_bolivian_holiday(self):
        holidays = {date(2026, 5, 1)}
        self.assertEqual(
            reportes_db._add_business_days(date(2026, 4, 30), 1, holidays),
            date(2026, 5, 4),
        )

    def test_holiday_can_be_added_and_removed(self):
        reportes_db.set_holiday(self.conn, "15/09/2026", "Feriado de prueba")
        self.assertIn(date(2026, 9, 15), reportes_db.get_holidays(self.conn))
        self.assertTrue(reportes_db.delete_holiday(self.conn, "15/09/2026"))
        self.assertNotIn(date(2026, 9, 15), reportes_db.get_holidays(self.conn))

    def test_monthly_deadlines_use_business_and_calendar_days(self):
        reportes_db.ensure_obligations(self.conn, datetime(2026, 9, 3, 10, 0))
        rows = self.conn.execute(
            """
            SELECT p.codigo, o.fecha_corte, o.fecha_hora_limite
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo IN ('M019', 'CREDITO_PCD', 'M030')
              AND o.periodo_clave = '2026-08'
            ORDER BY p.codigo
            """
        ).fetchall()
        deadlines = {row["codigo"]: row["fecha_hora_limite"] for row in rows}
        self.assertIn("2026-09-01T23:59", deadlines["M019"])
        self.assertIn("2026-09-14T12:00", deadlines["CREDITO_PCD"])
        self.assertIn("2026-09-15T23:59", deadlines["M030"])

    def test_monthly_success_survives_after_query_window(self):
        current = datetime(2026, 9, 3, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row(
            "M019 IF - Mensual Tasas Pasivas",
            "31/8/2026",
            arrival="1/9/2026 10:00",
        )
        run_id = reportes_db.start_scrape_run(self.conn, date(2026, 8, 31), date(2026, 9, 2))
        reportes_db.store_observations(self.conn, run_id, [row])

        result = reportes_db.evaluate_obligations(
            self.conn, [], datetime(2026, 9, 16, 10, 0)
        )
        self.assertNotIn("M019 IF - Mensual Tasas Pasivas", result["mensual"])
        status = self.conn.execute(
            """
            SELECT o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'M019' AND o.periodo_clave = '2026-08'
            """
        ).fetchone()[0]
        self.assertEqual(status, "EXITOSO")

    def test_monthly_success_after_deadline_is_marked_late(self):
        current = datetime(2026, 9, 3, 13, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row(
            "Reporte de transacciones relativas a la compra de activos virtuales",
            "31/8/2026",
            arrival="2/9/2026 08:48:42",
        )
        run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 8, 31), date(2026, 8, 31)
        )
        reportes_db.store_observations(self.conn, run_id, [row])

        result = reportes_db.evaluate_obligations(self.conn, [row], current)
        nombre = "Reporte de transacciones relativas a la compra de activos virtuales"
        self.assertNotIn(nombre, result["mensual"])
        self.assertIn(nombre, result["tardios"]["mensual"])
        status = self.conn.execute(
            """
            SELECT o.estado
            FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'ACTIVOS_VIRTUALES' AND o.periodo_clave = '2026-08'
            """
        ).fetchone()[0]
        self.assertEqual(status, "EXITOSO_TARDIO")
        failure = self.conn.execute(
            """
            SELECT i.estado, i.resuelto_en
            FROM incumplimientos i JOIN obligaciones o ON o.id = i.obligacion_id
            JOIN reportes p ON p.id = o.reporte_id
            WHERE p.codigo = 'ACTIVOS_VIRTUALES' AND o.periodo_clave = '2026-08'
            """
        ).fetchone()
        self.assertEqual(failure["estado"], "EXITOSO_TARDIO")
        self.assertIsNone(failure["resuelto_en"])

    def test_daily_rule_can_exclude_last_day_of_month(self):
        report_id = reportes_db.save_report(
            self.conn,
            None,
            "DTEST",
            "Reporte diario con excepción de fin de mes",
            "diario",
            True,
            True,
            "",
            [{
                "regla_fecha_corte": "AYER",
                "hora_limite": "12:00",
                "excluir_ultimo_dia_mes": True,
            }],
            [],
        )
        report = next(item for item in reportes_db.get_catalog(self.conn) if item["id"] == report_id)
        rule = dict(report["reglas"][0])
        rule.update({
            "codigo": report["codigo"],
            "nombre": report["nombre"],
            "tipo_periodo": report["tipo_periodo"],
        })

        self.assertIsNone(
            reportes_db.calculate_obligation(rule, date(2026, 9, 30))
        )
        self.assertIsNotNone(
            reportes_db.calculate_obligation(rule, date(2026, 10, 1))
        )
        self.assertTrue(report["reglas"][0]["excluir_ultimo_dia_mes"])

    def test_version_three_database_migrates_monthly_catalog(self):
        path = Path(self.temp_dir.name) / "monthly-migration.db"
        conn = reportes_db.connect(path)
        conn.executescript(reportes_db.SCHEMA_SQL)
        timestamp = reportes_db.now_iso()
        cursor = conn.execute(
            """
            INSERT INTO reportes
              (codigo, nombre, tipo_periodo, activo, validacion_activa,
               descripcion, creado_en, actualizado_en)
            VALUES ('M019', 'M019 antiguo', 'mensual', 1, 0, '', ?, ?)
            """,
            (timestamp, timestamp),
        )
        report_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO reglas_reportes
              (reporte_id, regla_fecha_corte, frecuencia_meses, dias_envio,
               ocurrencias_requeridas, activo, creado_en, actualizado_en)
            VALUES (?, 'FIN_MES', 1, '[]', 1, 1, ?, ?)
            """,
            (report_id, timestamp, timestamp),
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()

        reportes_db.initialize_database(path, ROOT / "reportes_seed.json")
        migrated = reportes_db.connect(path)
        monthly_count = migrated.execute(
            "SELECT COUNT(*) FROM reportes WHERE tipo_periodo = 'mensual'"
        ).fetchone()[0]
        active_rule = migrated.execute(
            """
            SELECT rr.dias_plazo, rr.tipo_plazo, rr.hora_limite
            FROM reglas_reportes rr JOIN reportes p ON p.id = rr.reporte_id
            WHERE p.codigo = 'M019' AND rr.activo = 1
            """
        ).fetchone()
        self.assertEqual(monthly_count, 20)
        self.assertEqual(
            (active_rule["dias_plazo"], active_rule["tipo_plazo"], active_rule["hora_limite"]),
            (1, "habil", "23:59"),
        )
        self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 7)
        migrated.close()

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
        daily_overdue = [row for row in overdue if row["tipo_periodo"] == "diario"]
        self.assertTrue(all(row["fecha_corte"] < "2026-09-04" for row in daily_overdue))
        self.assertTrue(all(row["estado"] != "EXITOSO" for row in daily_overdue))
        self.assertTrue(all(row["fecha_limite"] <= "2026-09-04" for row in daily_overdue))
        self.assertTrue(all(row["hora_limite"] <= "12:00" for row in daily_overdue))
        self.assertTrue(any(row["estado"] == "FALTANTE" for row in daily_overdue))
        self.assertTrue(any(row["tipo_periodo"] == "mensual" for row in overdue))
        # La obligación diaria del 3/9 vence a las 12:00 del 4/9: a las 10:00 aún no está vencida
        self.assertTrue(all(row["fecha_corte"] != "2026-09-03" for row in overdue))

        today = reportes_db.list_today_obligations(self.conn, datetime(2026, 9, 4, 10, 0))
        daily_today = [row for row in today if row["tipo_periodo"] == "diario"]
        self.assertTrue(daily_today)
        self.assertTrue(all(row["fecha_corte"] == "2026-09-03" for row in daily_today))

    def test_today_obligations_include_latest_send_date(self):
        current = datetime(2026, 9, 4, 10, 0)
        reportes_db.ensure_obligations(self.conn, current)
        row = scraped_row(
            D007,
            "3/9/2026",
            arrival="3/9/2026 09:30:00",
        )
        run_id = reportes_db.start_scrape_run(
            self.conn, date(2026, 9, 3), date(2026, 9, 3)
        )
        reportes_db.store_observations(self.conn, run_id, [row])
        reportes_db.evaluate_obligations(self.conn, [row], current)

        today = reportes_db.list_today_obligations(self.conn, current)
        sent = next(item for item in today if item["codigo"] == "D007")
        self.assertEqual(sent["fecha_envio"], "3/9/2026 09:30:00")

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
