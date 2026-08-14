"""
Deriva el PERFIL laboral de una persona a partir de su CV, usando el LLM.
Reemplaza la configuración fija a "software": ahora el rubro y las keywords de
búsqueda salen del CV, así el proyecto sirve para cualquier carrera.

Salida (dict):
    {
      "rubro": "Desarrollo de software",
      "descripcion_rubro": "Puestos de desarrollo web, QA, datos...",
      "keywords_busqueda": ["desarrollador", "programador", "analista", ...],
    }
"""

from postulape.services.llm_client import LLMClient

SYSTEM_PERFIL = """Eres un analista de perfiles laborales. Te doy el CV de una
persona y devuelves SOLO un objeto JSON con estas claves:

- "rubro": el área o carrera principal del candidato, en 2 a 6 palabras
  (ej. "Desarrollo de software", "Contabilidad y finanzas", "Enfermería",
  "Marketing digital"). Básate SOLO en el CV; no inventes profesiones.
- "descripcion_rubro": 1-2 frases describiendo el tipo de puestos que le calzan.
- "keywords_busqueda": lista de 3 a 8 términos CORTOS (1-3 palabras), en
  minúsculas, tal como se escribirían en el buscador de un portal de empleos
  para encontrar esos puestos (ej. "desarrollador", "analista de datos",
  "soporte tecnico"). Sin tildes en las keywords.

Responde únicamente con el JSON, sin texto adicional."""


def derivar_perfil_desde_cv(cv_texto: str, llm=None) -> dict:
    """Deriva rubro + keywords del CV. Una sola llamada al LLM (cacheable)."""
    llm = llm or LLMClient()
    user = f"### CV DEL CANDIDATO\n{(cv_texto or '')[:6000]}\n\nDevuelve el JSON."
    try:
        data = llm.chat_json(SYSTEM_PERFIL, user)
        if not isinstance(data, dict):
            raise ValueError("El LLM no devolvió un objeto JSON")
    except Exception as e:
        print(f"[Perfil] Error derivando perfil: {e}")
        data = {}

    rubro = str(data.get("rubro") or "").strip()
    desc = str(data.get("descripcion_rubro") or "").strip()
    kws_raw = data.get("keywords_busqueda") or []
    if not isinstance(kws_raw, list):
        kws_raw = [kws_raw]
    keywords = []
    for k in kws_raw:
        k = str(k).strip().lower()
        if k and k not in keywords:
            keywords.append(k)

    return {
        "rubro": rubro or "(no detectado)",
        "descripcion_rubro": desc,
        "keywords_busqueda": keywords,
    }


def texto_perfil(perfil: dict) -> str:
    """Texto compacto del perfil para inyectar en el clasificador."""
    return f"{perfil.get('rubro', '')}. {perfil.get('descripcion_rubro', '')}".strip()
