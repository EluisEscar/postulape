"""
Embudo COMPLETO hasta Etapa 3 (con descripciones) + guardado en GOOGLE SHEETS.
Igual que test_embudo_excel pero escribiendo en Sheets en vez de Excel.
Muestra en consola qué rechazó cada filtro.

Uso (desde la carpeta que contiene 'postulape', junto a main.py):
    python -m postulape.test.test_embudo_sheets "analista"
    python -m postulape.test.test_embudo_sheets "analista" 2
"""

import sys

from postulape import config
from postulape.services import matcher
from postulape.services.llm_client import LLMClient
from postulape.storage import sheets_store as almacen   # <-- guarda en Sheets
from postulape.scrapers.base import (
    pasa_filtro_antiguedad, pasa_filtro_modalidad, pasa_filtro_departamento,
)
from postulape.scrapers import bumeran as scr_bumeran
from postulape.scrapers import computrabajo as scr_computrabajo
from postulape.scrapers import indeed as scr_indeed


def scrapers(paginas):
    return {
        "bumeran": lambda kw: scr_bumeran.scrapear_bumeran(
            kw, config.UBICACION, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
            max_paginas=paginas),
        "computrabajo": lambda kw: scr_computrabajo.scrapear_computrabajo(
            kw, config.UBICACION, config.HEADLESS, config.PAUSA_ENTRE_PAGINAS_SEG,
            max_paginas=paginas, pubdate=config.MAX_DIAS_ANTIGUEDAD, traer_descripcion=False),
        "indeed": lambda kw: scr_indeed.scrapear_indeed(
            kw, config.UBICACION, config.MAX_DIAS_ANTIGUEDAD, config.HEADLESS,
            config.PAUSA_ENTRE_PAGINAS_SEG, max_paginas=paginas, traer_descripcion=False),
    }


def main():
    keywords = [k.strip() for k in (sys.argv[1] if len(sys.argv) > 1 else "analista").split(",")]
    paginas = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    SCR = scrapers(paginas)

    # Etapa 0: scrape ligero
    avisos, vistos = [], set()
    for plataforma in config.PLATAFORMAS_ACTIVAS:
        scraper = SCR.get(plataforma)
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

    # Etapa 1: filtros + blocklist
    filtrados = [a for a in avisos
                 if pasa_filtro_antiguedad(a, config.MAX_DIAS_ANTIGUEDAD)
                 and pasa_filtro_modalidad(a, config.MODALIDADES_PERMITIDAS)
                 and pasa_filtro_departamento(a, config.DEPARTAMENTOS_PERMITIDOS)]
    sin_blocklist = [a for a in filtrados if not matcher.descartado_por_blocklist(a)]
    bloqueados = [a for a in filtrados if matcher.descartado_por_blocklist(a)]

    # Etapa 2: Gemini clasifica títulos
    llm = LLMClient()
    ids = matcher.clasificar_titulos_en_lote(sin_blocklist, llm, "Ingeniería de software")
    candidatos = [a for a in sin_blocklist if a["id"] in ids]
    rechazados_gemini = [a for a in sin_blocklist if a["id"] not in ids]

    # ---------- Impresión de rechazados ----------
    print(f"\n{'=' * 70}")
    print(f"Scrapeados: {len(avisos)} | Blocklist deja: {len(sin_blocklist)} | Gemini OK: {len(candidatos)}")

    print(f"\n--- RECHAZADOS por blocklist ({len(bloqueados)}) ---")
    for a in bloqueados:
        print(f"  x  {a.get('titulo')}  [{a.get('plataforma')}]")

    print(f"\n--- RECHAZADOS por Gemini ({len(rechazados_gemini)}) ---")
    for a in rechazados_gemini:
        print(f"  x  {a.get('titulo')}  [{a.get('plataforma')}]")

    print(f"\n--- ACEPTADOS (van a Sheets) ({len(candidatos)}) ---")
    for a in candidatos:
        print(f"  OK {a.get('titulo')}  [{a.get('plataforma')}]")
    print(f"{'=' * 70}")

    # Etapa 3: descripción SOLO de los candidatos (Computrabajo / Indeed)
    por_plat = {}
    for a in candidatos:
        por_plat.setdefault((a.get("plataforma") or "").lower(), []).append(a)
    if por_plat.get("computrabajo"):
        scr_computrabajo.traer_descripciones(por_plat["computrabajo"], config.HEADLESS)
    if por_plat.get("indeed"):
        scr_indeed.traer_descripciones(por_plat["indeed"], config.HEADLESS)

    # Guardar en Google Sheets (con descripciones completas)
    almacen.agregar_avisos(candidatos)
    print(f"\nGuardado en Sheets: {len(candidatos)} avisos.")


if __name__ == "__main__":
    main()