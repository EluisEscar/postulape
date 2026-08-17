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
SYSTEM_CLASIF = """Eres un filtro estricto de relevancia laboral. Recibes el
RUBRO/PERFIL de un candidato y una lista numerada de TÍTULOS. Selecciona solo los
títulos que justifiquen revisar la descripción completa para ESE candidato.

Evalúa lo que el TÍTULO declara sobre profesión y especialidad. No confundas
profesión con sector, cargo genérico ni herramienta: trabajar en el mismo sector
o usar el mismo software NO vuelve compatibles dos profesiones distintas.

ÁRBOL DE DECISIÓN — aplícalo en este orden a cada título:

1. ¿NOMBRA UNA PROFESIÓN O ESPECIALIDAD TÉCNICA AJENA al perfil?
   - Si NO nombra también la profesión del candidato: DESCARTA, sin aplicar la
     duda a favor. Compartir sector no lo rescata.
   - EXCEPCIÓN MIXTA: si presenta explícitamente la profesión del candidato como
     alternativa válida junto a otra ("Profesión A/Profesión B", "A o B"), NO
     descartes por la otra profesión; continúa al paso 2.
   - Una especialidad técnica ajena también se DESCARTA aunque use una
     herramienta que el candidato domina.

2. ¿NOMBRA LA PROFESIÓN DEL CANDIDATO, sola o en un título mixto, o un área
   específica claramente compatible con su perfil? INCLUYE.

3. ¿NO DECLARA profesión ni área técnica concreta y es un cargo genérico como
   "Analista", "Asistente", "Coordinador", "Practicante" o "Trainee"?
   INCLUYE: la descripción decidirá. Si sí declara un área concreta ajena,
   DESCARTA aunque comparta palabras genéricas como "supervisor" o "asistente".

ILUSTRACIÓN (no es una lista cerrada): si el perfil es Arquitectura,
"Ingeniero Civil" se descarta por profesión distinta; "Arquitecto/Ingeniero
Civil" se incluye por la excepción mixta; y "Modelador BIM de instalaciones
eléctricas/MEP" se descarta por especialidad ajena aunque use Revit. Del mismo
modo, obra o construcción como sector compartido no sustituyen la profesión.

Devuelve únicamente los índices relevantes."""


INTENTOS_CLASIFICACION = 2
ESTADO_ERROR_CLASIFICACION = "Pendiente de reintento (error clasificación)"


def clasificar_titulos(avisos: list, llm, perfil: str = "") -> tuple[set, set]:
    """Devuelve ``(relevantes, pendientes)`` procesando títulos en lotes.

    Cada lote se reintenta antes de marcarlo como pendiente. Los lotes fallidos
    no pasan a las etapas costosas y tampoco se confunden con descartes reales.
    """
    relevantes, pendientes = set(), set()
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
        numero_lote = i // tam + 1
        for intento in range(1, INTENTOS_CLASIFICACION + 1):
            try:
                data = llm.chat_json(SYSTEM_CLASIF, user)
                relevantes_lote = set()
                for idx in data.get("relevantes", []):
                    if isinstance(idx, int) and 0 <= idx < len(lote):
                        relevantes_lote.add(lote[idx]["id"])
                relevantes.update(relevantes_lote)
                break
            except Exception as e:
                if intento < INTENTOS_CLASIFICACION:
                    print(f"[Clasif] Error en lote {numero_lote}; reintentando: {e}")
                    continue
                pendientes.update(a["id"] for a in lote)
                print(f"[Clasif] Error persistente en lote {numero_lote}; "
                      f"queda pendiente: {e}")
    return relevantes, pendientes


def clasificar_titulos_en_lote(avisos: list, llm, perfil: str = "") -> set:
    """Wrapper compatible: devuelve solo relevantes y omite lotes pendientes."""
    relevantes, _pendientes = clasificar_titulos(avisos, llm, perfil)
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
