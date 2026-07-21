"""
Prueba SOLO de la Etapa 2 del embudo: scrape ligero -> blocklist de título ->
Gemini clasifica títulos en lote. NO trae descripciones ni genera CV.

Uso (desde la raíz del proyecto POSTULAPE/POSTULAPE):
    python -m postulape.test.test_clasificacion "asistente"
    python -m postulape.test.test_clasificacion "asistente,analista"
"""

import sys

from postulape import config
from postulape.services import matcher
from postulape.services.llm_client import LLMClient
from postulape.scrapers.base import (
    pasa_filtro_antiguedad, pasa_filtro_modalidad, pasa_filtro_departamento,
)
from postulape.scrapers import bumeran as scr_bumeran
from postulape.scrapers import computrabajo as scr_computrabajo
from postulape.scrapers import indeed as scr_indeed
from postulape.storage import sheets_store

SCRAPERS = {
    "bumeran": lambda kw: scr_bumeran.scrapear_bumeran(
        kw, config.UBICACION, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
        max_paginas=2),
    "computrabajo": lambda kw: scr_computrabajo.scrapear_computrabajo(
        kw, config.UBICACION, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
        max_paginas=2, pubdate=config.MAX_DIAS_ANTIGUEDAD, traer_descripcion=False),
    "indeed": lambda kw: scr_indeed.scrapear_indeed(
        kw, config.UBICACION, config.MAX_DIAS_ANTIGUEDAD, config.HEADLESS,
        config.PAUSA_ENTRE_PAGINAS_SEG, max_paginas=2, traer_descripcion=False),
}


def main():
    keywords = ["asistente"]
    if len(sys.argv) > 1:
        keywords = [k.strip() for k in sys.argv[1].split(",")]

    # Etapa 0: scrape ligero
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

    # Etapa 1: filtros locales + blocklist de título
    filtrados = [a for a in avisos
                 if pasa_filtro_antiguedad(a, config.MAX_DIAS_ANTIGUEDAD)
                 and pasa_filtro_modalidad(a, config.MODALIDADES_PERMITIDAS)
                 and pasa_filtro_departamento(a, config.DEPARTAMENTOS_PERMITIDOS)]
    sin_blocklist = [a for a in filtrados if not matcher.descartado_por_blocklist(a)]
    bloqueados = [a for a in filtrados if matcher.descartado_por_blocklist(a)]

    print(f"\n{'=' * 70}")
    print(f"Scrapeados: {len(avisos)} | Tras filtros: {len(filtrados)} | "
          f"Tras blocklist: {len(sin_blocklist)}")

    if bloqueados:
        print(f"\n--- DESCARTADOS por blocklist ({len(bloqueados)}) ---")
        for a in bloqueados:
            print(f"  x {a.get('titulo')}")

    # Etapa 2: Gemini clasifica títulos
    llm = LLMClient()
    ids_relevantes = matcher.clasificar_titulos_en_lote(
        sin_blocklist, llm, "Ingeniería de software"
    )

    dentro = [a for a in sin_blocklist if a["id"] in ids_relevantes]
    fuera = [a for a in sin_blocklist if a["id"] not in ids_relevantes]

    print(f"\n--- RELEVANTES según Gemini ({len(dentro)}) ---")
    for a in dentro:
        print(f"  OK  {a.get('titulo')}  [{a.get('plataforma')}]")

    print(f"\n--- DESCARTADOS por Gemini ({len(fuera)}) ---")
    for a in fuera:
        print(f"  x   {a.get('titulo')}  [{a.get('plataforma')}]")

    print(f"\n{'=' * 70}")
    print(f"RESUMEN: {len(avisos)} -> {len(sin_blocklist)} (blocklist) -> {len(dentro)} (Gemini)")

    # Guardar en Google Sheets los que pasaron la clasificación
    sheets_store.agregar_avisos(dentro)
    print(f"\nGuardados en Sheets: {len(dentro)}")
    
if __name__ == "__main__":
    main()