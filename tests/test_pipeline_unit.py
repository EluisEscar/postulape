import argparse
import importlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from postulape import config
from postulape import cli
from postulape import personas
from postulape import scrape_pool
from postulape.services import match_usuario, matcher
from postulape.scrapers import bumeran as scr_bumeran
from postulape.status import es_estado_terminal, es_resultado_completo
from postulape.storage import db, excel_store, sheets_store


class LLMConError:
    def __init__(self):
        self.llamadas = 0

    def chat_json(self, *args, **kwargs):
        self.llamadas += 1
        raise RuntimeError("cuota agotada")


class LLMMalformado:
    def chat_json(self, *args, **kwargs):
        return ["esto no es un objeto"]


class HojaFalsa:
    def __init__(self, valores):
        self.valores = [list(fila) for fila in valores]
        self.llamadas_get_all_values = 0
        self.llamadas_col_values = 0
        self.llamadas_append = 0
        self.llamadas_update = 0
        self.rangos_update = []

    def get_all_values(self):
        self.llamadas_get_all_values += 1
        return [list(fila) for fila in self.valores]

    def col_values(self, columna):
        self.llamadas_col_values += 1
        indice = columna - 1
        return [fila[indice] for fila in self.valores if len(fila) > indice]

    def append_rows(self, filas, value_input_option=None):
        self.llamadas_append += 1
        fila_inicial = len(self.valores) + 1
        self.valores.extend([list(fila) for fila in filas])
        fila_final = fila_inicial + len(filas) - 1
        return {"updates": {
            "updatedRows": len(filas),
            "updatedRange": f"'Prueba'!A{fila_inicial}:M{fila_final}",
        }}

    def update(self, valores, rango, value_input_option=None,
               include_values_in_response=None):
        self.llamadas_update += 1
        self.rangos_update.append(rango)
        numeros = [int(n) for n in re.findall(r"[A-Z]+(\d+)", rango)]
        fila_inicial = numeros[0]
        fila_final = numeros[-1]
        while len(self.valores) < fila_final:
            self.valores.append([])
        for desplazamiento, fila in enumerate(valores):
            self.valores[fila_inicial - 1 + desplazamiento] = list(fila)
        rango_respuesta = f"'Prueba'!A{fila_inicial}:M{fila_final}"
        return {
            "updatedRows": len(valores),
            "updatedRange": rango_respuesta,
            "updatedData": {
                "range": rango_respuesta,
                "values": [list(fila) for fila in valores],
            },
        }


class HojaFallaEscritura(HojaFalsa):
    def update(self, valores, rango, value_input_option=None,
               include_values_in_response=None):
        self.llamadas_update += 1
        self.rangos_update.append(rango)
        return {"updatedRows": 0, "updatedRange": "",
                "updatedData": {"values": []}}


class HojaLecturaIntermitente(HojaFalsa):
    def __init__(self, valores, fallos_antes_de_exito):
        super().__init__(valores)
        self.fallos_restantes = fallos_antes_de_exito

    def get_all_values(self):
        self.llamadas_get_all_values += 1
        if self.fallos_restantes:
            self.fallos_restantes -= 1
            raise ConnectionError("DNS temporalmente no disponible")
        return [list(fila) for fila in self.valores]


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


class ConfiguracionLLMTest(unittest.TestCase):
    def test_proveedor_clasificacion_tiene_fallback_y_override(self):
        try:
            with mock.patch("dotenv.load_dotenv", return_value=False), \
                    mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}):
                os.environ.pop("LLM_PROVIDER_CLASIF", None)
                importlib.reload(config)
                self.assertEqual(config.LLM_PROVIDER_CLASIF, "gemini")

                os.environ["LLM_PROVIDER_CLASIF"] = "groq"
                importlib.reload(config)
                self.assertEqual(config.LLM_PROVIDER_CLASIF, "groq")
                self.assertEqual(config.LLM_PROVIDER, "gemini")
        finally:
            importlib.reload(config)


class ScrapePoolTest(unittest.TestCase):
    def test_limites_deben_ser_enteros_positivos(self):
        self.assertEqual(scrape_pool._entero_positivo("2"), 2)
        for valor in ("0", "-1", "texto"):
            with self.subTest(valor=valor), self.assertRaises(
                argparse.ArgumentTypeError):
                scrape_pool._entero_positivo(valor)


class PersonasTest(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.raiz = Path(self.temporal.name)
        self.parche_personas = mock.patch.object(
            personas, "DIR_PERSONAS", str(self.raiz / "personas"))
        self.parche_cvs = mock.patch.object(
            config, "DIR_CVS", str(self.raiz / "output" / "cvs"))
        self.parche_default = mock.patch.object(
            config, "DEFAULT_USER_ID", "usuario-default")
        self.parche_personas.start()
        self.parche_cvs.start()
        self.parche_default.start()
        self.addCleanup(self.parche_personas.stop)
        self.addCleanup(self.parche_cvs.stop)
        self.addCleanup(self.parche_default.stop)

    def _crear(self, carpeta, **datos):
        destino = Path(personas.DIR_PERSONAS) / carpeta
        destino.mkdir(parents=True)
        (destino / "cv_base.md").write_text("CV de prueba", encoding="utf-8")
        (destino / "persona.json").write_text(
            json.dumps(datos), encoding="utf-8")

    def test_carga_carpeta_y_user_id_de_persona_json(self):
        self._crear("maria", nombre="María", user_id="usuario-maria")

        resultado = personas.cargar("maria")

        self.assertEqual(resultado["carpeta"], "maria")
        self.assertEqual(resultado["user_id"], "usuario-maria")
        self.assertEqual(
            Path(resultado["dir_cvs"]),
            Path(config.DIR_CVS) / resultado["carpeta"])

    def test_user_id_ausente_usa_default(self):
        self._crear("esteban", nombre="Esteban")

        resultado = personas.cargar("esteban")

        self.assertEqual(resultado["user_id"], "usuario-default")

    def test_personas_distintas_tienen_identidad_distinta(self):
        self._crear("ana", user_id="usuario-ana")
        self._crear("luis", user_id="usuario-luis")

        ana = personas.cargar("ana")
        luis = personas.cargar("luis")

        self.assertNotEqual(ana["carpeta"], luis["carpeta"])
        self.assertNotEqual(ana["user_id"], luis["user_id"])


class CliPersonaTest(unittest.TestCase):
    def _persona(self):
        return {
            "nombre": "María Fernanda",
            "carpeta": "maria",
            "user_id": "usuario-maria",
            "cv_texto": "CV de arquitectura",
            "anios_experiencia": 1,
            "max_brecha_anios": 2,
            "ubicacion": "lima",
            "worksheet_name": "María",
            "spreadsheet_key": "sheet-maria",
            "dir_cvs": "output/cvs/maria",
        }

    def test_solo_scrape_deriva_keywords_sin_evaluar(self):
        perfil = {
            "rubro": "Arquitectura",
            "descripcion_rubro": "Diseño y supervisión arquitectónica",
            "keywords_busqueda": ["arquitecto", "dibujante arquitectónico"],
        }
        llm = mock.Mock()
        with mock.patch.object(
                sys, "argv", ["main.py", "--persona", "maria",
                              "--solo-scrape", "--paginas", "1"]), \
                mock.patch.object(cli.personas_mod, "cargar",
                                  return_value=self._persona()), \
                mock.patch.object(cli.resultados_store, "usar_hoja"), \
                mock.patch.object(cli.resultados_store, "cargar_ids_procesados",
                                  return_value=set()), \
                mock.patch.object(cli.resultados_store, "agregar_avisos"), \
                mock.patch.object(cli, "LLMClient", return_value=llm) as cliente_llm, \
                mock.patch.object(cli.perfil_mod, "derivar_perfil_desde_cv",
                                  return_value=perfil) as derivar, \
                mock.patch.object(cli, "scrapear_todo", return_value=[]) as scrapear, \
                mock.patch.object(cli, "aplicar_filtros", return_value=[]), \
                mock.patch.object(cli, "traer_descripciones"), \
                mock.patch.object(matcher, "clasificar_titulos") as clasificar, \
                mock.patch.object(matcher, "evaluar") as evaluar, \
                mock.patch.object(cli.cv_generator, "generar") as generar:
            cli.main()

        cliente_llm.assert_called_once_with(
            config.LLM_PROVIDER_CLASIF, config.LLM_MODEL_CLASIF)
        derivar.assert_called_once_with("CV de arquitectura", llm)
        scrapear.assert_called_once_with(
            ["arquitecto", "dibujante arquitectónico"], 1, ubicacion="lima")
        clasificar.assert_not_called()
        evaluar.assert_not_called()
        generar.assert_not_called()

    def test_solo_scrape_con_keywords_explicitas_no_llama_llm(self):
        with mock.patch.object(
                sys, "argv", ["main.py", "--persona", "maria",
                              "--solo-scrape", "--keywords", "arquitecto"]), \
                mock.patch.object(cli.personas_mod, "cargar",
                                  return_value=self._persona()), \
                mock.patch.object(cli.resultados_store, "usar_hoja"), \
                mock.patch.object(cli.resultados_store, "cargar_ids_procesados",
                                  return_value=set()), \
                mock.patch.object(cli.resultados_store, "agregar_avisos"), \
                mock.patch.object(cli, "LLMClient") as cliente_llm, \
                mock.patch.object(cli.perfil_mod, "derivar_perfil_desde_cv") as derivar, \
                mock.patch.object(cli, "scrapear_todo", return_value=[]), \
                mock.patch.object(cli, "aplicar_filtros", return_value=[]), \
                mock.patch.object(cli, "traer_descripciones"):
            cli.main()

        cliente_llm.assert_not_called()
        derivar.assert_not_called()

    def test_storage_usa_misma_carpeta_local_y_user_id_de_persona(self):
        oferta = aviso()
        persona = self._persona()
        ruta_cv = "output/cvs/maria/CV_Arquitecto.docx"

        def marcar_aplica(actual, *args, **kwargs):
            actual.update({
                "aplica": True,
                "score": 90,
                "motivo": "Buen encaje",
                "estado": "APLICA (90)",
            })

        with mock.patch.object(sys, "argv", ["main.py", "--persona", "maria"]), \
                mock.patch.object(cli.personas_mod, "cargar", return_value=persona), \
                mock.patch.object(cli.resultados_store, "usar_hoja"), \
                mock.patch.object(cli.resultados_store, "cargar_ids_procesados",
                                  return_value=set()), \
                mock.patch.object(cli.resultados_store, "agregar_avisos"), \
                mock.patch.object(cli.perfil_mod, "derivar_perfil_desde_cv", return_value={
                    "rubro": "Arquitectura",
                    "descripcion_rubro": "Diseño arquitectónico",
                    "keywords_busqueda": ["arquitecto"],
                }), \
                mock.patch.object(cli, "scrapear_todo", return_value=[oferta]), \
                mock.patch.object(cli, "aplicar_filtros", return_value=[oferta]), \
                mock.patch.object(matcher, "descartado_por_blocklist", return_value=False), \
                mock.patch.object(matcher, "clasificar_titulos",
                                  return_value=({oferta["id"]}, set())), \
                mock.patch.object(matcher, "evaluar", side_effect=marcar_aplica), \
                mock.patch.object(cli, "traer_descripciones"), \
                mock.patch.object(cli, "LLMClient",
                                  side_effect=[mock.Mock(), mock.Mock(), mock.Mock()]), \
                mock.patch.object(cli.cv_generator, "generar",
                                  return_value=ruta_cv) as generar, \
                mock.patch.object(config, "SUBIR_CVS", True), \
                mock.patch.object(db, "subir_cv_y_registrar",
                                  return_value="https://storage.test/cv") as subir:
            cli.main()

        self.assertEqual(generar.call_args.kwargs["dir_cvs"], persona["dir_cvs"])
        subir.assert_called_once_with(
            ruta_cv,
            persona=persona["carpeta"],
            user_id=persona["user_id"],
            job_id=oferta["id"],
        )
        self.assertEqual(Path(ruta_cv).parent.name, persona["carpeta"])

    def test_error_persistente_leyendo_ids_aborta_antes_de_procesar(self):
        hoja = HojaLecturaIntermitente(
            [sheets_store.ENCABEZADOS], fallos_antes_de_exito=10)
        with mock.patch.object(
                sys, "argv", ["main.py", "--persona", "maria",
                              "--solo-scrape", "--keywords", "arquitecto"]), \
                mock.patch.object(cli.personas_mod, "cargar",
                                  return_value=self._persona()), \
                mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja), \
                mock.patch.object(sheets_store.time, "sleep"), \
                mock.patch.object(cli, "scrapear_todo") as scrapear, \
                mock.patch.object(matcher, "clasificar_titulos") as clasificar, \
                mock.patch.object(matcher, "evaluar") as evaluar, \
                mock.patch.object(cli.cv_generator, "generar") as generar, \
                mock.patch("builtins.print") as imprimir:
            with self.assertRaises(SystemExit) as salida:
                cli.main()

        mensajes = "\n".join(" ".join(map(str, llamada.args))
                              for llamada in imprimir.call_args_list)
        self.assertEqual(salida.exception.code, 2)
        self.assertEqual(
            hoja.llamadas_get_all_values, sheets_store._INTENTOS_LECTURA)
        scrapear.assert_not_called()
        clasificar.assert_not_called()
        evaluar.assert_not_called()
        generar.assert_not_called()
        self.assertIn("Corrida abortada", mensajes)
        self.assertIn("no reprocesar", mensajes)
        self.assertIn("cuota de LLM", mensajes)

    def test_lectura_transitoria_se_recupera_y_continua(self):
        hoja = HojaLecturaIntermitente(
            [sheets_store.ENCABEZADOS], fallos_antes_de_exito=1)
        oferta = aviso()
        with mock.patch.object(
                sys, "argv", ["main.py", "--persona", "maria",
                              "--solo-scrape", "--keywords", "arquitecto"]), \
                mock.patch.object(cli.personas_mod, "cargar",
                                  return_value=self._persona()), \
                mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja), \
                mock.patch.object(sheets_store.time, "sleep"), \
                mock.patch.object(cli, "scrapear_todo", return_value=[oferta]) as scrapear, \
                mock.patch.object(cli, "aplicar_filtros", return_value=[oferta]), \
                mock.patch.object(cli, "traer_descripciones"), \
                mock.patch.object(cli.resultados_store, "agregar_avisos") as guardar:
            cli.main()

        self.assertEqual(hoja.llamadas_get_all_values, 2)
        scrapear.assert_called_once()
        guardar.assert_called_once_with([oferta], config.RUTA_EXCEL)

    def test_hoja_vacia_legitima_procesa_todos_como_nuevos(self):
        hoja = HojaLecturaIntermitente(
            [sheets_store.ENCABEZADOS], fallos_antes_de_exito=0)
        ofertas = [aviso(), {**aviso(), "id": "job-2"}]
        with mock.patch.object(
                sys, "argv", ["main.py", "--persona", "maria",
                              "--solo-scrape", "--keywords", "arquitecto"]), \
                mock.patch.object(cli.personas_mod, "cargar",
                                  return_value=self._persona()), \
                mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja), \
                mock.patch.object(cli, "scrapear_todo", return_value=ofertas), \
                mock.patch.object(cli, "aplicar_filtros", return_value=ofertas), \
                mock.patch.object(cli, "traer_descripciones"), \
                mock.patch.object(cli.resultados_store, "agregar_avisos") as guardar:
            cli.main()

        self.assertEqual(hoja.llamadas_get_all_values, 1)
        guardar.assert_called_once_with(ofertas, config.RUTA_EXCEL)


class BumeranDetalleTest(unittest.TestCase):
    class Elemento:
        def __init__(self, texto):
            self.texto = texto

        def inner_text(self):
            return self.texto

    class Pagina:
        def __init__(self, elementos):
            self.elementos = elementos
            self.url = None
            self.espera = None

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_selector(self, selector, **kwargs):
            self.espera = selector

        def query_selector(self, selector):
            return self.elementos.get(selector)

    def test_detalle_reemplaza_snippet_con_selector_principal(self):
        texto_completo = (
            "Descripción completa con funciones, requisitos, experiencia y "
            "condiciones suficientes para emitir un veredicto preciso."
        )
        page = self.Pagina({
            "#descripcion-aviso": self.Elemento(texto_completo),
        })
        oferta = aviso()
        oferta["descripcion"] = "Snippet incompleto del listado."

        scr_bumeran._traer_descripcion(page, oferta, pausa_detalle=0)

        self.assertEqual(oferta["descripcion"], texto_completo)
        self.assertEqual(page.url, oferta["link"])
        self.assertIn("#descripcion-aviso", page.espera)

    def test_detalle_inutil_conserva_snippet(self):
        snippet = "Snippet previo suficientemente útil como respaldo."
        page = self.Pagina({
            "#descripcion-aviso": self.Elemento("Muy corto"),
            "#ficha-detalle": self.Elemento(""),
            "#section-detalle": self.Elemento("Tampoco sirve"),
        })
        oferta = aviso()
        oferta["descripcion"] = snippet

        scr_bumeran._traer_descripcion(page, oferta, pausa_detalle=0)

        self.assertEqual(oferta["descripcion"], snippet)

    def test_cli_conecta_bumeran_en_etapa_tres(self):
        oferta = aviso()
        oferta["plataforma"] = "Bumeran"
        with mock.patch.object(cli.scr_bumeran, "traer_descripciones") as traer, \
                mock.patch.object(cli.scr_computrabajo, "traer_descripciones"), \
                mock.patch.object(cli.scr_indeed, "traer_descripciones"):
            cli.traer_descripciones([oferta])
        traer.assert_called_once_with([oferta], config.HEADLESS)


class MatcherTest(unittest.TestCase):
    def test_clasificacion_arquitectura_con_titulos_reales(self):
        descartados = [
            "Ingeniero Civil Estructural con exp. en mineria",
            "Coordinador de Saneamiento",
            "Jefe de Proyecto",
            "Ingeniero civil",
            "Supervisor Ingeniero Civil para obra Hidroeléctrica",
            "Ingeniero de Metalmecánica / Granja Pisco 14x7",
            "Residente de obra",
            "Ingeniero Residente en instalaciones eléctricas",
            "Modelador en Revit de MEP Eléctricas",
            "Modelador en Revit de Sanitaria",
            "Modelador BIM Comunicación",
        ]
        relevantes = [
            "Arquitecto/Ingeniero Civil - Responsable de Servicio",
            "Arquitecto/a de Interiores Remoto",
            "arquitecto de proyectos",
            "Practicante profesional de Arquitectura",
            "Asistente de Oficina Técnica",
            "arquitecto residente",
        ]
        titulos = descartados + relevantes
        avisos = [
            {"id": f"aviso-{i}", "titulo": titulo}
            for i, titulo in enumerate(titulos)
        ]

        class LLMClasificacionSimulado:
            def __init__(self):
                self.system = ""
                self.user = ""

            def chat_json(self, system, user, temperature=0.2):
                self.system = system
                self.user = user
                inicio = len(descartados)
                return {"relevantes": list(range(inicio, len(titulos)))}

        llm = LLMClasificacionSimulado()
        resultado, pendientes = matcher.clasificar_titulos(
            avisos, llm,
            "Arquitectura y urbanismo. Diseño y supervisión de proyectos arquitectónicos.")

        esperados = {
            f"aviso-{i}" for i in range(len(descartados), len(titulos))
        }
        self.assertEqual(resultado, esperados)
        self.assertEqual(pendientes, set())
        self.assertEqual(llm.system, matcher.SYSTEM_CLASIF)
        for titulo in titulos:
            self.assertIn(titulo, llm.user)
        self.assertIn("EXCEPCIÓN MIXTA", llm.system)
        self.assertIn("Compartir sector no lo rescata", llm.system)
        self.assertIn("herramienta que el candidato domina", llm.system)

    def test_error_de_clasificacion_deja_lote_pendiente(self):
        ofertas = [aviso(), {**aviso(), "id": "job-2"}]
        llm = LLMConError()
        with mock.patch.object(config, "TAM_LOTE_TITULOS", 40):
            relevantes, pendientes = matcher.clasificar_titulos(
                ofertas, llm, "software")
        self.assertEqual(relevantes, set())
        self.assertEqual(pendientes, {"job-1", "job-2"})
        self.assertEqual(llm.llamadas, matcher.INTENTOS_CLASIFICACION)

    def test_wrapper_compatibilidad_es_fail_closed(self):
        ofertas = [aviso(), {**aviso(), "id": "job-2"}]
        resultado = matcher.clasificar_titulos_en_lote(
            ofertas, LLMConError(), "software")
        self.assertEqual(resultado, set())

    def test_cli_no_evalua_un_lote_con_error_de_clasificacion(self):
        ofertas = [aviso(), {**aviso(), "id": "job-2"}]
        persona = {
            "nombre": "Prueba",
            "carpeta": "prueba",
            "user_id": "usuario-prueba",
            "cv_texto": "CV de prueba",
            "anios_experiencia": 1,
            "max_brecha_anios": 2,
            "ubicacion": "lima",
            "worksheet_name": "Prueba",
            "spreadsheet_key": "sheet-prueba",
            "dir_cvs": "output/cvs/prueba",
        }
        llm_clasif = LLMConError()

        with mock.patch.object(sys, "argv", ["main.py"]), \
                mock.patch.object(cli.personas_mod, "cargar", return_value=persona), \
                mock.patch.object(cli.resultados_store, "usar_hoja"), \
                mock.patch.object(cli.resultados_store, "cargar_ids_procesados",
                                  return_value=set()), \
                mock.patch.object(cli.resultados_store, "agregar_avisos") as guardar, \
                mock.patch.object(cli.perfil_mod, "derivar_perfil_desde_cv", return_value={
                    "rubro": "Arquitectura",
                    "descripcion_rubro": "Diseño arquitectónico",
                    "keywords_busqueda": ["arquitecto"],
                }), \
                mock.patch.object(cli, "scrapear_todo", return_value=ofertas), \
                mock.patch.object(cli, "aplicar_filtros", return_value=ofertas), \
                mock.patch.object(cli, "LLMClient",
                                  side_effect=[llm_clasif, mock.Mock(), mock.Mock()]), \
                mock.patch.object(cli, "traer_descripciones") as descripciones, \
                mock.patch.object(matcher, "evaluar") as evaluar, \
                mock.patch.object(cli.cv_generator, "generar") as generar:
            cli.main()

        descripciones.assert_not_called()
        evaluar.assert_not_called()
        generar.assert_not_called()
        guardados = [a for llamada in guardar.call_args_list for a in llamada.args[0]]
        self.assertEqual({a["id"] for a in guardados}, {"job-1", "job-2"})
        for oferta in guardados:
            self.assertEqual(oferta["estado"], matcher.ESTADO_ERROR_CLASIFICACION)
            self.assertFalse(es_estado_terminal(oferta["estado"]))
            self.assertFalse(oferta["estado"].startswith("Descartado"))

    def test_match_usuario_no_evalua_pendientes_de_clasificacion(self):
        ofertas = [aviso(), {**aviso(), "id": "job-2"}]
        perfil = {
            "cv_texto": "CV de prueba",
            "keywords": [],
            "rubro": "Arquitectura",
            "descripcion_rubro": "Diseño arquitectónico",
            "anios_experiencia": 1,
            "max_brecha_anios": 2,
        }
        with mock.patch.object(match_usuario.db, "get_perfil", return_value=perfil), \
                mock.patch.object(match_usuario.db, "jobs_recientes", return_value=ofertas), \
                mock.patch.object(match_usuario.db, "ids_evaluados", return_value=set()), \
                mock.patch.object(match_usuario.db, "guardar_matches") as guardar, \
                mock.patch.object(match_usuario.db, "matches_aplican", return_value=[]), \
                mock.patch.object(match_usuario, "_completar_descripciones") as descripciones, \
                mock.patch.object(matcher, "evaluar") as evaluar:
            match_usuario.correr_match(
                "usuario-1", llm=mock.Mock(), llm_clasif=LLMConError())

        descripciones.assert_not_called()
        evaluar.assert_not_called()
        guardados = guardar.call_args.args[1]
        self.assertEqual({a["id"] for a in guardados}, {"job-1", "job-2"})
        for oferta in guardados:
            self.assertEqual(oferta["estado"], matcher.ESTADO_ERROR_CLASIFICACION)
            self.assertFalse(es_estado_terminal(oferta["estado"]))

    def test_match_usuario_aborta_si_no_puede_leer_evaluados(self):
        ofertas = [aviso()]
        perfil = {
            "cv_texto": "CV de prueba",
            "keywords": [],
            "rubro": "Arquitectura",
            "descripcion_rubro": "Diseño arquitectónico",
        }
        with mock.patch.object(match_usuario.db, "get_perfil", return_value=perfil), \
                mock.patch.object(match_usuario.db, "jobs_recientes",
                                  return_value=ofertas), \
                mock.patch.object(match_usuario.db, "ids_evaluados",
                                  side_effect=ConnectionError("DNS caído")) as leer, \
                mock.patch.object(match_usuario.time, "sleep"), \
                mock.patch.object(matcher, "clasificar_titulos") as clasificar, \
                mock.patch.object(matcher, "evaluar") as evaluar, \
                mock.patch.object(match_usuario.db, "guardar_matches") as guardar:
            resultado = match_usuario.correr_match("usuario-1")

        self.assertEqual(
            leer.call_count, match_usuario._INTENTOS_LECTURA)
        self.assertEqual(resultado["estado"], "error_lectura_deduplicacion")
        self.assertIn("no repetir", resultado["mensaje"])
        clasificar.assert_not_called()
        evaluar.assert_not_called()
        guardar.assert_not_called()

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
        sheets_store.usar_hoja(
            f"test-{self._testMethodName}", "sheet-pruebas")
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

    def test_escritura_no_confirmada_reintenta_y_no_reporta_insercion(self):
        hoja = HojaFallaEscritura([sheets_store.ENCABEZADOS])
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja), \
                mock.patch.object(sheets_store.time, "sleep"), \
                mock.patch("builtins.print") as imprimir:
            resultado = sheets_store.agregar_avisos([aviso()])

        mensajes = "\n".join(" ".join(map(str, llamada.args))
                              for llamada in imprimir.call_args_list)
        self.assertEqual(hoja.llamadas_update, sheets_store._INTENTOS_APPEND)
        self.assertEqual(resultado["preparados"], 1)
        self.assertEqual(resultado["insertados"], 0)
        self.assertEqual(resultado["sin_confirmar"], ["job-1"])
        self.assertIn("Respuesta cruda", mensajes)
        self.assertIn("job-1", mensajes)
        self.assertIn("0 insertados confirmados", mensajes)

    def test_ids_se_leen_una_vez_y_luego_se_usa_cache(self):
        hoja = HojaFalsa([sheets_store.ENCABEZADOS])
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja):
            self.assertEqual(sheets_store.cargar_ids_existentes(), set())
            self.assertEqual(sheets_store.cargar_ids_existentes(), set())
            sheets_store.agregar_avisos([aviso()])
            sheets_store.agregar_avisos([{**aviso(), "id": "job-2"}])

        self.assertEqual(hoja.llamadas_get_all_values, 1)
        self.assertEqual(hoja.llamadas_update, 2)
        self.assertEqual(hoja.rangos_update, ["A2:M2", "A3:M3"])

    def test_ids_admiten_forzar_relectura(self):
        hoja = HojaFalsa([sheets_store.ENCABEZADOS])
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja):
            sheets_store.cargar_ids_existentes()
            sheets_store.cargar_ids_existentes(refrescar=True)

        self.assertEqual(hoja.llamadas_get_all_values, 2)

    def test_error_leyendo_ids_existentes_no_se_convierte_en_vacio(self):
        with mock.patch.object(
                sheets_store, "_indice_ids",
                side_effect=ConnectionError("DNS caído")) as leer, \
                mock.patch.object(sheets_store.time, "sleep"):
            with self.assertRaises(sheets_store.ErrorLecturaSheets):
                sheets_store.cargar_ids_existentes(refrescar=True)

        self.assertEqual(leer.call_count, sheets_store._INTENTOS_LECTURA)

    def test_cambiar_de_hoja_invalida_cache_de_ids(self):
        fila = ["job-otra-persona"] + [""] * (len(sheets_store.ENCABEZADOS) - 1)
        hoja_uno = HojaFalsa([sheets_store.ENCABEZADOS, fila])
        hoja_dos = HojaFalsa([sheets_store.ENCABEZADOS])

        sheets_store.usar_hoja("Persona Uno", "sheet-compartido")
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja_uno):
            self.assertEqual(
                sheets_store.cargar_ids_existentes(), {"job-otra-persona"})

        sheets_store.usar_hoja("Persona Dos", "sheet-compartido")
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja_dos):
            self.assertEqual(sheets_store.cargar_ids_existentes(), set())

        self.assertEqual(hoja_uno.llamadas_get_all_values, 1)
        self.assertEqual(hoja_dos.llamadas_get_all_values, 1)

    def test_incrementales_y_lote_final_reciben_rangos_distintos(self):
        hoja = HojaFalsa([sheets_store.ENCABEZADOS])
        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja):
            sheets_store.agregar_avisos([aviso()])
            sheets_store.agregar_avisos([{**aviso(), "id": "job-2"}])
            sheets_store.agregar_avisos([
                {**aviso(), "id": "job-3"},
                {**aviso(), "id": "job-4"},
            ])

        self.assertEqual(
            hoja.rangos_update, ["A2:M2", "A3:M3", "A4:M5"])
        self.assertEqual(
            [fila[0] for fila in hoja.valores[1:]],
            ["job-1", "job-2", "job-3", "job-4"],
        )

    def test_siguiente_fila_respeta_contenido_fuera_de_columna_id(self):
        nota = [""] * len(sheets_store.ENCABEZADOS)
        nota[9] = "nota manual"
        hoja = HojaFalsa([sheets_store.ENCABEZADOS, [], [], nota])

        with mock.patch.object(sheets_store, "_abrir_hoja", return_value=hoja):
            sheets_store.agregar_avisos([aviso()])

        self.assertEqual(hoja.rangos_update, ["A5:M5"])
        self.assertEqual(hoja.valores[4][0], "job-1")

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
