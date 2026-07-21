"""
PostulaPe — orquestador principal (embudo de 3 etapas).

Flujo:
  0. Scrape LIGERO de las plataformas (sin entrar al detalle).
  1. Filtros locales (antigüedad, modalidad, Lima) + dedup contra Excel +
     blocklist por título (0 tokens).
  2. Clasificación de TÍTULOS en lote con el LLM (1 llamada por lote).
  3. Se entra al detalle SOLO de los sobrevivientes para traer la descripción.
  4. Veredicto final del LLM (aplica/score) + generación de CV.
  5. Guardar en Excel (los que aplican quedan resaltados).

Uso:
    python main.py
    python main.py --solo-scrape                 # sin LLM (trae descripciones y guarda)
    python main.py --keywords "practicante,qa"   # override de keywords
"""

import sys

from postulape import config
from postulape.services import cv_generator, matcher
from postulape.storage import sheets_store as excel_store
from postulape.scrapers.base import (
    pasa_filtro_antiguedad, pasa_filtro_modalidad, pasa_filtro_departamento,
)
from postulape.scrapers import bumeran as scr_bumeran
from postulape.scrapers import computrabajo as scr_computrabajo
from postulape.scrapers import indeed as scr_indeed
from postulape.services.llm_client import LLMClient


# Etapa 0: scrape LIGERO (traer_descripcion=False donde aplica).
SCRAPERS = {
    "bumeran": lambda kw: scr_bumeran.scrapear_bumeran(
        kw, config.UBICACION, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG),
    "computrabajo": lambda kw: scr_computrabajo.scrapear_computrabajo(
        kw, config.UBICACION, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
        pubdate=config.MAX_DIAS_ANTIGUEDAD, traer_descripcion=False),
    "indeed": lambda kw: scr_indeed.scrapear_indeed(
        kw, config.UBICACION, config.MAX_DIAS_ANTIGUEDAD, config.HEADLESS,
        config.PAUSA_ENTRE_PAGINAS_SEG, traer_descripcion=False),
}


def scrapear_todo(keywords):
    avisos, vistos = [], set()
    for plataforma in config.PLATAFORMAS_ACTIVAS:
        scraper = SCRAPERS.get(plataforma)
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


def aplicar_filtros(avisos):
    out = []
    for a in avisos:
        if not pasa_filtro_antiguedad(a, config.MAX_DIAS_ANTIGUEDAD):
            continue
        if not pasa_filtro_modalidad(a, config.MODALIDADES_PERMITIDAS):
            continue
        if not pasa_filtro_departamento(a, config.DEPARTAMENTOS_PERMITIDOS):
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


def leer_cv_base():
    try:
        with open(config.RUTA_CV_BASE, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"[!] No existe {config.RUTA_CV_BASE}. Complétalo antes de usar el LLM.")
        return ""


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PostulaPe — embudo de scraping + LLM.")
    parser.add_argument("--keywords", help="Palabras clave separadas por coma.")
    parser.add_argument("--solo-scrape", action="store_true",
                        help="Solo scrapea y guarda (sin LLM).")
    parsed = parser.parse_args()
    solo_scrape = parsed.solo_scrape

    keywords = config.KEYWORDS
    if parsed.keywords:
        keywords = [k.strip() for k in parsed.keywords.split(",") if k.strip()]

    # --- Etapa 0: scrape ligero -------------------------------------------
    avisos = scrapear_todo(keywords)
    existentes = excel_store.cargar_ids_existentes(config.RUTA_EXCEL)
    nuevos = [a for a in avisos if a["id"] not in existentes]
    print(f"\n[Etapa 0] Scrapeados: {len(avisos)} | Nuevos: {len(nuevos)}")

    # --- Etapa 1: filtros locales + blocklist de título -------------------
    filtrados = aplicar_filtros(nuevos)
    sin_blocklist = [a for a in filtrados if not matcher.descartado_por_blocklist(a)]
    print(f"[Etapa 1] Tras filtros: {len(filtrados)} | Tras blocklist título: {len(sin_blocklist)}")

    # Modo solo-scrape: trae descripción de todo y guarda, sin LLM.
    if solo_scrape:
        traer_descripciones(sin_blocklist)
        excel_store.agregar_avisos(sin_blocklist, config.RUTA_EXCEL)
        print("\nModo --solo-scrape: guardado sin LLM.")
        return

    cv_texto = leer_cv_base()
    if not cv_texto:
        print("Sin CV base: traigo descripciones y guardo sin evaluar.")
        traer_descripciones(sin_blocklist)
        excel_store.agregar_avisos(sin_blocklist, config.RUTA_EXCEL)
        return

    llm = LLMClient()  # clasificar + evaluar (config.LLM_PROVIDER)
    llm_cv = LLMClient(config.LLM_PROVIDER_CV, config.LLM_MODEL_CV)  # CV

    # --- Etapa 2: clasificación de títulos en lote ------------------------
    ids_relevantes = matcher.clasificar_titulos_en_lote(sin_blocklist, llm, cv_texto[:800])
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
        matcher.evaluar(a, cv_texto, llm)
        print(f"    aplica={a['aplica']} score={a['score']} — {a['motivo']}")
        if a["aplica"]:
            aplican += 1
            try:
                a["cv_generado"] = cv_generator.generar(a, cv_texto, llm_cv)
            except Exception as e:
                print(f"    [CV] Error generando CV: {e}")
                a["cv_generado"] = ""
        # Guardado incremental: guarda este aviso ya, para no perder trabajo
        # si un aviso posterior falla o se corta por cuota.
        try:
            excel_store.agregar_avisos([a], config.RUTA_EXCEL)
        except Exception as e:
            print(f"    [Guardado] Error: {e}")
        if i < len(candidatos):
            time.sleep(config.PAUSA_LLM_SEG)   # respeta el límite por minuto

    # --- Etapa 5: guardar el resto (descartados por clasificación) --------
    procesados_ids = {a["id"] for a in candidatos}
    resto = [a for a in sin_blocklist if a["id"] not in procesados_ids]
    for a in resto:
        a.setdefault("estado", "Descartado (título)")
    excel_store.agregar_avisos(resto, config.RUTA_EXCEL)
    print(f"\n✔ Listo. {aplican} ofertas donde aplicas (CV en output/cvs/).")
    print(f"  Excel: {config.RUTA_EXCEL}")


if __name__ == "__main__":
    main()
