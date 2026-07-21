"""
Embudo hasta la Etapa 4 (VEREDICTO) + guardado en GOOGLE SHEETS.
NO genera CVs (para no gastar de más); solo evalúa aplica/score/estado.
Sirve para ver qué sale cuando "aplica" y cuando "No aplica".

Uso (desde la carpeta que contiene 'postulape', junto a main.py):
    python -m postulape.test.test_veredicto_sheets "analista"
    python -m postulape.test.test_veredicto_sheets "analista" 1
"""

import sys
import time

from postulape import config
from postulape.services import matcher
from postulape.services.llm_client import LLMClient
from postulape.storage import sheets_store as almacen
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
    paginas = int(sys.argv[2]) if len(sys.argv) > 2 else 1
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

    # Etapa 2: Gemini clasifica títulos
    llm = LLMClient()
    ids = matcher.clasificar_titulos_en_lote(sin_blocklist, llm, "Ingeniería de software")
    candidatos = [a for a in sin_blocklist if a["id"] in ids]
    print(f"\nScrapeados: {len(avisos)} | Blocklist deja: {len(sin_blocklist)} | Gemini OK: {len(candidatos)}")

    # Etapa 3: descripción SOLO de los candidatos
    por_plat = {}
    for a in candidatos:
        por_plat.setdefault((a.get("plataforma") or "").lower(), []).append(a)
    if por_plat.get("computrabajo"):
        scr_computrabajo.traer_descripciones(por_plat["computrabajo"], config.HEADLESS)
    if por_plat.get("indeed"):
        scr_indeed.traer_descripciones(por_plat["indeed"], config.HEADLESS)

    # Etapa 4: VEREDICTO (sin generar CV)
    cv_texto = open(config.RUTA_CV_BASE, encoding="utf-8").read()
    print(f"\n{'=' * 75}\nVEREDICTO ({len(candidatos)} candidatos)\n{'=' * 75}")
    aplican = 0
    for i, a in enumerate(candidatos, 1):
        matcher.evaluar(a, cv_texto, llm)
        marca = "APLICA" if a["aplica"] else "no    "
        if a["aplica"]:
            aplican += 1
        req = a.get("anios_requeridos")
        req_txt = f"| pide {req:g}a " if isinstance(req, (int, float)) else ""
        print(f"\n[{i}] {marca} score={a['score']} {req_txt}| {a.get('titulo')} [{a.get('plataforma')}]")
        print(f"     estado: {a.get('estado')}")
        if not a["aplica"]:
            print(f"     motivo: {a.get('motivo')}")
            if a.get("brechas"):
                print(f"     brechas: {a['brechas']}")
        if i < len(candidatos):
            time.sleep(config.PAUSA_LLM_SEG)

    # Guardar TODO en Sheets (aplican y no-aplican), para ver la columna 'aplica'
    almacen.agregar_avisos(candidatos)
    print(f"\n{'=' * 75}")
    print(f"RESUMEN: {aplican} aplican de {len(candidatos)} candidatos")
    print(f"Guardado en Sheets: {len(candidatos)} filas (revisa columnas 'aplica' y 'estado').")


if __name__ == "__main__":
    main()