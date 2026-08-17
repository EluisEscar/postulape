"""
Scraper CENTRAL del pool (tabla 'jobs'). Independiente de usuarios.
Scrapea la UNIÓN de:
  - config.KEYWORDS_BASE (genéricas)
  - las keywords guardadas en todos los perfiles (tabla profiles)
y hace upsert al pool compartido. NO filtra por rubro (el pool es neutro; el
filtrado por CV ocurre después, en el match por usuario).

Este es el script que luego correrá programado (cron / EventBridge) en AWS.

Uso (desde la carpeta que contiene 'postulape', junto a main.py):
    python -m postulape.scrape_pool
    python -m postulape.scrape_pool --keywords "analista,contador"   # override manual
"""

import argparse

from postulape import config
from postulape.cli import scrapear_todo
from postulape.storage import db


def _entero_positivo(valor: str) -> int:
    """Tipo de argparse para límites que deben ser mayores que cero."""
    try:
        numero = int(valor)
    except ValueError as e:
        raise argparse.ArgumentTypeError("debe ser un número entero") from e
    if numero <= 0:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return numero


def keywords_pool() -> list:
    """Unión de KEYWORDS_BASE + keywords de todos los perfiles (dedup, >=3 letras)."""
    candidatas = list(config.KEYWORDS_BASE) + db.keywords_de_perfiles()
    todas = []
    for k in candidatas:
        k = (k or "").strip().lower()
        if len(k) >= 3 and k not in todas:
            todas.append(k)
    return todas


def main():
    parser = argparse.ArgumentParser(description="PostulaPe — scraper del pool central.")
    parser.add_argument("--keywords", help="Override manual (separadas por coma).")
    parser.add_argument("--paginas", type=_entero_positivo, default=None,
                        help="Límite de páginas por plataforma y keyword (ej. --paginas 2). "
                             "Sin esto recorre todas las páginas disponibles (lento).")
    parser.add_argument("--max-keywords", type=_entero_positivo, default=None,
                        help="Usa solo las primeras N keywords (para corridas cortas).")
    parsed = parser.parse_args()

    if parsed.keywords:
        kws = [k.strip() for k in parsed.keywords.split(",") if k.strip()]
    else:
        kws = keywords_pool()

    print(f"[Pool] Keywords ({len(kws)}): {kws}")
    if parsed.max_keywords is not None:
        kws = kws[:parsed.max_keywords]
        print(f"[Pool] Recortado a {len(kws)} keywords.")

    avisos = scrapear_todo(kws, parsed.paginas)
    print(f"[Pool] Scrapeados (dedup en memoria): {len(avisos)}")

    existentes = db.cargar_ids_jobs()
    nuevos = [a for a in avisos if a["id"] not in existentes]
    print(f"[Pool] Nuevos respecto al pool: {len(nuevos)}")

    # Upsert de todos (dedup por id en la DB; refresca los ya existentes).
    db.agregar_jobs(avisos)
    print(f"[Pool] Pool actualizado. Total tras upsert ~ {len(existentes | {a['id'] for a in avisos})}.")


if __name__ == "__main__":
    main()
