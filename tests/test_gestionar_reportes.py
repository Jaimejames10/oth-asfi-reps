import unittest
from datetime import datetime

from gestionar_reportes import _overdue_days


class OverdueDaysTests(unittest.TestCase):
    def test_late_success_uses_send_date(self):
        row = {
            "estado": "EXITOSO_TARDIO",
            "fecha_hora_limite": "2026-09-01T12:00:00-04:00",
            "fecha_envio": "2/9/2026 08:48:42",
        }
        self.assertEqual(_overdue_days(row, datetime(2026, 9, 14, 10, 0)), "1")

    def test_missing_send_uses_current_date(self):
        row = {
            "estado": "FALTANTE",
            "fecha_hora_limite": "2026-09-01T12:00:00-04:00",
            "fecha_envio": None,
        }
        self.assertEqual(_overdue_days(row, datetime(2026, 9, 4, 10, 0)), "3")


if __name__ == "__main__":
    unittest.main()
