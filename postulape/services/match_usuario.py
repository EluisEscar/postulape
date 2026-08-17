"""
Match por usuario (Fase 2): NO scrapea en vivo. Lee el pool (tabla jobs) ya
scrapeado, lo cruza con el perfil/CV del usuario y guarda el veredicto en matches.

Distingue dos casos "sin resultados" y devuelve mensajes claros:
  - Caso A (sin_ofertas_rubro): el pool aún no tiene avisos del rubro del usuario.
  - Caso B (sin_match): hay avisos del rubro, pero ninguno le calza hoy.

Uso desde código:
    from postulape.services.match_usuario import correr_match
    resultado = correr_match(user_id)
"""

import time

from postulape import config
from postulape.services import matcher
from postulape.services.llm_client import LLMClient
from postulape.storage import db
from postulape.scrapers import computrabajo as scr_computrabajo
from postulape.scrapers import indeed as scr_indeed


def _relevante_por_titulo(job: dict, keywords: list) -> bool:
    if not keywords:
        return True
    t = (job.get("titulo") or "").lower()
    return any(k in t for k in keywords)


def _completar_descripciones(candidatos: list):
    """Trae la descripción del detalle para los candidatos que no la tienen y
    la cachea en el pool (para no re-scrapearla en el próximo usuario)."""
    faltan = [c for c in candidatos if not (c.get("descripcion") or "").strip()]
    por_plat = {}
    for c in faltan:
        por_plat.setdefault((c.get("plataforma") or "").lower(), []).append(c)
    if por_plat.get("computrabajo"):
        scr_computrabajo.traer_descripciones(por_plat["computrabajo"], config.HEADLESS)
    if por_plat.get("indeed"):
        scr_indeed.traer_descripciones(por_plat["indeed"], config.HEADLESS)
    # cachear en el pool lo que se haya traído
    for c in faltan:
        if (c.get("descripcion") or "").strip():
            try:
                db.actualizar_descripcion(c["id"], c["descripcion"])
            except Exception as e:
                print(f"[Match] No se pudo cachear descripción de {c['id']}: {e}")


def correr_match(user_id: str, llm=None, llm_clasif=None) -> dict:
    perfil = db.get_perfil(user_id)
    if not perfil or not (perfil.get("cv_texto") or "").strip():
        return {"estado": "sin_perfil", "aplican": 0,
                "mensaje": "Primero sube tu CV para armar tu perfil y poder buscar."}

    pool = db.jobs_recientes(limit=500)
    if not pool:
        return {"estado": "pool_vacio", "aplican": 0,
                "mensaje": "Aún estamos recopilando ofertas del portal. Vuelve más tarde."}

    kws = [str(k).lower() for k in (perfil.get("keywords") or [])]
    del_rubro = [j for j in pool if _relevante_por_titulo(j, kws)]

    # ---- Caso A: el pool no tiene nada del rubro del usuario todavía --------
    if not del_rubro:
        rubro = perfil.get("rubro") or "tu área"
        return {"estado": "sin_ofertas_rubro", "aplican": 0,
                "mensaje": (f"Todavía no tenemos ofertas de {rubro} en el buscador. "
                            "Tus criterios ya quedaron guardados: aparecerán en la próxima "
                            "actualización. Vuelve más tarde.")}

    # Solo evaluamos los que aún no se evaluaron para este usuario.
    ya = db.ids_evaluados(user_id)
    por_evaluar = [j for j in del_rubro if j["id"] not in ya]

    if por_evaluar:
        llm = llm or LLMClient()
        llm_clasif = llm_clasif or LLMClient(
            config.LLM_PROVIDER_CLASIF, config.LLM_MODEL_CLASIF)
        perfil_str = f"{perfil.get('rubro', '')}. {perfil.get('descripcion_rubro', '')}".strip()

        # Etapa 2: clasificación de títulos según el rubro
        ids_rel, ids_pendientes = matcher.clasificar_titulos(
            por_evaluar, llm_clasif, perfil_str)
        candidatos = [j for j in por_evaluar if j["id"] in ids_rel]
        pendientes = [j for j in por_evaluar if j["id"] in ids_pendientes]
        descartados = [
            j for j in por_evaluar
            if j["id"] not in ids_rel and j["id"] not in ids_pendientes
        ]

        # Persistir la memoria del fallo antes de iniciar etapas costosas.
        for p in pendientes:
            p.update({
                "aplica": False,
                "score": 0,
                "motivo": "No se pudo clasificar el título tras los reintentos.",
                "estado": matcher.ESTADO_ERROR_CLASIFICACION,
            })
        if pendientes:
            db.guardar_matches(user_id, pendientes)

        # Etapa 3: descripción de los candidatos (y cache en el pool)
        if candidatos:
            _completar_descripciones(candidatos)

        # Etapa 4: veredicto contra el CV, con la experiencia del perfil
        cv = perfil.get("cv_texto", "")
        anios = perfil.get("anios_experiencia")
        brecha = perfil.get("max_brecha_anios")
        for i, a in enumerate(candidatos):
            matcher.evaluar(a, cv, llm, anios_experiencia=anios, max_brecha_anios=brecha)
            if i < len(candidatos) - 1:
                time.sleep(config.PAUSA_LLM_SEG)

        # Guardar candidatos, descartes reales y errores reintentables por separado.
        for d in descartados:
            d.update({"aplica": False, "score": 0,
                      "estado": "Descartado (título no encaja con el rubro)"})
        if candidatos or descartados:
            db.guardar_matches(user_id, candidatos + descartados)

    # ---- Resultado: Caso B o éxito ----------------------------------------
    aplican = db.matches_aplican(user_id)
    if not aplican:
        return {"estado": "sin_match", "aplican": 0,
                "mensaje": ("Revisamos las ofertas disponibles y hoy ninguna encaja con tu "
                            "perfil. Actualizamos el buscador a diario, así que vuelve pronto.")}
    return {"estado": "ok", "aplican": len(aplican),
            "mensaje": f"Encontramos {len(aplican)} oferta(s) que encajan con tu perfil."}
