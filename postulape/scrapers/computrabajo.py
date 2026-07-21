"""
Scraper de Computrabajo Perú (Playwright).

Estructura de URL de búsqueda (CONFIRMADA):
    https://pe.computrabajo.com/trabajo-de-{keyword-slug}-en-{ubicacion-slug}?pubdate={dias}
    - la ubicación va en el PATH ("-en-lima"), no en query
    - pubdate = antigüedad máxima en días (1, 3, 7, 15, 30)
    - paginación con &p={pagina}
Ej: https://pe.computrabajo.com/trabajo-de-practicante-en-lima?pubdate=7

Cada oferta tiene una URL de detalle con un ID hexadecimal al final:
    https://pe.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-...-XXXXXXXX

⚠️  Computrabajo usa Cloudflare. Corre con headless=False. Si te aparece un
    challenge ("verificando que eres humano"), déjalo resolver solo unos
    segundos o resuélvelo a mano una vez; luego suele dejar pasar.

⚠️  TODO SELECTORES: abre una búsqueda real, F12, y confirma estos selectores.
    Los de abajo son los patrones históricos de Computrabajo; si cambiaron,
    ajústalos (misma técnica que usaste para Bumerán).
"""

import re
import time
from playwright.sync_api import sync_playwright

from postulape.scrapers.base import slugify, separar_ubicacion


def construir_url(keyword: str, ubicacion: str = "lima", pubdate: int = 7, pagina: int = 1) -> str:
    kw = slugify(keyword)
    ubi = slugify(ubicacion)
    base = f"https://pe.computrabajo.com/trabajo-de-{kw}-en-{ubi}"
    params = f"?pubdate={pubdate}"
    if pagina > 1:
        params += f"&p={pagina}"
    return base + params


DEPARTAMENTOS_PE = {
    "amazonas", "ancash", "áncash", "apurimac", "apurímac", "arequipa", "ayacucho",
    "cajamarca", "callao", "cusco", "cuzco", "huancavelica", "huanuco", "huánuco",
    "ica", "junin", "junín", "la libertad", "lambayeque", "lima", "loreto",
    "madre de dios", "moquegua", "pasco", "piura", "puno", "san martin",
    "san martín", "tacna", "tumbes", "ucayali",
}


def _parece_ubicacion(texto: str) -> bool:
    """True si el texto luce como 'Distrito, Departamento' del Perú
    (p. ej. 'Santiago De Surco, Lima')."""
    if "," not in texto:
        return False
    ultimo = texto.split(",")[-1].strip().lower()
    return ultimo in DEPARTAMENTOS_PE


def extraer_avisos(page) -> list[dict]:
    avisos = []
    # Cada oferta es un <article> con clase que contiene "box_offer".
    tarjetas = page.query_selector_all("article.box_offer, article[class*='box_offer']")

    for tarjeta in tarjetas:
        # Título + link: <h2><a class="js-o-link" href="...">
        link_el = tarjeta.query_selector("h2 a, a.js-o-link, a[class*='js-o-link']")
        if not link_el:
            continue
        href = link_el.get_attribute("href") or ""
        titulo = link_el.inner_text().strip()

        m = re.search(r"-([A-F0-9]{16,})", href, re.IGNORECASE)
        job_id = m.group(1) if m else href.rsplit("/", 1)[-1]

        # Empresa: <a class="fc_base t_ellipsis"> (confirmado). El selector viejo
        # [class*='fs16'] a veces capturaba también la puntuación (ej. "4,3 ⭐").
        empresa_el = tarjeta.query_selector(
            "a.fc_base.t_ellipsis, a[class*='t_ellipsis'], p.dIB.fs16 a"
        )
        empresa = empresa_el.inner_text().strip() if empresa_el else None

        # Ubicación y modalidad. En vez de fiarnos de una clase (que cambia),
        # recorremos TODOS los <p>/<span> de la tarjeta y elegimos:
        #  - modalidad: el elemento que menciona remoto/híbrido/presencial
        #  - ubicacion: el elemento con forma de ubicación ("Distrito, Departamento",
        #    donde el departamento es uno del Perú)
        ubicacion = modalidad = None
        # Selector confirmado: la ubicación va en <span class="mr10"> con
        # formato "distrito, departamento" (ej. "Santiago De Surco, Lima").
        loc_el = tarjeta.query_selector("span.mr10")
        if loc_el:
            t = loc_el.inner_text().strip()
            if _parece_ubicacion(t):
                ubicacion = t

        # Modalidad (y ubicación como respaldo si el selector de arriba falla):
        # recorremos los <p>/<span> de la tarjeta.
        for p in tarjeta.query_selector_all("p, span"):
            texto = p.inner_text().strip()
            if not texto:
                continue
            tl = texto.lower()
            if modalidad is None and any(k in tl for k in ("remoto", "híbrido", "hibrido", "presencial")):
                modalidad = texto
                continue
            if ubicacion is None and _parece_ubicacion(texto):
                ubicacion = texto

        # Antigüedad: <p class="fc_aux ..."> "Hace X días"
        ant_el = tarjeta.query_selector("p.fc_aux, [class*='fc_aux']")
        antiguedad = ant_el.inner_text().strip() if ant_el else None

        # Descripción/snippet: <p class="mbB"> (confirmado).
        desc_el = tarjeta.query_selector("p.mbB, p[class*='mbB'], p.fs14")
        descripcion = desc_el.inner_text().strip() if desc_el else None

        distrito, departamento = separar_ubicacion(ubicacion)
        link = href if href.startswith("http") else f"https://pe.computrabajo.com{href}"

        avisos.append({
            "id": f"computrabajo-{job_id}",
            "plataforma": "Computrabajo",
            "titulo": titulo,
            "empresa": empresa,
            "ubicacion": ubicacion,
            "distrito": distrito,
            "departamento": departamento,
            "modalidad": modalidad,
            "antiguedad": antiguedad,
            "descripcion": descripcion,
            "link": link,
        })
    return avisos


def _extraer_descripcion_detalle(page):
    """Descripción COMPLETA desde la página de detalle del aviso.
    Contenedor confirmado: div.fs16.t_word_wrap (fallback: p.mbB).
    Nos quedamos con el texto más largo por si la clase se repite."""
    candidatos = page.query_selector_all(
        "div.fs16.t_word_wrap, div[class*='t_word_wrap'], p.mbB"
    )
    mejor, mejor_len = None, 0
    for c in candidatos:
        try:
            texto = c.inner_text().strip()
        except Exception:
            continue
        if len(texto) > mejor_len:
            mejor, mejor_len = texto, len(texto)
    return mejor


def _traer_descripcion(page, aviso, pausa_detalle):
    """Navega al detalle del aviso y rellena aviso['descripcion'] con el texto
    completo. Conserva lo que ya había si algo falla."""
    if not aviso.get("link"):
        return
    try:
        page.goto(aviso["link"], wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(
            "div.fs16.t_word_wrap, div[class*='t_word_wrap'], p.mbB", timeout=12000
        )
        desc = _extraer_descripcion_detalle(page)
        if desc:
            aviso["descripcion"] = desc
    except Exception as e:
        print(f"[Computrabajo] No se pudo traer descripción de {aviso['id']}: {e}")
    finally:
        time.sleep(pausa_detalle)


def traer_descripciones(avisos, headless=False, pausa_detalle=1.5):
    """ETAPA 3 del embudo: entra al detalle SOLO de la lista de avisos dada
    (los sobrevivientes) y les rellena la descripción. Un solo navegador."""
    if not avisos:
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0 Safari/537.36")
        )
        for i, a in enumerate(avisos, 1):
            print(f"[Computrabajo] Descripción {i}/{len(avisos)}: {a.get('titulo')}")
            _traer_descripcion(page, a, pausa_detalle)
        browser.close()


def scrapear_computrabajo(keyword, ubicacion="lima", headless=False, pausa=2.0,
                          max_paginas=200, pubdate=7, traer_descripcion=True, pausa_detalle=1.5):
    todos, ids_vistos = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0 Safari/537.36")
        )
        for pagina in range(1, max_paginas + 1):
            url = construir_url(keyword, ubicacion, pubdate, pagina)
            print(f"[Computrabajo] Página {pagina}: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"[Computrabajo] Error página {pagina}: {e}")
                break

            try:
                page.wait_for_selector("article[class*='box_offer']", timeout=15000)
            except Exception:
                print(f"[Computrabajo] Sin avisos en página {pagina} "
                      f"(¿Cloudflare o cambió el selector?).")
                break

            nuevos = [a for a in extraer_avisos(page) if a["id"] not in ids_vistos]
            if not nuevos:
                print(f"[Computrabajo] Sin avisos nuevos en página {pagina}, fin.")
                break
            for a in nuevos:
                ids_vistos.add(a["id"])
                todos.append(a)
            print(f"[Computrabajo] {len(nuevos)} nuevos en página {pagina}.")

            # Descripción completa desde el detalle de cada aviso nuevo.
            # (navega una vez por aviso: más lento, pero trae el texto completo)
            if traer_descripcion:
                for a in nuevos:
                    _traer_descripcion(page, a, pausa_detalle)

            time.sleep(pausa)
        browser.close()
    return todos