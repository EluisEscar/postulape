"""
Matcher: decide si TÚ aplicas a un aviso, comparando tu CV con la oferta.
Devuelve por aviso: aplica (bool), score (0-100), motivo (str), keywords (list).

Incluye un pre-filtro barato por keywords (config.PREFILTRO_BLOCKLIST) que
descarta ofertas obviamente ajenas (contable, legal, ventas...) SIN gastar
tokens de LLM. Lo controlas con config.USAR_PREFILTRO.
"""

from postulape import config
from postulape.scrapers.base import quitar_tildes

import re


def _a_bool(v) -> bool:
    """Interpreta el 'aplica' del LLM aunque venga como texto ('false', 'no')."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("true", "1", "si", "sí", "yes", "aplica")


def _num_o_none(v):
    """Extrae un número de v (acepta 5, '5', '5 años', '2-3'). None si no hay."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
    return float(m.group(0)) if m else None


def _num_score(v) -> int:
    """Score 0-100 robusto: acepta 85, '85', '85/100'. Fuera de rango -> recorta."""
    n = _num_o_none(v)
    if n is None:
        return 0
    return max(0, min(100, int(n)))

SYSTEM = """Eres un reclutador senior. Decides, de forma honesta y realista, si
un candidato debería postular a una oferta, comparando su CV con la DESCRIPCIÓN
COMPLETA del puesto.

Cómo evaluar:
1. RUBRO: infiere el rubro del candidato a partir de su CV. Si el puesto es de un
   rubro CLARAMENTE distinto al del candidato, aplica=false, aunque el título diga
   "analista" o "asistente".
2. REQUISITOS DUROS: extrae de la descripción los requisitos OBLIGATORIOS
   (marcados como "requisito", "indispensable", "mínimo", "obligatorio", o
   claramente exigidos). Distingue de los DESEABLES ("deseable", "nice to have",
   "plus", "valorable"), que NO son excluyentes.
   - Años de experiencia: compara los años EXIGIDOS con los del candidato
     (te los indico abajo). Aplica la REGLA DE AÑOS que te doy en el mensaje.
     Nota: prácticas, trainee, "sin experiencia", "egresado", "junior" => el
     candidato SÍ califica en experiencia.
   - Habilidad, certificación o título obligatorio del puesto que el candidato
     claramente NO tiene y son núcleo del puesto => aplica=false.
3. Si cumple el rubro y no hay un requisito duro que lo bloquee, evalúa el encaje
   y pon un score realista.

Sé honesto: no infles el score para roles donde claramente no califica, ni lo
hundas por un "deseable" que no cumple."""

USER_TPL = """### CV DEL CANDIDATO
{cv}

### DATOS DEL CANDIDATO
- Años de experiencia profesional: ~{anios_exp}

### REGLA DE AÑOS
Si la oferta EXIGE (obligatorio, no "deseable") MÁS de {umbral_anios} años de
experiencia, entonces aplica=false por experiencia. Si pide {umbral_anios} o
menos, o si son años "deseables", NO descartes por experiencia.

### OFERTA
Título: {titulo}
Empresa: {empresa}
Ubicación: {ubicacion}
Modalidad: {modalidad}
Descripción: {descripcion}

Devuelve un JSON con EXACTAMENTE estas claves:
{{
  "aplica": true/false,
  "score": 0-100,
  "anios_requeridos": número o null,   // años de experiencia exigidos (null si no dice)
  "anios_candidato": número,           // años que estimas del CV
  "brechas": ["requisitos duros que el candidato NO cumple"],
  "motivo": "1-2 frases explicando la decisión (menciona la brecha si la hay)",
  "keywords": ["requisitos de la oferta que el candidato SÍ cumple"]
}}"""


def _tiene_senal_tecnica(titulo_norm: str) -> bool:
    """True si el título contiene una señal técnica (como PALABRA completa)."""
    for termino in config.TECH_ALLOWLIST:
        t = quitar_tildes(termino)
        if re.search(rf"\b{re.escape(t)}\b", titulo_norm):
            return True
    return False


def _prefiltro_descarta(aviso: dict) -> bool:
    """True si el TÍTULO cae en el blocklist y NO tiene señal técnica de rescate.
    Solo mira el título (no la descripción), para no banear roles tech cuya
    descripción mencione palabras genéricas como 'ventas' o 'seguridad'."""
    titulo = quitar_tildes(aviso.get("titulo", ""))
    if _tiene_senal_tecnica(titulo):
        return False  # rescatado por señal técnica
    return any(quitar_tildes(b) in titulo for b in config.PREFILTRO_BLOCKLIST)


def descartado_por_blocklist(aviso: dict) -> bool:
    """Etapa 1 del embudo: True si el título cae en el blocklist (0 tokens)."""
    return config.USAR_PREFILTRO and _prefiltro_descarta(aviso)


# ---------------------------------------------------------------------------
# ETAPA 2 — Clasificación de TÍTULOS en lote (1 llamada por lote de N títulos)
# ---------------------------------------------------------------------------
SYSTEM_CLASIF = """Eres un filtro de relevancia laboral. Te doy el RUBRO/PERFIL del
candidato y una lista numerada de TÍTULOS de ofertas. Devuelve los números de los
títulos que valga la pena revisar para ESE candidato.

INCLUYE:
- Títulos claramente del rubro del candidato.
- Títulos GENÉRICOS o ambiguos que NO declaran un rubro concreto (p. ej.
  "Analista" a secas, "Asistente", "Coordinador", "Practicante", "Trainee"):
  ante duda, inclúyelo; la descripción completa decidirá después.

DESCARTA:
- Títulos que pertenecen claramente a OTRO rubro distinto al del candidato.

Regla clave: si el título nombra un área concreta AJENA al rubro del candidato,
descártalo aunque comparta palabras como "analista" o "asistente". Solo trata
como dudoso lo que NO declara ningún rubro.

Responde solo con los índices."""


def clasificar_titulos_en_lote(avisos: list, llm, perfil: str = "") -> set:
    """Devuelve el conjunto de ids de avisos cuyo TÍTULO es plausiblemente
    relevante. Procesa en lotes de config.TAM_LOTE_TITULOS. Si un lote falla,
    lo incluye completo (fail-open: mejor no perder avisos)."""
    relevantes = set()
    perfil = perfil or "(rubro no especificado; trata los títulos ambiguos como dudosos)"
    tam = max(1, config.TAM_LOTE_TITULOS)

    for i in range(0, len(avisos), tam):
        lote = avisos[i:i + tam]
        listado = "\n".join(f"{j}. {a.get('titulo', '')}" for j, a in enumerate(lote))
        user = (
            f"### PERFIL DEL CANDIDATO\n{perfil}\n\n"
            f"### TÍTULOS\n{listado}\n\n"
            'Devuelve un JSON: {"relevantes": [lista de índices enteros relevantes]}'
        )
        try:
            data = llm.chat_json(SYSTEM_CLASIF, user)
            for idx in data.get("relevantes", []):
                if isinstance(idx, int) and 0 <= idx < len(lote):
                    relevantes.add(lote[idx]["id"])
        except Exception as e:
            # Fail-open: un problema temporal del clasificador no debe convertir
            # ofertas todavía no revisadas en descartes permanentes. El veredicto
            # individual posterior sigue decidiendo si realmente aplican.
            relevantes.update(a["id"] for a in lote)
            print(f"[Clasif] Error en lote {i // tam + 1}; se revisará completo: {e}")
    return relevantes


def evaluar(aviso: dict, cv_texto: str, llm, anios_experiencia=None, max_brecha_anios=None) -> dict:
    """Rellena aviso con aplica/score/motivo/keywords. Muta y devuelve el dict.
    anios_experiencia / max_brecha_anios: por usuario; si None usan config."""
    anios_experiencia = config.ANIOS_EXPERIENCIA if anios_experiencia is None else anios_experiencia
    max_brecha_anios = config.MAX_BRECHA_ANIOS if max_brecha_anios is None else max_brecha_anios
    if config.USAR_PREFILTRO and _prefiltro_descarta(aviso):
        aviso.update({
            "aplica": False, "score": 0,
            "motivo": "Descartado por pre-filtro (rubro ajeno a software).",
            "keywords": [], "estado": "No aplica: rubro ajeno",
        })
        return aviso

    user = USER_TPL.format(
        cv=cv_texto[:6000],
        anios_exp=anios_experiencia,
        umbral_anios=anios_experiencia + max_brecha_anios,
        titulo=aviso.get("titulo", ""),
        empresa=aviso.get("empresa", ""),
        ubicacion=aviso.get("ubicacion", ""),
        modalidad=aviso.get("modalidad", ""),
        descripcion=(aviso.get("descripcion") or "")[:4000],
    )
    try:
        data = llm.chat_json(SYSTEM, user)
        if not isinstance(data, dict):
            raise ValueError("El LLM no devolvió un objeto JSON")
    except Exception as e:
        aviso.update({"aplica": False, "score": 0,
                      "motivo": f"Error LLM: {e}", "keywords": [],
                      "estado": "Pendiente de reintento (error LLM)"})
        return aviso

    score = _num_score(data.get("score"))
    aplica = _a_bool(data.get("aplica")) and score >= config.UMBRAL_APLICA
    brechas_raw = data.get("brechas", []) or []
    brechas = ([str(b) for b in brechas_raw]
               if isinstance(brechas_raw, list) else [str(brechas_raw)])
    motivo = str(data.get("motivo") or "")
    keywords_raw = data.get("keywords", []) or []
    keywords = ([str(k) for k in keywords_raw]
                if isinstance(keywords_raw, list) else [str(keywords_raw)])

    # Red de seguridad determinista: aunque el LLM diga que aplica, si la oferta
    # exige claramente más años de los tolerados, se descarta.
    req = _num_o_none(data.get("anios_requeridos"))
    umbral = anios_experiencia + max_brecha_anios
    if req is not None and req > umbral:
        aplica = False
        nota = f"Requiere {req:g} años de experiencia (tienes ~{anios_experiencia})"
        if not any("año" in str(b).lower() or "experiencia" in str(b).lower() for b in brechas):
            brechas = [nota] + brechas
        if not motivo:
            motivo = nota

    # La columna 'estado' del Excel resume el veredicto (sin agregar columnas).
    if aplica:
        estado = f"APLICA ({score})"
    else:
        detalle = brechas[0] if brechas else (motivo[:60] if motivo else "no encaja")
        estado = f"No aplica ({score}): {detalle}"

    aviso.update({
        "aplica": aplica,
        "score": score,
        "motivo": motivo,
        "brechas": brechas,
        "anios_requeridos": data.get("anios_requeridos"),
        "anios_candidato": data.get("anios_candidato"),
        "keywords": keywords,
        "estado": estado,
    })
    return aviso
