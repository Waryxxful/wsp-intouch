import logging
import re
from datetime import timedelta

from django.utils import timezone

from bot.models import ScrapedPage, ScrapeRun, ScrapingSource, Servicio, Sucursal, VehiculoCatalogo

from .crawler import crawl
from .extractor import extract_catalog, _normalizar_clave
from .normalizar import _normalizar_direccion
from bot.rag.indexador import borrar_chunks_de_paginas_purgadas, indexar_pagina_en_supabase

logger = logging.getLogger(__name__)

# Un run "corriendo" mas viejo que esto se trata como muerto/colgado, no como
# genuinamente en curso -- mismo umbral que usa bot/scraping/scheduler.py para
# decidir si arranca un run nuevo pese a que el anterior nunca termino. Vive
# aca (no en scheduler.py) porque execute_scrape lo necesita para no proteger
# para siempre las ScrapedPage de un run que quedo colgado sin terminar nunca
# (crash del proceso a mitad de un scrape, sin que nada lo marque "error")
# (ver docs/PENDIENTES.md); scheduler.py lo importa de aca.
_CORRIENDO_STALE_MINUTOS = 60


def start_scrape(source: ScrapingSource) -> ScrapeRun:
    """Crea el registro ScrapeRun en estado 'corriendo' y devuelve de
    inmediato. Separado de execute_scrape() para que la vista del panel
    pueda responder al operador sin esperar a que termine el crawl+
    extraccion (que puede tardar varios segundos) -- ver
    admin_panel/views.py::api_scraping_source_run."""
    return ScrapeRun.objects.create(source=source, url=source.url, estado="corriendo")


def execute_scrape(run: ScrapeRun) -> None:
    """Hace el trabajo pesado (crawl + extraccion + upsert) sobre un
    ScrapeRun ya creado. Pensado para correr en un thread separado (vista
    del panel) o de forma sincronica en el hilo principal (run_scrape,
    usado por el management command y por los tests de este archivo)."""
    try:
        # source puede ser None (ScrapeRun.source es nullable) -- todo el
        # catalogo historico anterior al campo `cliente` es renault, asi que
        # ese es el default seguro para preservar el comportamiento de
        # siempre. Resuelto ARRIBA de todo (no despues del bloque de RAG,
        # como antes) -- bug real 2026-08-27/28 (docs/PENDIENTES.md):
        # indexar_pagina_en_supabase/borrar_chunks_de_paginas_purgadas se
        # llamaban sin cliente, dependiendo en silencio de RAG_SCHEMA (env
        # var global de proceso) -- un scrape de Astara con el proceso
        # todavia sirviendo Renault mando 297 chunks al schema renault.
        cliente = run.source.cliente if run.source_id else "renault"
        paginas, errores = crawl(run.url)
        # Aplana TODOS los items de TODAS las paginas, no solo el primero de
        # cada una: extraer_estructurados devuelve una LISTA a proposito para
        # que un extractor futuro (ej. uno que parsee una pagina-listado con
        # varias sedes) pueda aportar mas de un item sin tocar el pipeline
        # (ver el spec de este feature). Con un `[0]` hardcodeado los items
        # 2..n de ese extractor se perderian en silencio, sin error ni log.
        estructurados_sucursales = [
            item
            for p in paginas
            for item in p.get("estructurados", {}).get("sucursales", [])
        ]
        # url_pagina_modelo -> url_pdf_ficha_tecnica, para las paginas que
        # crawl() (bot/scraping/crawler.py) fetcheo directo por tener un link
        # a ficha tecnica -- ver docs/PENDIENTES.md, seccion "[URGENTE]
        # Astara -- auditoría completa de catálogo", punto 5. No hay un
        # campo dedicado en VehiculoCatalogo: el sistema ya trata
        # url_fuente terminado en .pdf como "hay ficha real" (ver
        # bot/business/ventas.py::_resolver_ficha_tecnica_url), asi que
        # _upsert_catalogo usa este mapeo para preferir el PDF sobre la
        # pagina HTML al guardar url_fuente.
        fichas_tecnicas_por_pagina = {
            p["ficha_tecnica_de"]: p["url"] for p in paginas if p.get("ficha_tecnica_de")
        }
        # nombre normalizado del vehiculo -> datos deterministicos de
        # equipamiento/precios extraidos de #dCaracteristicas/#dDescripcion
        # (seminuevos, bot/scraping/estructurados.py::_detectar_datos_seminuevo)
        # -- ver docs/PENDIENTES.md, seccion "[URGENTE] Astara -- auditoría
        # completa de catálogo", punto 5 (b). _upsert_catalogo los usa para
        # COMPLETAR lo que el LLM haya dejado vacio, nunca para reemplazar
        # un valor que el LLM ya trajo.
        #
        # La clave es el NOMBRE, no la URL de la pagina: falla real del
        # ScrapeRun 86 (2026-09-01, quedo en 0 equipamiento) -- cada unidad
        # usada tiene su ficha en /seminuevos/.../ficha/<id>, pero el LLM
        # extrae los vehiculos desde la pagina de LISTADO, asi que ninguna
        # fila de VehiculoCatalogo tiene como url_fuente la URL de su
        # propia ficha. Con la URL como clave el join nunca matcheaba.
        datos_seminuevos_por_nombre = {
            _normalizar_clave(p["estructurados"]["seminuevo"]["nombre"]): p["estructurados"]["seminuevo"]
            for p in paginas
            if p.get("estructurados", {}).get("seminuevo", {}).get("nombre")
        }
        # Purga las ScrapedPage de runs ANTERIORES del mismo source antes de
        # crear las nuevas -- sin esto, cada re-scrape apilaba un run encima
        # del anterior sin borrar nada, y el mismo #fragmento terminaba
        # duplicado 2-3 veces en Supabase (bug real del rollout 2026-08-18,
        # 46 de 114 filas). Solo si crawl() encontro contenido nuevo: un
        # error no-SSRF en la pagina semilla (timeout, 5xx, tamano excedido,
        # etc.) NO hace que crawl() levante -- devuelve (resultados=[],
        # errores=[...]) normalmente (ver bot/scraping/crawler.py) -- y
        # purgar en ese caso dejaria el RAG mas vacio que antes en vez de
        # protegido.
        if run.source_id is not None and paginas:
            # exclude(run__estado="corriendo", run__started_at__gte=limite_stale):
            # protege las paginas de un run hermano SOLO si sigue genuinamente en
            # curso (arranco hace menos de _CORRIENDO_STALE_MINUTOS) -- ver el
            # comentario de start_scheduler en bot/scraping/scheduler.py sobre la
            # ventana de doble-tick y el camino mas ancho de _tick_source
            # reintentando tras ese umbral si el primero sigue vivo. Un run que se
            # colgo para siempre en "corriendo" (crash del proceso, sin nada que lo
            # marque "error") ya no queda protegido eternamente: pasado el umbral se
            # trata igual que cualquier otro run viejo.
            limite_stale = timezone.now() - timedelta(minutes=_CORRIENDO_STALE_MINUTOS)
            paginas_a_purgar = ScrapedPage.objects.filter(
                run__source_id=run.source_id
            ).exclude(run=run).exclude(run__estado="corriendo", run__started_at__gte=limite_stale)
            ids_purgados = list(paginas_a_purgar.values_list("id", flat=True))
            paginas_a_purgar.delete()
            if ids_purgados:
                # Borra tambien los chunks de Supabase de esas paginas purgadas --
                # sin esto quedaban huerfanos hasta que alguien corria
                # reindexar_conocimiento_rag a mano (ver docs/PENDIENTES.md).
                borrar_chunks_de_paginas_purgadas(ids_purgados, cliente=cliente)
        for p in paginas:
            pagina = ScrapedPage.objects.create(
                run=run, url=p["url"], texto=p["texto"],
                imagenes=p.get("imagenes", []), secciones=p.get("secciones", []),
                es_documento=p.get("es_documento", False),
            )
            indexar_pagina_en_supabase(pagina, cliente=cliente)
        run.paginas_procesadas = len(paginas)
        run.paginas_con_error = errores
        catalogo = extract_catalog(paginas)
        run.catalogo_extraido = catalogo
        _upsert_catalogo(
            catalogo, cliente=cliente, estructurados=estructurados_sucursales,
            fichas_tecnicas=fichas_tecnicas_por_pagina,
            datos_seminuevos=datos_seminuevos_por_nombre,
        )
        run.estado = "ok"
    except Exception as exc:
        logger.warning("[scraping] run %s fallo: %s", run.pk, exc)
        run.estado = "error"
        run.error_detalle = str(exc)
    run.finished_at = timezone.now()
    run.save()


def run_scrape(source: ScrapingSource) -> ScrapeRun:
    """Punto de entrada sincronico: crea el run y lo ejecuta de inmediato
    en el mismo hilo. Usado por el management command
    (scrape_business_site), por el scheduler y por los tests de este
    archivo."""
    run = start_scrape(source)
    execute_scrape(run)
    return run


_GRATIS = ("gratis", "gratuito", "gratuita")


def _parsear_entero(valor, campo: str, nombre: str) -> int | None:
    """Convierte un valor semi-libre del LLM (numero, string con simbolos de
    moneda/separadores de miles, o texto tipo "gratuito") a un entero para
    guardar en un campo numerico del modelo. Nunca lanza: un valor no
    interpretable se descarta (None) y se loguea, en vez de abortar todo el
    upsert del catalogo -- y con el toda la corrida -- por un solo campo con
    formato inesperado (ej. Servicio.precio es DecimalField y Django lanza
    al intentar guardar un string como "gratuito" o "$11.990.000")."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return int(valor)
    texto = str(valor).strip().lower()
    if texto in _GRATIS:
        return 0
    solo_digitos = re.sub(r"[^\d]", "", texto)
    if not solo_digitos:
        logger.warning("[scraping] %s=%r del servicio %r no es interpretable, se omite", campo, valor, nombre)
        return None
    return int(solo_digitos)


def _upsert_catalogo(
    catalogo: dict, cliente: str, estructurados: list[dict] | None = None,
    fichas_tecnicas: dict[str, str] | None = None,
    datos_seminuevos: dict[str, dict] | None = None,
) -> None:
    """Crea o actualiza Servicio/Sucursal por nombre normalizado (ver
    extractor._normalizar_clave: insensible a mayusculas/acentos/puntuacion),
    escopeado al `cliente` de la fuente que genero este catalogo -- nunca
    matchea ni actualiza filas de otro cliente, aunque el nombre normalizado
    coincida (por eso usa el manager `todos_los_clientes` con un filtro
    explicito por `cliente` en vez de `objects`, que filtra por
    settings.CLIENTE_ACTIVO y podria no ser el cliente de esta fuente).
    Las sucursales ademas matchean por direccion normalizada cuando esta
    disponible -- el LLM nombra la misma sucursal de forma distinta entre
    corridas (con/sin prefijo de marca, con/sin acentos), pero la direccion
    es una senal mas confiable de que es el mismo lugar. Nunca borra
    registros que el sitio ya no mencione -- un scrape parcial no debe
    destruir datos cargados a mano."""
    indice_servicios = {
        _normalizar_clave(s.nombre): s for s in Servicio.todos_los_clientes.filter(cliente=cliente)
    }
    for item in catalogo.get("servicios", []):
        nombre = (item.get("nombre") or "").strip()
        if not nombre:
            continue
        duracion = _parsear_entero(item.get("duracion_min"), "duracion_min", nombre)
        precio = _parsear_entero(item.get("precio"), "precio", nombre)
        campos = {}
        if duracion is not None:
            campos["duracion_min"] = duracion
        if precio is not None:
            campos["precio"] = precio
        clave = _normalizar_clave(nombre)
        obj = indice_servicios.get(clave)
        if obj is None:
            obj = Servicio.todos_los_clientes.create(nombre=nombre, cliente=cliente, **campos)
            indice_servicios[clave] = obj
        elif campos:
            for campo, valor in campos.items():
                setattr(obj, campo, valor)
            obj.save(update_fields=list(campos.keys()))

    indice_sucursales = {}
    indice_direcciones = {}
    for s in Sucursal.todos_los_clientes.filter(cliente=cliente):
        indice_sucursales[_normalizar_clave(s.nombre)] = s
        if s.direccion:
            indice_direcciones[_normalizar_clave(s.direccion)] = s

    for item in catalogo.get("sucursales", []):
        nombre = (item.get("nombre") or "").strip()[:200]
        if not nombre:
            continue
        # Trunca a los mismos max_length de Sucursal (nombre=200,
        # direccion=300, horario_texto=200) -- bug real en produccion
        # (2026-08-28, docs/PENDIENTES.md): sin esto, un horario_texto largo
        # que el LLM devuelve (comun en sedes con horarios de Ventas Y
        # Servicio Tecnico juntos en el mismo texto) aborta la corrida
        # entera contra SQL Server ("String or binary data would be
        # truncated") -- sqlite (tests) no lo detecta porque no valida
        # max_length en save(). Mismo criterio ya aplicado al paso
        # estructurado de sucursales y a modelo/version/url_fuente de
        # vehiculos, mas abajo en esta funcion.
        direccion = (item.get("direccion") or "")[:300]
        horario = (item.get("horario_texto") or "")[:200]
        campos = {}
        if direccion:
            campos["direccion"] = direccion
        if horario:
            campos["horario_texto"] = horario
        clave_direccion = _normalizar_clave(direccion) if direccion else ""
        obj = indice_direcciones.get(clave_direccion) if clave_direccion else None
        if obj is None:
            obj = indice_sucursales.get(_normalizar_clave(nombre))
        if obj is None and not direccion:
            # NO crear una sede nueva sin direccion. Regresion real del
            # ScrapeRun 86 (2026-09-01): el scrape de Astara creo 8 filas
            # basura por esta via -- duplicados con otro nombre ("ASTARA
            # RETAIL (CANTAGALLO)" cuando ya existia "Cantagallo") y
            # encabezados de pagina tomados como sede ("Sala de ventas",
            # de "Te esperamos en nuestra sala de ventas"). Es la SEGUNDA
            # vez: las mismas filas se borraron a mano el 2026-08-27 sin
            # tocar la causa, y volvieron. Una sede sin direccion no se
            # puede geocodificar (bot/signals.py) ni aparece en
            # buscar_sucursales_cercanas (filtra latitud no nula), pero SI
            # es visible al cliente por listar_catalogo -- es puro ruido.
            # El guard es solo para CREAR: una sede que ya existe se sigue
            # actualizando abajo (ej. para completarle el horario).
            logger.info(
                "[scraping] sucursal %r sin direccion, no se crea (cliente=%s)", nombre, cliente,
            )
            continue
        if obj is None:
            obj = Sucursal.todos_los_clientes.create(nombre=nombre, cliente=cliente, **campos)
        elif campos:
            for campo, valor in campos.items():
                setattr(obj, campo, valor)
            obj.save(update_fields=list(campos.keys()))
        indice_sucursales[_normalizar_clave(nombre)] = obj
        if clave_direccion:
            indice_direcciones[clave_direccion] = obj

    # Paso estructurado, DESPUES del upsert por LLM de arriba -- si el LLM
    # ya creo/actualizo una sucursal con una direccion parecida pero menos
    # confiable, este paso la encuentra por direccion normalizada (que SI
    # expande abreviaturas, a diferencia de _normalizar_clave) y la corrige
    # en vez de crear una fila paralela. Reemplaza categorias entre corridas
    # (nunca las acumula para siempre), pero UNE las de dos paginas de una
    # misma corrida -- ver docs/superpowers/specs/2026-08-27-sucursales-
    # categorias-extraccion-estructurada-design.md, "Vigencia de categorias",
    # y el comentario del set de claves mas abajo.
    indice_por_direccion_normalizada = {
        _normalizar_direccion(s.direccion): s
        for s in Sucursal.todos_los_clientes.filter(cliente=cliente)
        if s.direccion
    }
    # Claves (direccion normalizada) que este paso ya escribio DURANTE ESTA
    # corrida. Implementa las dos mitades de "Vigencia de categorias" del spec
    # con una sola regla: el PRIMER write de una clave en esta corrida
    # REEMPLAZA (semantica entre corridas -- una categoria retirada del sitio
    # real tiene que poder desaparecer), y una REPETICION de la misma clave
    # dentro de esta misma corrida (dos paginas distintas que resuelven a la
    # misma direccion normalizada) UNE en vez de pisar, para que la segunda
    # pagina no borre las categorias que aporto la primera.
    claves_escritas_en_esta_corrida: set[str] = set()
    for item in (estructurados or []):
        # Truncado a los max_length reales de Sucursal (nombre=200,
        # direccion=300, horario_texto=200): sqlite (tests) acepta el overflow
        # en silencio, pero el backend real es SQL Server (mssql-django) y
        # revienta duro, abortando execute_scrape a mitad de camino (el upsert
        # de vehiculos nunca corre y el run queda en un "fallo" ambiguo). El
        # horario_texto real medido de Cantagallo son 179 chars: 21 de holgura.
        # Mismo patron que modelo/version/url_fuente mas abajo.
        direccion = (item.get("direccion") or "").strip()[:300]
        nombre = (item.get("nombre") or "").strip()[:200]
        if not direccion or not nombre:
            continue
        clave = _normalizar_direccion(direccion)
        obj = indice_por_direccion_normalizada.get(clave)
        categorias = list(dict.fromkeys(item.get("categorias") or []))
        horario = (item.get("horario_texto") or "")[:200]
        if obj is None:
            obj = Sucursal.todos_los_clientes.create(
                nombre=nombre, cliente=cliente, direccion=direccion,
                horario_texto=horario, categorias=categorias,
                categorias_actualizado_en=timezone.now(),
            )
            indice_por_direccion_normalizada[clave] = obj
        else:
            obj.direccion = direccion
            if horario:
                obj.horario_texto = horario
            if clave in claves_escritas_en_esta_corrida:
                obj.categorias = list(dict.fromkeys(list(obj.categorias or []) + categorias))
            else:
                obj.categorias = categorias
            obj.categorias_actualizado_en = timezone.now()
            obj.save(update_fields=["direccion", "horario_texto", "categorias", "categorias_actualizado_en"])
        claves_escritas_en_esta_corrida.add(clave)

    indice_vehiculos = {}
    for v in VehiculoCatalogo.todos_los_clientes.filter(cliente=cliente):
        indice_vehiculos[f"{_normalizar_clave(v.modelo)}|{_normalizar_clave(v.version)}"] = v

    for item in catalogo.get("vehiculos", []):
        modelo = (item.get("modelo") or "").strip()
        if not modelo:
            continue
        version = (item.get("version") or "").strip()
        modelo = modelo[:100]
        version = version[:150]
        url_fuente_original = item.get("url_fuente") or ""
        # Si crawl() descubrio una ficha tecnica PDF linkeada desde esta
        # misma pagina, preferirla sobre la pagina HTML como url_fuente --
        # mismo criterio que ya aplica _consolidar_candidatos en
        # bot/business/ventas.py al fusionar filas duplicadas ("una ficha
        # tecnica real descargable es un dato mas util que la pagina que la
        # menciona"), ahora aplicado en el momento del upsert en vez de
        # depender de que haya una fila hermana con el PDF por casualidad.
        url_fuente = ((fichas_tecnicas or {}).get(url_fuente_original) or url_fuente_original)[:200]

        def _precio_valido(campo: str, valor_crudo=None) -> int | None:
            valor = _parsear_entero(item.get(campo) if valor_crudo is None else valor_crudo, campo, modelo)
            if valor is not None and valor > 2_000_000_000:
                logger.warning("[scraping] %s=%r del vehiculo %r es inverosimil, se descarta", campo, valor, modelo)
                return None
            return valor

        precio = _precio_valido("precio")
        precio_contado = _precio_valido("precio_contado")
        precio_financiado = _precio_valido("precio_financiado")
        specs = {k: v for k, v in (item.get("specs") or {}).items() if v is not None}

        # Completa (nunca reemplaza) con lo que _detectar_datos_seminuevo
        # encontro en #dCaracteristicas/#dDescripcion de esta misma pagina
        # -- el LLM no estructura listas de equipamiento a proposito (ver
        # el prompt de bot/scraping/extractor.py), asi que "equipamiento"
        # nunca lo trae el LLM; precio_contado/financiado si puede traerlos
        # cuando el sitio los publica en otra parte de la pagina, y en ese
        # caso ese valor manda.
        # Match por nombre normalizado (insensible a mayus/acentos): la
        # ficha dice "ALFA ROMEO TONALE" y el LLM "Alfa Romeo Tonale".
        datos_sem = (datos_seminuevos or {}).get(_normalizar_clave(modelo))
        if datos_sem:
            if precio_contado is None and datos_sem.get("precio_contado"):
                precio_contado = _precio_valido("precio_contado", datos_sem["precio_contado"])
            if precio_financiado is None and datos_sem.get("precio_financiado"):
                precio_financiado = _precio_valido("precio_financiado", datos_sem["precio_financiado"])
            if datos_sem.get("equipamiento"):
                specs = {**specs, "equipamiento": datos_sem["equipamiento"]}
        # "usado" si la URL de origen es la seccion de seminuevos del sitio --
        # ver comentario en VehiculoCatalogo.condicion. Se resuelve por URL,
        # no por texto del modelo, porque el mismo nombre generico
        # ("Compass", "L200") aparece tanto en la ficha 0km como en el listado
        # de seminuevos.
        condicion = "usado" if "seminuevos" in url_fuente.lower() else "0km"
        clave = f"{_normalizar_clave(modelo)}|{_normalizar_clave(version)}"
        obj = indice_vehiculos.get(clave)
        if obj is None:
            obj = VehiculoCatalogo.todos_los_clientes.create(
                modelo=modelo, version=version, precio=precio,
                precio_contado=precio_contado, precio_financiado=precio_financiado,
                specs=specs, url_fuente=url_fuente, cliente=cliente, condicion=condicion,
            )
            indice_vehiculos[clave] = obj
            continue
        campos = {}
        if precio is not None:
            campos["precio"] = precio
        if precio_contado is not None:
            campos["precio_contado"] = precio_contado
        if precio_financiado is not None:
            campos["precio_financiado"] = precio_financiado
        if specs:
            obj.specs = {**obj.specs, **specs}
            campos["specs"] = obj.specs
        if url_fuente:
            campos["url_fuente"] = url_fuente
        if condicion != obj.condicion:
            campos["condicion"] = condicion
        if campos:
            for campo, valor in campos.items():
                setattr(obj, campo, valor)
            obj.save(update_fields=list(campos.keys()))
