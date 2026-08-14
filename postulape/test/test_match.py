"""
Prueba el MATCH POR USUARIO contra el pool (tabla jobs) ya scrapeado.
1. Deriva el perfil del CV base y lo guarda en 'profiles' (para DEFAULT_USER_ID).
2. Corre el match: clasifica + veredicto contra el pool y guarda en 'matches'.
3. Imprime el mensaje resultante (Caso A / Caso B / éxito).

Requisitos:
  - Haber corrido antes 'python -m postulape.scrape_pool' para llenar el pool.
  - DEFAULT_USER_ID en el .env = UUID de un usuario real en Supabase Auth
    (Supabase → Authentication → Users → Add user, copia su UUID).

Uso:
    python -m postulape.test.test_match
"""

from postulape import config
from postulape.services.llm_client import LLMClient
from postulape.services import perfil as perfil_mod
from postulape.services.match_usuario import correr_match
from postulape.storage import db


def main():
    uid = config.DEFAULT_USER_ID
    if not uid:
        print("Falta DEFAULT_USER_ID en el .env (UUID de un usuario de Supabase Auth).")
        return

    # 1) Derivar y guardar el perfil desde el CV base
    cv_texto = open(config.RUTA_CV_BASE, encoding="utf-8").read()
    llm_clasif = LLMClient(config.LLM_PROVIDER, config.LLM_MODEL_CLASIF)
    derivado = perfil_mod.derivar_perfil_desde_cv(cv_texto, llm_clasif)
    print(f"[Perfil] Rubro: {derivado['rubro']} | keywords: {derivado['keywords_busqueda']}")

    db.upsert_perfil(uid, {
        "cv_texto": cv_texto,
        "rubro": derivado["rubro"],
        "descripcion_rubro": derivado["descripcion_rubro"],
        "keywords_busqueda": derivado["keywords_busqueda"],
        "ubicacion": config.UBICACION,
        "modalidades": [],
        "anios_experiencia": config.ANIOS_EXPERIENCIA,
        "max_brecha_anios": config.MAX_BRECHA_ANIOS,
    })
    print("[Perfil] Guardado en 'profiles'.")

    # 2) Correr el match contra el pool
    print("\nCorriendo match contra el pool...\n")
    resultado = correr_match(uid, llm_clasif=llm_clasif)

    # 3) Mostrar resultado
    print("=" * 70)
    print(f"ESTADO:  {resultado['estado']}")
    print(f"MENSAJE: {resultado['mensaje']}")
    print(f"APLICAN: {resultado['aplican']}")
    print("=" * 70)

    if resultado["aplican"]:
        print("\nOfertas donde aplicas (top por score):")
        for m in db.matches_aplican(uid)[:10]:
            print(f"  score={m['score']}  {m['estado']}  (job {m['job_id']})")


if __name__ == "__main__":
    main()