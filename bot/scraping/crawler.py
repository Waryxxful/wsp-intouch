import concurrent.futures
import io
import ipaddress
import logging
import socket
import time
from urllib.parse import urljoin, urlparse

import pdfplumber
import requests
from bs4 import BeautifulSoup
from docx import Document
from openpyxl import load_workbook

from .estructurados import _detectar_datos_seminuevo, _detectar_href_ficha_tecnica, extraer_estructurados

logger = logging.getLogger(__name__)


class UrlInseguraError(ValueError):
    pass


# Identificacion honesta del bot -- el default de requests ("python-requests/x.x")
# es un patron obvio de script que varios WAFs bloquean o limitan de entrada,
# incluso para requests legitimos. No se hace pasar por un navegador real.
_HEADERS = {"User-Agent": "IntouchCRM-ScrapingBot/1.0 (uso interno: catalogo para bot de WhatsApp)"}

# Paths cuyos enlaces se priorizan en el BFS (se insertan al frente de la
# cola en vez de al final) -- caso real 2026-08-27: el scrape de prueba de
# astararetail.cl agoto max_pages=60 explorando fichas de vehiculos antes de
# llegar a /nuestras-sucursales/, dejando las 8 sucursales sin direccion (ver
# docs/PENDIENTES.md, seccion "Astara"). Generico a proposito (no especifico
# de un cliente): cualquier sitio de retail tipicamente enlaza su pagina de
# sucursales/ubicaciones desde el footer, que el BFS normal visita tarde.
_PATHS_PRIORITARIOS = ("sucursal", "tienda", "ubicac", "puntos-de-venta", "donde-estamos", "dondeestamos")

_CONTENT_TYPE_HTML = "text/html"
_CONTENT_TYPE_PDF = "application/pdf"
_CONTENT_TYPE_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_CONTENT_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CONTENT_TYPES_SOPORTADOS = (_CONTENT_TYPE_HTML, _CONTENT_TYPE_PDF, _CONTENT_TYPE_DOCX, _CONTENT_TYPE_XLSX)

# Los documentos (PDF/Word/Excel) suelen pesar mucho mas que una pagina HTML
# tipica por overhead binario/compresion (fichas tecnicas reales de
# renault.cl llegan a 9+ MB) -- max_page_bytes (pensado para HTML) los
# descartaria a todos. Los documentos usan este tope propio, mas generoso,
# en vez de max_page_bytes.
MAX_DOC_BYTES = 20 * 1024 * 1024

# Tope de caracteres del texto extraido de UN documento (PDF/Word/Excel).
# MAX_DOC_BYTES acota el tamano del ARCHIVO, pero no acota el texto que
# sale de parsearlo (un PDF/Word/Excel de hasta 20MB puede, en teoria,
# contener mucho mas texto plano que su tamano comprimido en disco) --
# esto acota la memoria y el tamano de fila en ScrapedPage.texto.
MAX_TEXTO_DOCUMENTO_CHARS = 1_000_000


def validar_url_segura(url: str) -> None:
    """Levanta UrlInseguraError si la URL no es segura para scrapear (SSRF).
    Se llama sobre CADA url que el crawler decide fetchear -- la semilla
    y cada enlace seguido, incluyendo redirects (ver _fetch en este mismo
    modulo). La URL la configura un admin autenticado, pero igual no hay
    que confiar ciegamente: un enlace interno del sitio podria apuntar a
    una IP privada por error o de forma maliciosa."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlInseguraError(f"esquema no permitido: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UrlInseguraError("URL sin host")
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as exc:
        raise UrlInseguraError(f"no se pudo resolver el host: {host}") from exc
    addr = ipaddress.ip_address(ip)
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
        raise UrlInseguraError(f"IP no permitida (privada/loopback/reservada): {ip}")


def _fetch(
    url: str, timeout: int, max_bytes: int, max_doc_bytes: int = MAX_DOC_BYTES, max_redirects: int = 5
) -> tuple[str, str, bytes]:
    """Descarga una URL validando SSRF en cada hop de redirect (no solo la
    URL inicial -- un 3xx podria apuntar a una IP privada). Devuelve
    (url_final, content_type, contenido_bytes). Corta si la respuesta excede
    el tope correspondiente segun el Content-Type: max_bytes para HTML
    (evita paginas gigantes / zip bombs) o max_doc_bytes para documentos
    (PDF/Word/Excel, que pesan mucho mas que una pagina HTML tipica); y
    descarta cualquier Content-Type que no este en _CONTENT_TYPES_SOPORTADOS."""
    for _ in range(max_redirects + 1):
        # ponytail: validar_url_segura() resuelve el host y valida esa IP, pero
        # requests.get() hace su propia resolucion DNS al conectar -- no hay
        # garantia de que sea la MISMA IP (gap TOCTOU/DNS-rebinding teorico).
        # Aceptado a proposito: la URL la configura un admin autenticado desde
        # el panel, nunca llega por WhatsApp. Si este crawler alguna vez acepta
        # URLs de una fuente no confiable, subir de nivel a resolver una vez,
        # pinear esa IP, y conectar directo a ella preservando el header Host.
        validar_url_segura(url)
        resp = requests.get(url, timeout=timeout, stream=True, allow_redirects=False, headers=_HEADERS)
        if resp.is_redirect and resp.next is not None:
            url = resp.next.url
            resp.close()
            continue
        if resp.status_code >= 400:
            resp.close()
            raise ValueError(f"respuesta HTTP {resp.status_code}: {url}")
        content_type = resp.headers.get("Content-Type", "")
        tipo = content_type.split(";")[0].strip().lower()
        if tipo not in _CONTENT_TYPES_SOPORTADOS:
            resp.close()
            raise ValueError(f"content-type no soportado ({content_type or 'vacio'}): {url}")
        limite = max_bytes if tipo == _CONTENT_TYPE_HTML else max_doc_bytes
        content = resp.raw.read(limite + 1, decode_content=True)
        resp.close()
        if len(content) > limite:
            raise ValueError(f"respuesta demasiado grande (> {limite} bytes): {url}")
        return url, content_type, content
    raise ValueError(f"demasiadas redirecciones siguiendo: {url}")


# Multiplicador aplicado al timeout por-operacion de _fetch para obtener el
# limite de tiempo de PARED de _fetch_con_limite. Da margen para conexiones
# lentas pero legitimas (varios hops de redirect, cada uno con su propio
# connect+read) sin dejar de acotar un cuelgue real a un tiempo finito.
_FETCH_LIMITE_TIEMPO_MULTIPLICADOR = 3


def _fetch_con_limite(url: str, timeout: int, max_bytes: int, max_doc_bytes: int) -> tuple[str, str, bytes]:
    """Envoltorio de _fetch() con un limite de tiempo de PARED aplicado desde
    afuera del socket. Bug real detectado en produccion (wsp_demo,
    renault.cl): la resolucion DNS (socket.gethostbyname en
    validar_url_segura, y la que hace requests.get() por su cuenta al
    conectar) puede colgarse sin avanzar (CPU 0%) -- el `timeout` que recibe
    requests.get() NO cubre esa fase porque ocurre ANTES de que exista un
    socket sobre el que fijar un timeout. Sin este limite externo, un
    cuelgue ahi deja el ScrapeRun en estado "corriendo" para siempre y
    requiere matar el proceso a mano (visto repetidas veces en produccion).
    Corre _fetch() en un thread aparte y lo abandona si excede el limite --
    Python no puede cancelar un thread bloqueado en una syscall, pero al
    menos el resto del crawl no queda rehen de esa unica pagina."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_fetch, url, timeout, max_bytes, max_doc_bytes)
    try:
        return future.result(timeout=timeout * _FETCH_LIMITE_TIEMPO_MULTIPLICADOR)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(
            f"fetch colgado (posible DNS/conexion sin avanzar) tras "
            f"{timeout * _FETCH_LIMITE_TIEMPO_MULTIPLICADOR}s: {url}"
        ) from None
    finally:
        executor.shutdown(wait=False)


_TAGS_ENCABEZADO = ("h1", "h2", "h3")
_TAGS_CONTENIDO = ("p", "li", "table", "h1", "h2", "h3")

# Piso de cobertura: si las secciones estructuradas capturan menos de esta
# fraccion del texto plano real de la pagina, _TAGS_CONTENIDO se quedo afuera
# de demasiado contenido (tipico de paginas armadas con <div>/<span> sueltos,
# sin p/li/table/h1-3 -- confirmado en renault.cl/concesionarios/: el texto
# real de las sucursales, 2897 chars via soup.get_text(), quedaba reducido a
# 141 chars de secciones). En ese caso es mas seguro caer al chunking plano
# de siempre (secciones=[]) que perder la mayoria del contenido real.
_COBERTURA_MINIMA_SECCIONES = 0.5


def _extraer_secciones(soup: BeautifulSoup) -> list[dict]:
    """Recorre los elementos de contenido en orden de documento y los agrupa
    por el encabezado (h1/h2/h3) que los precede. Una tabla se trata como
    contenido de la seccion activa (no rompe agrupamiento). El texto sin
    ningun encabezado antes (ej. el primer parrafo introductorio de la
    pagina) queda en una seccion con titulo=None al principio de la lista."""
    secciones: list[dict] = []
    actual: dict | None = None
    for el in soup.find_all(_TAGS_CONTENIDO):
        if el.name not in _TAGS_ENCABEZADO and el.find_parent(_TAGS_CONTENIDO) is not None:
            continue  # ya viene incluido en el texto de su ancestro
        if el.name in _TAGS_ENCABEZADO:
            titulo = el.get_text(separator=" ", strip=True)
            if not titulo:
                continue
            actual = {"titulo": titulo, "texto": ""}
            secciones.append(actual)
            continue
        texto_el = el.get_text(separator=" ", strip=True)
        if not texto_el:
            continue
        if actual is None:
            actual = {"titulo": None, "texto": ""}
            secciones.append(actual)
        actual["texto"] = f"{actual['texto']}\n\n{texto_el}" if actual["texto"] else texto_el
    return [s for s in secciones if s["texto"]]


def _extraer_texto_y_enlaces(url: str, html: bytes) -> tuple[str, list[str], list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    estructurados = extraer_estructurados(soup)
    href_ficha_tecnica = _detectar_href_ficha_tecnica(soup)
    if href_ficha_tecnica:
        # Mezclado en el mismo dict "estructurados" ya existente -- no se
        # agrega un 6to valor a la tupla de retorno de esta funcion (romperia
        # todos los callers/tests que ya desestructuran 5 valores). Es una
        # señal independiente de extraer_estructurados/_REGISTRO -- ver su
        # propio docstring en bot/scraping/estructurados.py.
        estructurados["ficha_tecnica_href"] = urljoin(url, href_ficha_tecnica)
    datos_seminuevo = _detectar_datos_seminuevo(soup)
    if datos_seminuevo is not None:
        estructurados["seminuevo"] = datos_seminuevo
    secciones = _extraer_secciones(soup)
    texto = "\n\n".join(f"{s['titulo']}\n\n{s['texto']}" if s["titulo"] else s["texto"] for s in secciones)
    texto_plano = soup.get_text(separator=" ", strip=True)
    if texto_plano and len(texto) < _COBERTURA_MINIMA_SECCIONES * len(texto_plano):
        logger.warning(
            "[scraping] %s: secciones capturan solo %d/%d chars (%.0f%%) -- cae a chunking plano sin categoria",
            url, len(texto), len(texto_plano), 100 * len(texto) / len(texto_plano),
        )
        texto = texto_plano
        secciones = []
    enlaces = []
    for a in soup.find_all("a", href=True):
        enlaces.append(urljoin(url, a["href"]))
    imagenes = []
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        if not src or src.startswith("data:"):
            continue
        imagenes.append({"url": urljoin(url, src), "alt": img.get("alt", "").strip()})
    return texto, enlaces, imagenes, secciones, estructurados


def _truncar_texto_documento(texto: str) -> str:
    if len(texto) <= MAX_TEXTO_DOCUMENTO_CHARS:
        return texto
    return texto[:MAX_TEXTO_DOCUMENTO_CHARS]


def _extraer_texto_pdf(contenido: bytes) -> str:
    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        partes = [pagina.extract_text() or "" for pagina in pdf.pages]
    texto = "\n".join(p for p in partes if p).strip()
    if not texto:
        raise ValueError("PDF sin texto extraible (posiblemente escaneado)")
    return _truncar_texto_documento(texto)


def _extraer_texto_docx(contenido: bytes) -> str:
    documento = Document(io.BytesIO(contenido))
    partes = [p.text for p in documento.paragraphs if p.text]
    for tabla in documento.tables:
        for fila in tabla.rows:
            for celda in fila.cells:
                if celda.text:
                    partes.append(celda.text)
    texto = "\n".join(partes).strip()
    if not texto:
        raise ValueError("documento Word sin texto extraible")
    return _truncar_texto_documento(texto)


def _extraer_texto_xlsx(contenido: bytes) -> str:
    workbook = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        partes = []
        for hoja in workbook.worksheets:
            for fila in hoja.iter_rows(values_only=True):
                for valor in fila:
                    if valor is not None:
                        partes.append(str(valor))
    finally:
        workbook.close()
    texto = "\n".join(partes).strip()
    if not texto:
        raise ValueError("archivo Excel sin datos extraibles")
    return _truncar_texto_documento(texto)


def _extraer_texto(url: str, content_type: str, contenido: bytes) -> tuple[str, list[str], list[dict], list[dict], bool, dict]:
    """Despacha la extraccion de texto segun el Content-Type ya validado por
    _fetch. Solo la rama HTML descubre enlaces, imagenes y estructurados
    nuevos -- los documentos (PDF/Word/Excel) no aportan URLs, imagenes ni
    fichas estructuradas. El quinto valor (es_documento) distingue HTML de
    documento -- lo necesita bot/rag/indexador.py para decidir si un
    secciones=[] es "documento sin estructura HTML aprovechable" (True) o
    "HTML de baja cobertura de secciones" (False), casos que requieren
    chunking distinto. El sexto valor (estructurados) lo consume
    bot/scraping/runner.py para el upsert de categorias de sucursal."""
    tipo = content_type.split(";")[0].strip().lower()
    if tipo == _CONTENT_TYPE_HTML:
        texto, enlaces, imagenes, secciones, estructurados = _extraer_texto_y_enlaces(url, contenido)
        return texto, enlaces, imagenes, secciones, False, estructurados
    if tipo == _CONTENT_TYPE_PDF:
        return _extraer_texto_pdf(contenido), [], [], [], True, {}
    if tipo == _CONTENT_TYPE_DOCX:
        return _extraer_texto_docx(contenido), [], [], [], True, {}
    if tipo == _CONTENT_TYPE_XLSX:
        return _extraer_texto_xlsx(contenido), [], [], [], True, {}
    raise ValueError(f"content-type no soportado ({content_type}): {url}")


def _forzar_esquema(url: str, esquema: str) -> str:
    """Normaliza el esquema de una URL descubierta al de la semilla del
    crawl. Bug real detectado en produccion (renault.cl): el sitio migro a
    https pero su propio HTML tiene enlaces internos con el esquema viejo
    (http), incluidos adjuntos PDF -- el sitio real solo responde en el
    esquema nuevo, asi que esos enlaces legitimos 404-eaban antes de
    siquiera intentar fetchearse con el content-type/tamano correctos."""
    partes = urlparse(url)
    if partes.scheme == esquema:
        return url
    return partes._replace(scheme=esquema).geturl()


def _sin_fragmento(url: str) -> str:
    """El fragmento de una URL (#seccion) nunca se transmite al servidor
    (es puramente client-side) -- normalizarlo antes de decidir si una URL
    ya fue visitada es siempre seguro, nunca puede cambiar que contenido
    se trae. Bug real detectado en revision manual del RAG (renault.cl):
    "/garantia/" y "/garantia/#masthead" quedaban scrapeadas e indexadas
    dos veces, duplicando cada resultado de busqueda."""
    return urlparse(url)._replace(fragment="").geturl()


def _es_url_prioritaria(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(palabra in path for palabra in _PATHS_PRIORITARIOS)


def crawl(
    seed_url: str,
    max_depth: int = 4,
    max_pages: int = 120,
    timeout: int = 10,
    max_page_bytes: int = 2 * 1024 * 1024,
    max_doc_bytes: int = MAX_DOC_BYTES,
    delay: float = 0.5,
) -> tuple[list[dict], list[dict]]:
    """BFS mismo-dominio a partir de seed_url. Devuelve (resultados, errores):
    resultados es una lista de {"url": ..., "texto": ..., "imagenes": ...,
    "secciones": ...} (maximo max_pages entradas, sin bajar de profundidad
    max_depth, semilla=profundidad 0); errores es una lista de
    {"url": ..., "error": ...} con las paginas
    individuales descartadas (timeout/404/tamano/enlace inseguro) sin
    abortar el resto de la corrida. max_page_bytes acota el tamano de
    paginas HTML; max_doc_bytes acota el tamano de documentos (PDF/Word/
    Excel), que pesan mucho mas que una pagina HTML tipica. Los enlaces
    cuyo path matchea _PATHS_PRIORITARIOS (sucursales/ubicaciones) se
    visitan antes que el resto -- ver comentario en la constante."""
    seed_url = _sin_fragmento(seed_url)
    seed_domain = urlparse(seed_url).netloc
    seed_scheme = urlparse(seed_url).scheme
    visitadas: set[str] = set()
    resultados: list[dict] = []
    errores: list[dict] = []
    # Dos colas FIFO separadas -- NUNCA "prioritarios + cola + normales": eso
    # inserta cada tanda nueva de prioritarios AL FRENTE de los prioritarios
    # ya encolados (LIFO), no solo por delante de los normales. Bug real
    # detectado el 2026-08-27 corriendo esto contra astararetail.cl: la
    # semilla descubre /nuestras-sucursales/ (prioritaria) en la primera
    # pagina, pero el sitio tambien tiene 14 paginas /sucursal-X/ que
    # matchean la misma prioridad -- cada una descubierta en una pagina
    # posterior se colaba por delante de /nuestras-sucursales/, que nunca
    # llego a visitarse dentro del cupo de max_pages. Con colas FIFO
    # separadas (se vacia la prioritaria antes de tocar la normal, y cada
    # cola respeta el orden en que sus items se descubrieron) esto no puede
    # pasar: un prioritario descubierto antes siempre se visita antes que
    # uno descubierto despues.
    cola_prioritaria: list[tuple[str, int]] = []
    cola_normal: list[tuple[str, int]] = [(seed_url, 0)]
    # Fichas tecnicas (PDF) descubiertas via estructurados["ficha_tecnica_href"]
    # -- lista APARTE de resultados, fusionada al final (ver mas abajo). Si se
    # agregaran directo a resultados, len(resultados) crecería con cada una y
    # el chequeo "len(resultados) < max_pages" del while de mas abajo cerraria
    # el crawl antes de tiempo -- exactamente el presupuesto compartido que
    # este mecanismo existe para evitar (docs/PENDIENTES.md, seccion
    # "[URGENTE] Astara -- auditoría completa de catálogo", punto 5: 293 PDFs
    # compitiendo por el mismo max_pages que las 293 paginas de modelo).
    documentos_directos: list[dict] = []

    while (cola_prioritaria or cola_normal) and len(resultados) < max_pages:
        url, profundidad = (cola_prioritaria or cola_normal).pop(0)
        url = _sin_fragmento(url)
        if url in visitadas:
            continue
        visitadas.add(url)

        try:
            url_final, content_type, contenido = _fetch_con_limite(
                url, timeout=timeout, max_bytes=max_page_bytes, max_doc_bytes=max_doc_bytes
            )
            url_final = _sin_fragmento(url_final)
            visitadas.add(url_final)
            texto, enlaces, imagenes, secciones, es_documento, estructurados = _extraer_texto(url_final, content_type, contenido)
        except Exception as exc:
            if isinstance(exc, UrlInseguraError) and url == seed_url:
                raise
            errores.append({"url": url, "error": str(exc)})
            if (cola_prioritaria or cola_normal) and len(resultados) < max_pages:
                time.sleep(delay)
            continue

        resultados.append({
            "url": url_final, "texto": texto, "imagenes": imagenes,
            "secciones": secciones, "es_documento": es_documento,
            "estructurados": estructurados,
        })

        href_ficha_tecnica = estructurados.get("ficha_tecnica_href")
        if href_ficha_tecnica:
            # Mismo par de normalizaciones que ya se aplica a cualquier
            # enlace descubierto (ver el "for enlace in enlaces" de mas
            # abajo): _forzar_esquema por el mismo bug real documentado ahi
            # (renault.cl migro a https pero sus propios adjuntos PDF
            # quedaron linkeados en http, que 404-eaba) -- el caso
            # EXACTO que motiva esa funcion es "incluidos adjuntos PDF".
            href_ficha_tecnica = _sin_fragmento(_forzar_esquema(href_ficha_tecnica, seed_scheme))
        if href_ficha_tecnica and href_ficha_tecnica not in visitadas:
            # Fetch directo, sin pasar por ninguna cola: se conoce la URL
            # exacta apenas se parsea la pagina de modelo, asi que no hace
            # falta descubrirla "de nuevo" via el BFS. visitadas.add() ANTES
            # del for de enlaces de abajo evita que el mismo href tambien se
            # encole como un enlace normal mas (se marca ahora, se procese
            # bien o mal el fetch -- un reintento infinito de una ficha rota
            # en cada pagina que la linkea seria peor que perderse esa una).
            visitadas.add(href_ficha_tecnica)
            # A diferencia del "for enlace in enlaces" de mas abajo, este
            # fetch NO se restringe a seed_domain a proposito: una ficha
            # tecnica real puede vivir en un CDN de assets en otro dominio
            # (ej. wp-content de un subdominio distinto). _fetch_con_limite
            # sigue validando SSRF (validar_url_segura) igual que cualquier
            # otro fetch, asi que esto no relaja seguridad, solo el filtro
            # de dominio que existe para no salirse del sitio en el BFS
            # normal -- irrelevante aca porque no hay BFS, es un fetch
            # dirigido y unico.
            try:
                pdf_url_final, pdf_content_type, pdf_contenido = _fetch_con_limite(
                    href_ficha_tecnica, timeout=timeout, max_bytes=max_page_bytes, max_doc_bytes=max_doc_bytes
                )
                pdf_texto, _, _, _, pdf_es_documento, _ = _extraer_texto(pdf_url_final, pdf_content_type, pdf_contenido)
                documentos_directos.append({
                    "url": pdf_url_final, "texto": pdf_texto, "imagenes": [],
                    "secciones": [], "es_documento": pdf_es_documento,
                    "estructurados": {}, "ficha_tecnica_de": url_final,
                })
            except Exception as exc:
                errores.append({"url": href_ficha_tecnica, "error": str(exc)})

        if profundidad < max_depth:
            for enlace in enlaces:
                if urlparse(enlace).netloc != seed_domain:
                    continue
                enlace = _sin_fragmento(_forzar_esquema(enlace, seed_scheme))
                if enlace in visitadas:
                    continue
                item = (enlace, profundidad + 1)
                if _es_url_prioritaria(enlace):
                    cola_prioritaria.append(item)
                else:
                    cola_normal.append(item)

        if (cola_prioritaria or cola_normal) and len(resultados) < max_pages:
            time.sleep(delay)

    return resultados + documentos_directos, errores
