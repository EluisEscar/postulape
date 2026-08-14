"""
Personas: permite usar el proyecto con VARIAS personas, cada una con su CV,
sus años de experiencia, su carpeta de CVs generados y su hoja de resultados.

Estructura esperada (en la raíz del proyecto, junto a main.py):

    personas/
      esteban/
        cv_base.md          <- el CV de esa persona
        persona.json        <- sus datos (años de experiencia, ubicación, hoja)
      maria/
        cv_base.md
        persona.json

    output/cvs/esteban/...  <- CVs generados de cada persona, separados
    output/cvs/maria/...

persona.json (todos los campos son opcionales; se usan defaults de config):

    {
      "nombre": "Esteban",
      "anios_experiencia": 1,
      "max_brecha_anios": 2,
      "ubicacion": "lima",
      "worksheet_name": "Esteban",
      "spreadsheet_key": ""
    }

El RUBRO y las KEYWORDS no se ponen aquí: se derivan del CV con el LLM
(services/perfil.py), así funciona para cualquier carrera.
"""

import json
import os

from postulape import config

DIR_PERSONAS = os.path.join(config.DIR_BASE, "personas")


def listar() -> list:
    """Nombres de las personas configuradas (carpetas dentro de personas/)."""
    if not os.path.isdir(DIR_PERSONAS):
        return []
    return sorted(
        d for d in os.listdir(DIR_PERSONAS)
        if os.path.isdir(os.path.join(DIR_PERSONAS, d))
    )


def cargar(nombre: str | None) -> dict:
    """Carga una persona. Si nombre es None, devuelve la persona por defecto
    (el cv_base.md de la raíz y los valores de config): modo un solo usuario."""
    if not nombre:
        return {
            "nombre": "(default)",
            "cv_texto": _leer(config.RUTA_CV_BASE),
            "anios_experiencia": config.ANIOS_EXPERIENCIA,
            "max_brecha_anios": config.MAX_BRECHA_ANIOS,
            "ubicacion": config.UBICACION,
            "worksheet_name": config.WORKSHEET_NAME,
            "spreadsheet_key": config.SPREADSHEET_KEY,
            "dir_cvs": config.DIR_CVS,
        }

    base = os.path.join(DIR_PERSONAS, nombre)
    if not os.path.isdir(base):
        disponibles = listar()
        raise FileNotFoundError(
            f"No existe la persona '{nombre}' en {DIR_PERSONAS}. "
            f"Disponibles: {disponibles or 'ninguna'}"
        )

    ruta_cv = os.path.join(base, "cv_base.md")
    cv_texto = _leer(ruta_cv)
    if not cv_texto:
        raise FileNotFoundError(f"Falta el CV de '{nombre}': {ruta_cv}")

    datos = {}
    ruta_json = os.path.join(base, "persona.json")
    if os.path.exists(ruta_json):
        try:
            datos = json.loads(_leer(ruta_json) or "{}")
        except json.JSONDecodeError as e:
            print(f"[Personas] persona.json de '{nombre}' inválido: {e}")

    return {
        "nombre": datos.get("nombre", nombre),
        "cv_texto": cv_texto,
        "anios_experiencia": datos.get("anios_experiencia", config.ANIOS_EXPERIENCIA),
        "max_brecha_anios": datos.get("max_brecha_anios", config.MAX_BRECHA_ANIOS),
        "ubicacion": datos.get("ubicacion", config.UBICACION),
        # Cada persona en su propia HOJA del Sheet (o su propio Sheet si pone key).
        "worksheet_name": datos.get("worksheet_name", nombre),
        "spreadsheet_key": datos.get("spreadsheet_key", config.SPREADSHEET_KEY),
        # Sus CVs generados van a una subcarpeta propia.
        "dir_cvs": os.path.join(config.DIR_CVS, nombre),
    }


def _leer(ruta: str) -> str:
    if not ruta or not os.path.exists(ruta):
        return ""
    with open(ruta, encoding="utf-8") as f:
        return f.read()
