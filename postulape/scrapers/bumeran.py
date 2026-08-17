"""
Scraper de Bumerán Perú (Playwright).
Devuelve lista de dicts con el schema común de aviso.
"""

import re
import time
from playwright.sync_api import sync_playwright

from postulape.scrapers.base import slugify, separar_ubicacion


def construir_url(keyword: str, ubicacion: str, pagina: int = 1) -> str:
    kw = slugify(keyword)
    ubi = slugify(ubicacion)
    base = f"https://www.bumeran.com.pe/en-{ubi}/empleos-publicacion-menor-a-7-dias-busqueda-{kw}.html"
    return f"{base}?page={pagina}" if pagina > 1 else base


def extraer_avisos(page) -> list[dict]:
    avisos = []
    tarjetas = page.query_selector_all('a[href*="/empleos/"][target="_blank"]')

    for tarjeta in tarjetas:
        href = tarjeta.get_attribute("href")
        if not href:
            continue
        m = re.search(r"-(\d+)\.html$", href)
        if not m:
            continue
        job_id = m.group(1)

        titulo_el = tarjeta.query_selector("h2")
        titulo = titulo_el.inner_text().strip() if titulo_el else None

        antiguedad = empresa = None
        for h3 in tarjeta.query_selector_all("h3"):
            texto = h3.inner_text().strip()
            tl = texto.lower()
            if tl.startswith("publicado") or tl.startswith("actualizado"):
                antiguedad = texto
            elif empresa is None and texto:
                empresa = texto

        ubicacion = modalidad = None
        icono_ubi = tarjeta.query_selector('i[aria-label="Ubicación"]')
        if icono_ubi:
            cont = icono_ubi.evaluate_handle("el => el.parentElement").as_element()
            el = cont.query_selector("h3") if cont else None
            if el:
                ubicacion = el.inner_text().strip()

        icono_mod = tarjeta.query_selector('i[aria-label="Modalidad"]')
        if icono_mod:
            cont = icono_mod.evaluate_handle("el => el.parentElement").as_element()
            el = cont.query_selector("h3") if cont else None
            if el:
                modalidad = el.inner_text().strip()

        # Las clases de Bumerán son generadas y cambian entre listado y detalle.
        # El <p> más largo se conserva solo como snippet de respaldo.
        descripcion, mejor = None, 0
        for p in tarjeta.query_selector_all("p"):
            texto = p.inner_text().strip()
            if len(texto) > mejor:
                mejor, descripcion = len(texto), texto
        if mejor < 40:
            descripcion = None

        distrito, departamento = separar_ubicacion(ubicacion)
        link = href if href.startswith("http") else f"https://www.bumeran.com.pe{href}"

        avisos.append({
            "id": f"bumeran-{job_id}",
            "plataforma": "Bumeran",
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


def _traer_descripcion(page, aviso, pausa_detalle=1.5):
    """Trae la descripción completa y conserva el snippet si algo falla."""
    if not aviso.get("link"):
        return
    try:
        page.goto(aviso["link"], wait_until="domcontentloaded", timeout=30000)
        selectores = ("#descripcion-aviso", "#ficha-detalle", "#section-detalle")
        try:
            page.wait_for_selector(", ".join(selectores), timeout=12000)
        except Exception:
            # Igual probamos los selectores: el contenido puede existir aunque
            # Playwright haya agotado la espera por visibilidad.
            pass

        descripcion = None
        for selector in selectores:
            elemento = page.query_selector(selector)
            if not elemento:
                continue
            try:
                texto = elemento.inner_text().strip()
            except Exception:
                continue
            if len(texto) > 40:
                descripcion = texto
                break

        if descripcion:
            aviso["descripcion"] = descripcion
        else:
            print(f"[Bumeran] Sin descripción completa para {aviso.get('id')}; "
                  "se conserva el snippet.")
    except Exception as e:
        print(f"[Bumeran] No se pudo traer descripción de {aviso.get('id')}: {e}. "
              "Se conserva el snippet.")
    finally:
        time.sleep(pausa_detalle)


def traer_descripciones(avisos, headless=False, pausa_detalle=1.5):
    """ETAPA 3: completa solo los avisos sobrevivientes con un navegador."""
    if not avisos:
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            for i, a in enumerate(avisos, 1):
                print(f"[Bumeran] Descripción {i}/{len(avisos)}: {a.get('titulo')}")
                _traer_descripcion(page, a, pausa_detalle)
        finally:
            browser.close()


def scrapear_bumeran(keyword, ubicacion, headless=False, pausa=2.0, max_paginas=200):
    todos, ids_vistos = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        for pagina in range(1, max_paginas + 1):
            url = construir_url(keyword, ubicacion, pagina)
            print(f"[Bumeran] Página {pagina}: {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                print(f"[Bumeran] Error página {pagina}: {e}")
                break

            sel = 'a[href*="/empleos/"][target="_blank"]'
            try:
                page.wait_for_selector(sel, timeout=15000)
            except Exception:
                print(f"[Bumeran] Sin avisos en página {pagina}.")
                break

            nuevos = [a for a in extraer_avisos(page) if a["id"] not in ids_vistos]
            if not nuevos:
                print(f"[Bumeran] Sin avisos nuevos en página {pagina}, fin.")
                break
            for a in nuevos:
                ids_vistos.add(a["id"])
                todos.append(a)
            print(f"[Bumeran] {len(nuevos)} nuevos en página {pagina}.")
            time.sleep(pausa)
        browser.close()
    return todos
