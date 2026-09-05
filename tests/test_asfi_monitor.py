import unittest

from asfi_monitor import _es_error_validacion, analizar_reporte_nuevo


class AnalisisReporteTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
