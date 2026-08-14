"""
PostulaPe — orquestador principal del embudo de búsqueda y evaluación.

Flujo:
  0. Scrape LIGERO de las plataformas (sin entrar al detalle).
  1. Filtros locales (antigüedad, modalidad, ubicación) + deduplicación +
     blocklist por título (0 tokens).
  2. Clasificación de TÍTULOS en lote con el LLM (1 llamada por lote).
  3. Se entra al detalle SOLO de los sobrevivientes para traer la descripción.
  4. Veredicto final del LLM (aplica/score) + generación de CV.
  5. Guardar o actualizar el resultado en Google Sheets.

Uso:
    python main.py
    python main.py --solo-scrape                 # sin LLM (trae descripciones y guarda)
    python main.py --keywords "practicante,qa"   # override de keywords
"""

from postulape import config
from postulape.services import cv_generator, matcher
from postulape.services import perfil as perfil_mod
from postulape import personas as personas_mod
from postulape.storage import sheets_store as resultados_store
from postulape.scrapers.base import (
    pasa_filtro_antiguedad, pasa_filtro_modalidad, pasa_filtro_departamento,
)
from postulape.scrapers import bumeran as scr_bumeran
from postulape.scrapers import computrabajo as scr_computrabajo
from postulape.scrapers import indeed as scr_indeed
from postulape.services.llm_client import LLMClient


# Etapa 0: scrape LIGERO (traer_descripcion=False donde aplica).
def _scrapers(paginas=None, ubicacion=None):
    """Devuelve los scrapers. paginas=None -> sin límite (default del scraper)."""
    kw_pag = {} if paginas is None else {"max_paginas": paginas}
    ubicacion = ubicacion or config.UBICACION
    return {
        "bumeran": lambda kw: scr_bumeran.scrapear_bumeran(
            kw, ubicacion, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
            **kw_pag),
        "computrabajo": lambda kw: scr_computrabajo.scrapear_computrabajo(
            kw, ubicacion, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
            pubdate=config.MAX_DIAS_ANTIGUEDAD, traer_descripcion=False, **kw_pag),
        "indeed": lambda kw: scr_indeed.scrapear_indeed(
            kw, ubicacion, config.MAX_DIAS_ANTIGUEDAD, config.HEADLESS,
            config.PAUSA_ENTRE_PAGINAS_SEG, traer_descripcion=False, **kw_pag),
    }


def scrapear_todo(keywords, paginas=None, ubicacion=None):
    avisos, vistos = [], set()
    scrapers = _scrapers(paginas, ubicacion)
    for plataforma in config.PLATAFORMAS_ACTIVAS:
        scraper = scrapers.get(plataforma)
        if not scraper:
            continue
        for kw in keywords:
            print(f"\n=== {plataforma.upper()} :: '{kw}' ===")
            try:
                for a in scraper(kw):
                    if a["id"] not in vistos:
                        vistos.add(a["id"])
                        avisos.append(a)
            except Exception as e:
                print(f"[{plataforma}] Falló '{kw}': {e}")
    return avisos


def aplicar_filtros(avisos, departamentos=None):
    departamentos = (config.DEPARTAMENTOS_PERMITIDOS
                     if departamentos is None else departamentos)
    out = []
    for a in avisos:
        if not pasa_filtro_antiguedad(a, config.MAX_DIAS_ANTIGUEDAD):
            continue
        if not pasa_filtro_modalidad(a, config.MODALIDADES_PERMITIDAS):
            continue
        if not pasa_filtro_departamento(a, departamentos):
            continue
        out.append(a)
    return out


def traer_descripciones(avisos):
    """Etapa 3: agrupa por plataforma y trae la descripción con la función de
    cada scraper. Bumerán ya la trae del listado, así que se omite."""
    por_plat = {}
    for a in avisos:
        por_plat.setdefault((a.get("plataforma") or "").lower(), []).append(a)
    if por_plat.get("computrabajo"):
        scr_computrabajo.traer_descripciones(por_plat["computrabajo"], config.HEADLESS)
    if por_plat.get("indeed"):
        scr_indeed.traer_descripciones(por_plat["indeed"], config.HEADLESS)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PostulaPe — embudo de scraping + LLM.")
    parser.add_argument("--keywords", help="Palabras clave separadas por coma.")
    parser.add_argument("--solo-scrape", action="store_true",
                        help="Solo scrapea y guarda (sin LLM).")
    parser.add_argument("--paginas", type=int, default=None,
                        help="Límite de páginas por plataforma (ej. --paginas 1 para probar).")
    parser.add_argument("--persona", help="Nombre de la carpeta en personas/ "
                                          "(ej. --persona maria). Sin esto usa cv_base.md de la raíz.")
    parsed = parser.parse_args()
    solo_scrape = parsed.solo_scrape

    # --- Persona: CV, años y destinos propios -----------------------------
    persona = personas_mod.cargar(parsed.persona)
    print(f"[Persona] {persona['nombre']} | experiencia ~{persona['anios_experiencia']} año(s) "
          f"| hoja: {persona['worksheet_name']}")
    resultados_store.usar_hoja(persona["worksheet_name"], persona["spreadsheet_key"])

    # --- Perfil: rubro + keywords derivados del CV ------------------------
    cv_texto = persona["cv_texto"]
    perfil = {"rubro": "", "descripcion_rubro": "", "keywords_busqueda": []}
    llm_clasif = None
    if not solo_scrape and cv_texto:
        llm_clasif = LLMClient(config.LLM_PROVIDER, config.LLM_MODEL_CLASIF)
        perfil = perfil_mod.derivar_perfil_desde_cv(cv_texto, llm_clasif)
        print(f"[Perfil] Rubro: {perfil['rubro']}")
        print(f"[Perfil] Keywords derivadas: {perfil['keywords_busqueda']}")

    # Keywords de búsqueda: override CLI > derivadas del CV > config.KEYWORDS
    if parsed.keywords:
        keywords = [k.strip() for k in parsed.keywords.split(",") if k.strip()]
    elif perfil["keywords_busqueda"]:
        keywords = [k for k in perfil["keywords_busqueda"] if len(k) >= 3]
    else:
        keywords = config.KEYWORDS
    print(f"[Búsqueda] Keywords: {keywords}")

    # --- Etapa 0: scrape ligero -------------------------------------------
    ubicacion = persona["ubicacion"]
    avisos = scrapear_todo(keywords, parsed.paginas, ubicacion=ubicacion)
    procesados = resultados_store.cargar_ids_procesados(config.RUTA_EXCEL)
    por_procesar = [a for a in avisos if a["id"] not in procesados]
    print(f"\n[Etapa 0] Scrapeados: {len(avisos)} | Por procesar: {len(por_procesar)}")

    # --- Etapa 1: filtros locales (blocklist TI apagado por defecto) ------
    departamento = ubicacion.split(",")[-1].strip() if ubicacion else ""
    departamentos = [departamento] if departamento else config.DEPARTAMENTOS_PERMITIDOS
    filtrados = aplicar_filtros(por_procesar, departamentos=departamentos)
    sin_blocklist = [a for a in filtrados if not matcher.descartado_por_blocklist(a)]
    print(f"[Etapa 1] Tras filtros: {len(filtrados)} | Tras blocklist: {len(sin_blocklist)}")

    # Modo solo-scrape: trae descripción de todo y guarda, sin LLM.
    if solo_scrape:
        traer_descripciones(sin_blocklist)
        resultados_store.agregar_avisos(sin_blocklist, config.RUTA_EXCEL)
        print("\nModo --solo-scrape: guardado sin LLM.")
        return

    if not cv_texto:
        print("Sin CV base: traigo descripciones y guardo sin evaluar.")
        traer_descripciones(sin_blocklist)
        resultados_store.agregar_avisos(sin_blocklist, config.RUTA_EXCEL)
        return

    llm = LLMClient()  # veredicto (config.LLM_MODEL)
    if llm_clasif is None:
        llm_clasif = LLMClient(config.LLM_PROVIDER, config.LLM_MODEL_CLASIF)
    llm_cv = LLMClient(config.LLM_PROVIDER_CV, config.LLM_MODEL_CV)  # CV

    # --- Etapa 2: clasificación de títulos en lote (según rubro) ----------
    perfil_str = perfil_mod.texto_perfil(perfil)
    ids_relevantes = matcher.clasificar_titulos_en_lote(sin_blocklist, llm_clasif, perfil_str)
    candidatos = [a for a in sin_blocklist if a["id"] in ids_relevantes]
    print(f"[Etapa 2] Títulos relevantes según LLM: {len(candidatos)} de {len(sin_blocklist)}")

    # --- Etapa 3: descripción SOLO de los candidatos ----------------------
    print(f"[Etapa 3] Trayendo descripción de {len(candidatos)} avisos...")
    traer_descripciones(candidatos)

    # --- Etapa 4: veredicto final + CV (guardado INCREMENTAL) -------------
    import time
    aplican = 0
    for i, a in enumerate(candidatos, 1):
        print(f"\n[Etapa 4 · {i}/{len(candidatos)}] {a.get('titulo')} ({a.get('plataforma')})")
        matcher.evaluar(a, cv_texto, llm,
                        anios_experiencia=persona["anios_experiencia"],
                        max_brecha_anios=persona["max_brecha_anios"])
        print(f"    aplica={a['aplica']} score={a['score']} — {a['motivo']}")
        if a["aplica"]:
            aplican += 1
            try:
                a["cv_generado"] = cv_generator.generar(a, cv_texto, llm_cv,
                                                       dir_cvs=persona["dir_cvs"])
            except Exception as e:
                print(f"    [CV] Error generando CV: {e}")
                a["cv_generado"] = ""

            if not a["cv_generado"]:
                # El match fue positivo, pero el entregable quedó incompleto.
                # Mantenerlo reintentable evita perder el CV por una caída puntual.
                a["estado"] = "Pendiente de reintento (error generando CV)"

            # Subida opcional a Supabase Storage (bucket privado). Si está
            # activada, en la hoja se guarda el ENLACE TEMPORAL en vez de la
            # ruta local, para que la persona pueda descargar su CV.
            if config.SUBIR_CVS and a["cv_generado"]:
                try:
                    from postulape.storage import db as _db
                    enlace = _db.subir_cv_y_registrar(
                        a["cv_generado"],
                        persona=str(persona["nombre"]).lower().replace(" ", "_"),
                        user_id=config.DEFAULT_USER_ID,
                        job_id=a.get("id"),
                    )
                    if enlace:
                        a["cv_generado"] = enlace
                except Exception as e:
                    print(f"    [Storage] Error subiendo CV: {e}")
        # Guardado incremental: guarda este aviso ya, para no perder trabajo
        # si un aviso posterior falla o se corta por cuota.
        try:
            resultados_store.agregar_avisos(
                [a], config.RUTA_EXCEL, actualizar_existentes=True)
        except Exception as e:
            print(f"    [Guardado] Error: {e}")
        if i < len(candidatos):
            time.sleep(config.PAUSA_LLM_SEG)   # respeta el límite por minuto

    # --- Etapa 5: guardar el resto (descartados por clasificación) --------
    procesados_ids = {a["id"] for a in candidatos}
    resto = [a for a in sin_blocklist if a["id"] not in procesados_ids]
    for a in resto:
        a.setdefault("estado", "Descartado (título)")
    resultados_store.agregar_avisos(
        resto, config.RUTA_EXCEL, actualizar_existentes=True)
    print(f"\n✔ Listo. {aplican} ofertas donde aplicas.")
    print(f"  CVs locales: {persona['dir_cvs']}")
    print(f"  Resultados en la hoja '{persona['worksheet_name']}' del Google Sheet.")
    if config.SUBIR_CVS:
        print(f"  CVs subidos al bucket '{config.BUCKET_CVS}' (enlaces en la hoja).")


if __name__ == "__main__":
    main()
