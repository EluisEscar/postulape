"""
Prueba aislada de Computrabajo: scrapea y guarda en un Excel aparte,
con la MISMA estructura que el Excel general (usa excel_store).

Uso:
    python test_computrabajo.py "asistente"
    python test_computrabajo.py "asistente" 3      # 3 páginas
"""

import os
import sys

from postulape import config
from postulape.scrapers.computrabajo import scrapear_computrabajo
from postulape.storage import excel_store

# Excel de prueba, separado del general
RUTA_TEST = os.path.normpath(os.path.join(config.DIR_CVS, "..", "test_computrabajo.xlsx"))


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "asistente"
    paginas = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    print(f"Scrapeando Computrabajo :: '{kw}' ({paginas} páginas)...\n")
    avisos = scrapear_computrabajo(
        kw,
        config.UBICACION,
        headless=config.HEADLESS,
        pausa=config.PAUSA_ENTRE_PAGINAS_SEG,
        max_paginas=paginas,
        pubdate=config.MAX_DIAS_ANTIGUEDAD,
    )

    print(f"\nScrapeados: {len(avisos)}")
    for a in avisos:
        desc = a.get("descripcion") or ""
        print(f"- [{a['id']}] {a.get('titulo')} | {a.get('empresa')} | "
              f"{a.get('distrito')} | {a.get('modalidad')} | {a.get('antiguedad')} | desc={len(desc)} chars")

    excel_store.agregar_avisos(avisos, RUTA_TEST)
    print(f"\nGuardado en: {RUTA_TEST}")


if __name__ == "__main__":
    main()