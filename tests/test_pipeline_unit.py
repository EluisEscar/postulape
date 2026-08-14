import tempfile
import unittest
from pathlib import Path
from unittest import mock

from postulape import config
from postulape import cli
from postulape.services import matcher
from postulape.status import es_estado_terminal, es_resultado_completo
from postulape.storage import db, excel_store, sheets_store


class LLMConError:
    def chat_json(self, *args, **kwargs):
        raise RuntimeError("cuota agotada")


class LLMMalformado:
    def chat_json(self, *args, **kwargs):
        return ["esto no es un objeto"]


class HojaFalsa:
    def __init__(self, valores):
        self.valores = [list(fila) for fila in valores]

    def get_all_values(self):
        return [list(fila) for fila in self.valores]

    def col_values(self, columna):
        indice = columna - 1
        return [fila[indice] for fila in self.valores if len(fila) > indice]

    def append_rows(self, filas, value_input_option=None):
        self.valores.extend([list(fila) for fila in filas])

    def update(self, valores, rango, value_input_option=None):
        numero = int(rango.split(":", 1)[0][1:])
        self.valores[numero - 1] = list(valores[0])


class ConsultaFalsa:
    def __init__(self, datos):
        self.data = datos

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        return self


class ClienteFalso:
    def __init__(self, datos):
        self.consulta = ConsultaFalsa(datos)

    def table(self, *args, **kwargs):
        return self.consulta


def aviso(estado="Pendiente"):
    return {
        "id": "job-1",
        "plataforma": "TEST",
        "titulo": "Analista",
        "empresa": "Empresa",
        "distrito": "Lima",
        "departamento": "Lima",
        "modalidad": "Remoto",
        "antiguedad": "hace 1 día",
        "descripcion": "Descripción",
        "link": "https://example.test/job-1",
        "estado": estado,
    }


class EstadosTest(unittest.TestCase):
    def test_solo_las_decisiones_finales_son_terminales(self):
        self.assertTrue(es_estado_terminal("APLICA (80)"))
        self.assertTrue(es_estado_terminal("No aplica (20): brecha"))
        self.assertTrue(es_estado_terminal("Descartado (título)"))
        self.assertFalse(es_estado_terminal("Pendiente"))
        self.assertFalse(es_estado_terminal("Pendiente de reintento (error LLM)"))

    def test_match_positivo_sin_cv_aun_no_esta_completo(self):
        self.assertFalse(es_resultado_completo("APLICA (85)", ""))
        self.assertTrue(es_resultado_completo("APLICA (85)", "CV.docx"))
        self.assertTrue(es_resultado_completo("No aplica (20): brecha", ""))


class MatcherTest(unittest.TestCase):
    def test_error_de_clasificacion_no_descarta_el_lote(self):
        ofertas = [aviso(), {**aviso(), "id": "job-2"}]
        with mock.patch.object(config, "TAM_LOTE_TITULOS", 40):
            resultado = matcher.clasificar_titulos_en_lote(
                ofertas, LLMConError(), "software")
        self.assertEqual(resultado, {"job-1", "job-2"})

    def test_error_de_veredicto_queda_reintentable(self):
        oferta = aviso()
        matcher.evaluar(oferta, "CV", LLMConError())
        self.assertFalse(oferta["aplica"])
        self.assertFalse(es_estado_terminal(oferta["estado"]))

    def test_json_con_tipo_incorrecto_queda_reintentable(self):
        oferta = aviso()
        matcher.evaluar(oferta, "CV", LLMMalformado())
        self.assertFalse(es_estado_terminal(oferta["estado"]))
        self.assertIn("objeto JSON", oferta["motivo"])


class SheetsStoreTest(unittest.TestCase):
    def setUp(self):
        pendiente = [
            "job-1", "TEST", "Analista", "Empresa", "Lima", "Lima",
            "Remoto", "hace 1 día", "Descripción", "https://example.test/job-1",
            "Pendiente", "No", "",
        ]
        finalizada = pendiente.copy()
        finalizada[0] = "job-2"
        finalizada[10] = "No aplica (20): brecha"
        aplica_sin_cv = pendiente.copy()
        aplica_sin_cv[0] = "job-3"
        aplica_sin_cv[10] = "APLICA (85)"
        aplica_sin_cv[11] = "Sí"
        self.hoja = HojaFalsa([
            sheets_store.ENCABEZADOS, pendiente, finalizada, aplica_sin_cv])

    def test_pendientes_no_se_consideran_procesados(self):
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=self.hoja):
            self.assertEqual(sheets_store.cargar_ids_procesados(), {"job-2"})

    def test_actualiza_fila_pendiente_sin_duplicarla(self):
        oferta = aviso("APLICA (85)")
        oferta.update({"aplica": True, "cv_generado": "CV.docx"})
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=self.hoja):
            resultado = sheets_store.agregar_avisos(
                [oferta], actualizar_existentes=True)
        self.assertEqual(resultado["actualizados"], 1)
        self.assertEqual(len(self.hoja.valores), 4)
        self.assertEqual(self.hoja.valores[1][10], "APLICA (85)")
        self.assertEqual(self.hoja.valores[1][12], "CV.docx")


class ExcelStoreTest(unittest.TestCase):
    def test_pendiente_se_puede_actualizar(self):
        with tempfile.TemporaryDirectory() as directorio:
            ruta = str(Path(directorio) / "resultados.xlsx")
            excel_store.agregar_avisos([aviso()], ruta)
            self.assertEqual(excel_store.cargar_ids_procesados(ruta), set())
            excel_store.agregar_avisos(
                [aviso("No aplica (30): brecha")], ruta,
                actualizar_existentes=True)
            self.assertEqual(excel_store.cargar_ids_procesados(ruta), {"job-1"})


class SupabaseStoreTest(unittest.TestCase):
    def test_errores_no_bloquean_reintentos_por_usuario(self):
        datos = [
            {"job_id": "job-1", "estado": "APLICA (85)"},
            {"job_id": "job-2", "estado": "Pendiente de reintento (error LLM)"},
        ]
        with mock.patch.object(db, "cliente", return_value=ClienteFalso(datos)):
            self.assertEqual(db.ids_evaluados("usuario-1"), {"job-1"})


class UbicacionTest(unittest.TestCase):
    def test_scraper_recibe_ubicacion_de_la_persona(self):
        with mock.patch.object(config, "PLATAFORMAS_ACTIVAS", ["bumeran"]), \
                mock.patch.object(cli.scr_bumeran, "scrapear_bumeran", return_value=[]) as scraper:
            cli.scrapear_todo(["analista"], paginas=1, ubicacion="cusco")
        self.assertEqual(scraper.call_args.args[1], "cusco")
        self.assertEqual(scraper.call_args.kwargs["max_paginas"], 1)

    def test_filtro_admite_ubicacion_con_distrito_y_departamento(self):
        oferta = aviso()
        resultado = cli.aplicar_filtros([oferta], departamentos=["Lima"])
        self.assertEqual(resultado, [oferta])


if __name__ == "__main__":
    unittest.main()
