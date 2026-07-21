"""
Prueba aislada de Indeed: scrapea y guarda en un Excel aparte,
con la MISMA estructura que el Excel general (usa excel_store).

Uso:
    python test_indeed.py "asistente"
    python test_indeed.py "asistente" 3      # 3 páginas
"""

import os
import sys

from postulape import config
from postulape.scrapers.indeed import scrapear_indeed
from postulape.storage import excel_store

# Excel de prueba, separado del general
RUTA_TEST = os.path.join(config.DIR_CVS, "..", "test_indeed.xlsx")
RUTA_TEST = os.path.normpath(RUTA_TEST)  # -> output/test_indeed.xlsx


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "asistente"
    paginas = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    print(f"Scrapeando Indeed :: '{kw}' ({paginas} páginas)...\n")
    avisos = scrapear_indeed(
        kw,
        config.UBICACION,
        fromage=config.MAX_DIAS_ANTIGUEDAD,
        headless=config.HEADLESS,
        pausa=config.PAUSA_ENTRE_PAGINAS_SEG,
        max_paginas=paginas,
    )

    print(f"\nScrapeados: {len(avisos)}")
    for a in avisos:
        desc = a.get("descripcion") or ""
        print(f"- [{a['id']}] {a.get('titulo')} | {a.get('empresa')} | desc={len(desc)} chars")

    excel_store.agregar_avisos(avisos, RUTA_TEST)
    print(f"\nGuardado en: {RUTA_TEST}")


if __name__ == "__main__":
    main()