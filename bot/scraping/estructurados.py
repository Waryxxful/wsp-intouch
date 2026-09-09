import logging
import re
from dataclasses import dataclass
from typing import Callable

from bs4 import BeautifulSoup

from .normalizar import _normalizar_clave

logger = logging.getLogger(__name__)

# Vocabulario CERRADO de categorias de sucursal. El slug se sigue DERIVANDO del
# label del sitio (no hay tabla de traduccion hardcodeada label->categoria),
# pero se VALIDA contra este set antes de guardarse. Un slug de vocabulario
# abierto no solo contamina los datos: ESCONDE la sucursal de los filtros
# reales del bot. El filtro de bot/business/catalogo.py incluye una sucursal si
# `not s.categorias or categoria in s.categorias`, asi que una categoria basura
# no-vacia (ej. "de_atencion", derivada de un label "Horario de atención:")
# deja la sede "categorizada pero sin match" para categoria="ventas" o
# "servicio_tecnico" -- PEOR que quedarse sin categorizar, caso en que seguiria
# siendo siempre visible. Soportar vocabulario nuevo del sitio = agregarlo aca.
_CATEGORIAS_CONOCIDAS = {"ventas", "servicio_tecnico"}

# Categoria por defecto: label generico "Horario:" sin sufijo (decision
# explicita del spec, justificada por los datos reales del sitio), o sufijo
# fuera de _CATEGORIAS_CONOCIDAS (fallback seguro: "siempre visible como
# ventas" es mejor que "invisible para todo filtro real").
_CATEGORIA_DEFAULT = "ventas"


@dataclass
class ExtractorEstructurado:
    nombre: str
    detectar: Callable[[BeautifulSoup], bool]
    parse: Callable[[BeautifulSoup], dict | None]


def _es_ficha_sucursal(soup: BeautifulSoup) -> bool:
    """Una pagina 'parece una ficha de sucursal' si tiene un <h1> Y un
    <p><strong> que empieza con "Direccion" Y al menos un <p><strong> que
    empieza con "Horario" -- los TRES, no cualquiera de ellos. Deteccion por
    PATRON DE CONTENIDO, no por clase CSS: el markup real de astararetail.cl
    es Elementor generico (elementor-widget-text-editor), la misma clase que
    usa cualquier bloque de texto en cualquier sitio Elementor, asi que un
    selector CSS daria falsos positivos masivos en un sitio Elementor futuro
    sin relacion con sucursales.

    Por que exige AMBOS labels y no "Direccion O Horario" (falso positivo real
    demostrado en la revision final del feature): una pagina de contacto
    corporativa cualquiera tiene <h1>Contacto</h1> + <p><strong>Dirección:
    </strong>... y con el criterio "O" se detectaba y parseaba en una fila
    Sucursal bogus. Y eso es peligroso porque este extractor corre contra
    TODAS las paginas de TODOS los clientes (no solo Astara), sin supervision
    (bot/scraping/scheduler.py), y la fila resultante es inmediatamente
    visible al cliente por las tools del bot (listar_catalogo,
    buscar_sucursales_cercanas). La senal que separa una sede real de una
    pagina generica es el horario: una sede fisica SIEMPRE publica su horario
    de atencion, una pagina de contacto/about generalmente no."""
    if soup.find("h1") is None:
        return False
    tiene_direccion = False
    tiene_horario = False
    for strong in soup.find_all("strong"):
        if strong.find_parent("p") is None:
            continue
        texto = strong.get_text(strip=True)
        if texto.startswith("Dirección"):
            tiene_direccion = True
        elif texto.startswith("Horario"):
            tiene_horario = True
    return tiene_direccion and tiene_horario


def _texto_de_parrafo_con_label(soup: BeautifulSoup, prefijo: str) -> list[tuple[str, str]]:
    """Devuelve [(label, resto), ...] para cada <p><strong>...</strong>...</p>
    cuyo <strong> empiece con `prefijo`. Usa get_text() sobre el <p> completo
    y le saca el texto del <strong> del principio -- no regex sobre el HTML
    crudo, para no romperse con markup anidado dentro del parrafo (caso real:
    <span lang="PT"> dentro de un <p> de direccion en
    /sucursal-rancagua-dyp/)."""
    resultados = []
    for strong in soup.find_all("strong"):
        label = strong.get_text(strip=True)
        if not label.startswith(prefijo):
            continue
        p = strong.find_parent("p")
        if p is None:
            continue
        texto_completo = re.sub(r"\s+", " ", p.get_text(separator=" ", strip=True))
        resto = texto_completo[len(label):] if texto_completo.startswith(label) else texto_completo
        resultados.append((label, resto.strip(" :")))
    return resultados


def _parsear_ficha_sucursal(soup: BeautifulSoup) -> dict | None:
    """Extrae nombre/direccion/horario_texto/categorias de una pagina ya
    confirmada como ficha de sucursal por _es_ficha_sucursal. Devuelve None
    si el markup no tiene lo minimo indispensable (h1 + parrafo de direccion +
    al menos un parrafo de horario) -- una pagina detectada pero sin poder
    parsearse no debe reventar el crawl, simplemente no aporta nada
    estructurado.

    Exige el parrafo de horario por el mismo motivo que _es_ficha_sucursal
    (ver su docstring): sin horario no hay evidencia de que sea una sede
    fisica, y este parser tambien es alcanzable directo (sin pasar por el
    detector) -- no debe poder inventar una Sucursal desde una pagina de
    contacto corporativa."""
    h1 = soup.find("h1")
    if h1 is None:
        return None
    nombre = h1.get_text(strip=True)
    direcciones = _texto_de_parrafo_con_label(soup, "Dirección")
    horarios = _texto_de_parrafo_con_label(soup, "Horario")
    if not nombre or not direcciones or not horarios:
        return None
    direccion = direcciones[0][1]

    categorias = []
    partes_horario = []
    for label, resto in horarios:
        partes_horario.append(f"{label} {resto}".strip())
        sufijo = label[len("Horario"):].strip(" :")
        if not sufijo:
            categorias.append(_CATEGORIA_DEFAULT)
            continue
        slug = _normalizar_clave(sufijo).replace(" ", "_")
        if slug in _CATEGORIAS_CONOCIDAS:
            categorias.append(slug)
            continue
        # Vocabulario nuevo en el sitio: no se guarda el slug derivado (ver
        # el comentario de _CATEGORIAS_CONOCIDAS -- una categoria basura
        # esconde la sede de todo filtro real). Se loguea el label CRUDO,
        # sin sluggear, para que un operador lo reconozca tal cual aparece
        # en la pagina y decida si extender el vocabulario.
        logger.warning(
            "[scraping] label de horario desconocido %r en la ficha de sucursal %r: "
            "el slug derivado %r no esta en %s, cae a %r -- extender "
            "_CATEGORIAS_CONOCIDAS si es una categoria real del sitio",
            label, nombre, slug, sorted(_CATEGORIAS_CONOCIDAS), _CATEGORIA_DEFAULT,
        )
        categorias.append(_CATEGORIA_DEFAULT)

    return {
        "nombre": nombre,
        "direccion": direccion,
        "horario_texto": " / ".join(partes_horario),
        # dict.fromkeys: dedupe preservando el orden. Dos parrafos "Horario
        # Ventas:" en la misma pagina (markup duplicado por el CMS) daban
        # ["ventas", "ventas"].
        "categorias": list(dict.fromkeys(categorias)),
    }


def _detectar_href_ficha_tecnica(soup: BeautifulSoup) -> str | None:
    """Busca un <a href> a un PDF cuyo texto de link o path del href sugiera
    que es la ficha tecnica del vehiculo (ej. "Ficha Técnica", o un href
    tipo /wp-content/uploads/.../ficha-outlander-phev.pdf). Devuelve el
    href CRUDO (relativo o absoluto, sin resolver contra la URL de la
    pagina -- eso lo hace el caller, que es quien tiene esa URL a mano).

    Senal INDEPENDIENTE, no parte de _REGISTRO/extraer_estructurados: una
    pagina de modelo de vehiculo no se "clasifica como" ficha tecnica (a
    diferencia de una ficha de sucursal, que es una interpretacion
    excluyente de toda la pagina) -- simplemente puede CONTENER un link a
    una, sin que eso cambie que tambien es (y sigue siendo) una pagina de
    modelo con precio/specs propios.

    Exige AMBAS cosas -- que el href termine en .pdf Y que el texto del
    link o el propio href contengan "ficha" (insensible a acentos/mayus,
    via _normalizar_clave) -- para no capturar cualquier PDF de la pagina
    sin relacion (ej. terminos y condiciones)."""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().endswith(".pdf"):
            continue
        texto_link = _normalizar_clave(a.get_text(strip=True))
        href_normalizado = _normalizar_clave(href)
        if "ficha" in texto_link or "ficha" in href_normalizado:
            return href
    return None


# Patrones de precio dentro del texto libre de #dDescripcion (astararetail.cl,
# seminuevos) -- ej. "Precio al contado: $42.990.000" / "Precio con
# financiamiento o vehiculo en parte de pago: $41.990.000". Devuelven el
# string crudo con simbolo/separadores (igual que el LLM ya entrega
# precio_contado/precio_financiado) para que runner.py los parsee con el
# mismo _parsear_entero que ya usa para todo el resto -- no se reimplementa
# ese parseo aca para no invertir la direccion de imports (estructurados.py
# esta por DEBAJO de crawler.py, que esta por debajo de runner.py).
_RE_PRECIO_CONTADO = re.compile(r"precio al contado\D*(\$[\d.,]+)", re.IGNORECASE)
_RE_PRECIO_FINANCIADO = re.compile(r"precio con financiamiento\D*?(\$[\d.,]+)", re.IGNORECASE)


def _detectar_datos_seminuevo(soup: BeautifulSoup) -> dict | None:
    """Extrae equipamiento + precio contado/financiado de una ficha de
    vehiculo usado (seminuevo) de astararetail.cl -- selectors reales
    confirmados yendo directo al HTML de una ficha real (no una version
    resumida), ver docs/PENDIENTES.md, seccion "[URGENTE] Astara --
    auditoría completa de catálogo", punto 5 (b). A diferencia de 0km
    (donde las specs viven en un PDF aparte), acá viven en la MISMA pagina,
    en dos bloques con id estable: #dCaracteristicas (<li> de equipamiento)
    y #dDescripcion (texto libre con motor/traccion en prosa + los 2
    precios). Exige AMBOS ids -- mismo criterio que _es_ficha_sucursal
    (dos senales, no una) para no confundir esto con cualquier otra pagina
    que por casualidad tenga alguno de los dos ids.

    Devuelve None si no matchea. Si matchea, "equipamiento" es siempre una
    lista (vacia si no hay <li>) y "precio_contado"/"precio_financiado" son
    el string crudo encontrado o None si el texto no sigue el patron
    esperado -- nunca inventa un precio, deja que runner.py decida no
    completar el campo en vez de arriesgar un valor mal parseado."""
    caracteristicas = soup.find(id="dCaracteristicas")
    descripcion_el = soup.find(id="dDescripcion")
    if caracteristicas is None or descripcion_el is None:
        return None
    # marca+modelo, ej. "ALFA ROMEO TONALE" -- es la clave con la que
    # runner.py matchea esta ficha contra la fila de VehiculoCatalogo.
    # NO se usa la URL de la pagina como clave: falla real del ScrapeRun 86
    # (2026-09-01), el LLM extrae los usados desde la pagina de LISTADO
    # (/seminuevos/seminuevos?promocion=...), asi que ninguna fila tiene
    # como url_fuente la URL de su ficha individual.
    marca_el = soup.find("h4", class_="marcaVehiculo")
    nombre = marca_el.get_text(strip=True) if marca_el else ""
    equipamiento = [
        li.get_text(strip=True) for li in caracteristicas.find_all("li") if li.get_text(strip=True)
    ]
    descripcion_texto = descripcion_el.get_text(separator="\n", strip=True)
    match_contado = _RE_PRECIO_CONTADO.search(descripcion_texto)
    match_financiado = _RE_PRECIO_FINANCIADO.search(descripcion_texto)
    return {
        "nombre": nombre,
        "equipamiento": equipamiento,
        "descripcion": descripcion_texto,
        "precio_contado": match_contado.group(1) if match_contado else None,
        "precio_financiado": match_financiado.group(1) if match_financiado else None,
    }


_REGISTRO: list[ExtractorEstructurado] = [
    ExtractorEstructurado(
        nombre="ficha_sucursal_individual",
        detectar=_es_ficha_sucursal,
        parse=_parsear_ficha_sucursal,
    ),
]


def extraer_estructurados(soup: BeautifulSoup) -> dict:
    """Corre cada extractor del registro contra soup (el primero que
    detecta esta pagina como suya, gana). Devuelve {"sucursales": [item]}
    si algo matcheo y pudo parsearse, {} si no. Generico a proposito: un
    extractor futuro (otro cliente, otro tipo de pagina) es una entrada mas
    en _REGISTRO, sin tocar esta funcion ni el resto del pipeline."""
    for extractor in _REGISTRO:
        if extractor.detectar(soup):
            item = extractor.parse(soup)
            if not item:
                return {}
            # Log obligatorio de todo resultado no vacio: este extractor corre
            # sin supervision (bot/scraping/scheduler.py) contra las paginas de
            # TODOS los clientes, y lo que produce se convierte en una fila
            # Sucursal visible al cliente por WhatsApp. Un falso positivo tiene
            # que ser visible en los logs de inmediato, no descubrirse porque
            # un cliente recibio una sucursal inventada.
            logger.info(
                "[scraping] extractor %r detecto una ficha de sucursal: "
                "nombre=%r direccion=%r categorias=%r",
                extractor.nombre, item.get("nombre"), item.get("direccion"),
                item.get("categorias"),
            )
            return {"sucursales": [item]}
    return {}
