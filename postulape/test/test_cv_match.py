"""
Test de generación de CV + medición de coincidencia (similitud) con la oferta.

Para cada trabajo que el veredicto marca como APLICA:
  1. genera el CV adaptado (usando las keywords de esa oferta),
  2. mide la similitud del CV vs la descripción de la oferta ANTES (CV base)
     y DESPUÉS (CV adaptado), para ver cuánto sube el "match",
  3. guarda en Google Sheets (con la ruta del CV en la columna 'cv').

La similitud es un coseno de frecuencias de términos (0-100%), sin librerías
extra. No es un ATS real, pero sirve para comparar base vs adaptado.

Uso (desde la carpeta que contiene 'postulape', junto a main.py):
    python -m postulape.test.test_cv_match "analista" 1
"""

import math
import os
import sys
import time
from collections import Counter

from postulape import config
from postulape.services import matcher, cv_generator
from postulape.services.llm_client import LLMClient
from postulape.storage import sheets_store as almacen
from postulape.scrapers.base import (
    quitar_tildes, pasa_filtro_antiguedad, pasa_filtro_modalidad,
    pasa_filtro_departamento,
)
from postulape.scrapers import bumeran as scr_bumeran
from postulape.scrapers import computrabajo as scr_computrabajo
from postulape.scrapers import indeed as scr_indeed

try:
    from docx import Document
except Exception:
    Document = None

_STOP = set("""de la el en y a los las un una que con por para su sus del al se es
son o u e como mas más este esta estos estas lo le les nos su tu mi ser haber
tener hacer sobre entre desde hasta muy fue han ha he""".split())


def _tokens(texto: str):
    t = quitar_tildes((texto or "").lower())
    palabras = "".join(c if c.isalnum() else " " for c in t).split()
    return [p for p in palabras if len(p) >= 3 and p not in _STOP]


def similitud(a: str, b: str) -> float:
    """Coseno de frecuencias de términos entre dos textos, en % (0-100)."""
    ca, cb = Counter(_tokens(a)), Counter(_tokens(b))
    if not ca or not cb:
        return 0.0
    comunes = set(ca) & set(cb)
    dot = sum(ca[t] * cb[t] for t in comunes)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return round(100 * dot / (na * nb), 1) if na and nb else 0.0


def _texto_docx(ruta: str) -> str:
    if not ruta or Document is None or not os.path.exists(ruta):
        return ""
    try:
        d = Document(ruta)
        return "\n".join(p.text for p in d.paragraphs)
    except Exception:
        return ""


def _cobertura_keywords(keywords, texto: str):
    """% de keywords de la oferta que aparecen en el texto del CV."""
    if not keywords:
        return 0.0, [], []
    tnorm = quitar_tildes((texto or "").lower())
    presentes = [k for k in keywords if quitar_tildes(k.lower()) in tnorm]
    faltantes = [k for k in keywords if k not in presentes]
    return round(100 * len(presentes) / len(keywords), 1), presentes, faltantes


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
    keywords_busqueda = [k.strip() for k in (sys.argv[1] if len(sys.argv) > 1 else "analista").split(",")]
    paginas = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    SCR = scrapers(paginas)

    # Etapa 0: scrape ligero
    avisos, vistos = [], set()
    for plataforma in config.PLATAFORMAS_ACTIVAS:
        scraper = SCR.get(plataforma)
        if not scraper:
            continue
        for kw in keywords_busqueda:
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

    # Etapa 2: clasificación de títulos (modelo barato)
    llm_clasif = LLMClient(config.LLM_PROVIDER, config.LLM_MODEL_CLASIF)
    ids = matcher.clasificar_titulos_en_lote(sin_blocklist, llm_clasif, "Ingeniería de software")
    candidatos = [a for a in sin_blocklist if a["id"] in ids]
    print(f"\nScrapeados: {len(avisos)} | Blocklist deja: {len(sin_blocklist)} | Gemini OK: {len(candidatos)}")

    # Etapa 3: descripción de los candidatos
    por_plat = {}
    for a in candidatos:
        por_plat.setdefault((a.get("plataforma") or "").lower(), []).append(a)
    if por_plat.get("computrabajo"):
        scr_computrabajo.traer_descripciones(por_plat["computrabajo"], config.HEADLESS)
    if por_plat.get("indeed"):
        scr_indeed.traer_descripciones(por_plat["indeed"], config.HEADLESS)

    # Etapa 4 + 5: veredicto (70b) y, si aplica, CV + medición de match
    cv_base = open(config.RUTA_CV_BASE, encoding="utf-8").read()
    llm = LLMClient()  # veredicto (LLM_MODEL)
    llm_cv = LLMClient(config.LLM_PROVIDER_CV, config.LLM_MODEL_CV)  # CV

    print(f"\n{'=' * 78}\nVEREDICTO + CV ({len(candidatos)} candidatos)\n{'=' * 78}")
    aplican = 0
    for i, a in enumerate(candidatos, 1):
        matcher.evaluar(a, cv_base, llm)
        marca = "APLICA" if a["aplica"] else "no    "
        print(f"\n[{i}] {marca} score={a['score']} | {a.get('titulo')} [{a.get('plataforma')}]")

        if a["aplica"]:
            aplican += 1
            desc = a.get("descripcion") or ""
            sim_antes = similitud(desc, cv_base)
            ruta = cv_generator.generar(a, cv_base, llm_cv)
            a["cv_generado"] = ruta
            cv_txt = _texto_docx(ruta)
            sim_desp = similitud(desc, cv_txt) if cv_txt else 0.0
            cob, presentes, faltantes = _cobertura_keywords(a.get("keywords", []), cv_txt)
            print(f"     similitud oferta↔CV:  base {sim_antes}%  →  adaptado {sim_desp}%")
            print(f"     cobertura keywords en el CV: {cob}%  ({len(presentes)}/{len(a.get('keywords', []))})")
            if faltantes:
                print(f"     keywords que faltaron: {faltantes}")
        else:
            print(f"     estado: {a.get('estado')}")

        if i < len(candidatos):
            time.sleep(config.PAUSA_LLM_SEG)

    # Guardar todos en Sheets (aplican y no-aplican)
    almacen.agregar_avisos(candidatos)
    print(f"\n{'=' * 78}\nRESUMEN: {aplican} aplican de {len(candidatos)}. "
          f"CVs en {config.DIR_CVS}")


if __name__ == "__main__":
    main()