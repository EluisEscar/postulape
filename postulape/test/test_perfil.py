"""
Prueba la derivación de PERFIL desde un CV (rubro + keywords), sin scrapear.
Sirve para verificar que el rubro se detecta bien para distintas carreras.

Uso (desde la carpeta que contiene 'postulape', junto a main.py):
    python -m postulape.test.test_perfil                # usa cv_base.md
    python -m postulape.test.test_perfil ruta/al/cv.md  # usa otro CV (texto/markdown)
"""

import sys

from postulape import config
from postulape.services.llm_client import LLMClient
from postulape.services import perfil as perfil_mod


def main():
    ruta = sys.argv[1] if len(sys.argv) > 1 else config.RUTA_CV_BASE
    cv_texto = open(ruta, encoding="utf-8").read()

    print(f"CV leído de: {ruta}  ({len(cv_texto)} caracteres)")
    print("Derivando perfil con el LLM...\n")

    llm = LLMClient()  # usa el proveedor/modelo de config (Groq/Gemini)
    perfil = perfil_mod.derivar_perfil_desde_cv(cv_texto, llm)

    print("=" * 60)
    print(f"RUBRO:        {perfil['rubro']}")
    print(f"DESCRIPCIÓN:  {perfil['descripcion_rubro']}")
    print("KEYWORDS de búsqueda:")
    for k in perfil["keywords_busqueda"]:
        print(f"  - {k}")
    print("=" * 60)
    print("\nSi el rubro y las keywords calzan con el CV, la Fase 1 va bien.")
    print("Estas keywords reemplazarán a config.KEYWORDS por usuario.")


if __name__ == "__main__":
    main()