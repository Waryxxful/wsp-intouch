from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from bot.scraping.estructurados import (
    _detectar_datos_seminuevo, _detectar_href_ficha_tecnica, _es_ficha_sucursal, _parsear_ficha_sucursal,
    extraer_estructurados,
)

_HTML_CANTAGALLO_AMBAS_CATEGORIAS = """
<html><body>
<h1 class="elementor-cta__title">
    Cantagallo
</h1>
<div class="elementor-widget-container">
    <p><strong>Horario Ventas:</strong><br>Lunes a viernes 09:00 a 19:00 hrs.<br>Sábado 10:00 a 14:00 hrs.</p>
    <p><strong>Horario Servicio Técnico:</strong><br>Lunes a Jueves 8:00 a 17:30 hrs. Viernes 8:00 a 16:30 hrs. Sábado cerrado.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Dirección:<br /></strong>Av. Las Condes 12.256, Vitacura.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Teléfono:<br /></strong>600 375 7000</p>
</div>
</body></html>
"""

_HTML_MALL_PLAZA_SUR_SOLO_VENTAS = """
<html><body>
<h1>Mall Plaza Sur</h1>
<div class="elementor-widget-container">
    <p><strong>Horario:</strong><br />Lunes a jueves 10:30 a 20:30 hrs. Viernes y Sábado 10:30 a 20:00 hrs. Domingo 11:00 a 21:00 hrs.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Dirección:<br /></strong>Av Jorge Alessandri Rodriguez 20040 L128A, San Bernardo.</p>
</div>
</body></html>
"""

# Caso real que rompe un parser basado en regex: la direccion tiene un
# <span> anidado dentro del mismo <p> que el <strong>. Ademas el label
# generico "Horario:" corresponde en realidad a Desabolladura y Pintura,
# no a Ventas -- limite conocido documentado en el spec, este fixture solo
# confirma que el parser no se rompe, no que clasifique bien la categoria.
_HTML_RANCAGUA_DYP_DIRECCION_ANIDADA = """
<html><body>
<h1>RANCAGUA</h1>
<div class="elementor-widget-container">
    <p><strong>Horario:</strong><br />Lunes a Jueves 8:30 a 17:30 hrs.<br />Viernes 8:00 a 16:30 hrs. Sábado cerrado.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Dirección:<br /></strong>Av. <span lang="PT">Longitudinal Sur, N° 1040, Ruta Travesía, </span>Rancagua.</p>
</div>
</body></html>
"""

_HTML_FICHA_VEHICULO_NO_MATCHEA = """
<html><body>
<h1>Koleos</h1>
<div class="elementor-widget-container">
    <p><strong>Precio:</strong> $24.490.000</p>
    <p><strong>Motor:</strong> 2.5L</p>
</div>
</body></html>
"""

# <strong> con texto que matchea "Direccion"/"Horario" pero fuera de un <p>
# (boilerplate generico de Elementor/WordPress: un <li> o <div> reusa el
# mismo texto en paginas sin relacion con sucursales). No debe detectarse
# como ficha de sucursal -- el spec exige especificamente <p><strong>, no
# cualquier <strong> en la pagina.
_HTML_STRONG_FUERA_DE_P_NO_MATCHEA = """
<html><body>
<h1>Preguntas Frecuentes</h1>
<div class="elementor-widget-container">
    <ul>
        <li><strong>Horario</strong> de atencion telefonica: 24/7</li>
    </ul>
    <div><strong>Dirección</strong> de oficinas corporativas (no una sucursal)</div>
</div>
</body></html>
"""

# Falso positivo REAL demostrado en la revision final: una pagina de contacto
# corporativa cualquiera (de CUALQUIER cliente, no solo Astara -- el extractor
# corre contra todas las paginas de todos los scrapes, sin atender a nadie)
# tiene <h1> + <p><strong>Dirección:</strong>. Con el criterio viejo (Direccion
# O Horario) se detectaba y parseaba a {'nombre': 'Contacto', 'categorias':
# ['ventas']}, creando una fila Sucursal bogus inmediatamente visible al
# cliente por las tools del bot. La senal que la distingue de una sede real:
# una sede SIEMPRE publica horario de atencion, una pagina de contacto
# generica no.
_HTML_CONTACTO_CORPORATIVO_SIN_HORARIO = """
<html><body>
<h1>Contacto</h1>
<div class="elementor-widget-container">
    <p><strong>Dirección:</strong> Isidora Goyenechea 3000, Piso 20, Las Condes.</p>
    <p><strong>Email:</strong> contacto@ejemplo.cl</p>
</div>
</body></html>
"""

# Pagina con <h1> y un <p><strong>Horario...</strong> pero SIN parrafo de
# direccion -- el parser no tiene lo minimo indispensable y devuelve None
# (una pagina detectada pero no parseable no debe reventar el crawl, solo no
# aporta nada estructurado).
_HTML_HORARIO_SIN_DIRECCION = """
<html><body>
<h1>Atención al Cliente</h1>
<div class="elementor-widget-container">
    <p><strong>Horario:</strong> Lunes a viernes 09:00 a 18:00 hrs.</p>
</div>
</body></html>
"""

# Label de horario con vocabulario FUERA del set conocido: el slug derivado
# seria "de_atencion", una categoria basura que ademas ESCONDE la sucursal de
# los filtros reales del bot (una lista no vacia sin "ventas"/"servicio_tecnico"
# no matchea ningun filtro real). Debe caer al default "ventas" y loguear un
# warning con el label crudo.
_HTML_LABEL_DE_HORARIO_DESCONOCIDO = """
<html><body>
<h1>Sede Nueva</h1>
<div class="elementor-widget-container">
    <p><strong>Horario de atención:</strong> Lunes a viernes 09:00 a 18:00 hrs.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Dirección:</strong> Av. Nueva 100, Santiago.</p>
</div>
</body></html>
"""

# Dos parrafos "Horario Ventas:" en la misma pagina (caso barato de markup
# duplicado por el CMS) -- no deben producir ["ventas", "ventas"].
_HTML_HORARIO_VENTAS_DUPLICADO = """
<html><body>
<h1>Sede Duplicada</h1>
<div class="elementor-widget-container">
    <p><strong>Horario Ventas:</strong> Lunes a viernes 09:00 a 19:00 hrs.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Horario Ventas:</strong> Sábado 10:00 a 14:00 hrs.</p>
</div>
<div class="elementor-widget-container">
    <p><strong>Dirección:</strong> Av. Duplicada 200, Santiago.</p>
</div>
</body></html>
"""


class EsFichaSucursalTest(SimpleTestCase):
    def test_detecta_pagina_con_h1_y_direccion(self):
        soup = BeautifulSoup(_HTML_CANTAGALLO_AMBAS_CATEGORIAS, "html.parser")
        self.assertTrue(_es_ficha_sucursal(soup))

    def test_no_detecta_ficha_de_vehiculo(self):
        soup = BeautifulSoup(_HTML_FICHA_VEHICULO_NO_MATCHEA, "html.parser")
        self.assertFalse(_es_ficha_sucursal(soup))

    def test_no_detecta_pagina_sin_h1(self):
        soup = BeautifulSoup("<html><body><p><strong>Dirección:</strong> x</p></body></html>", "html.parser")
        self.assertFalse(_es_ficha_sucursal(soup))

    def test_no_detecta_strong_fuera_de_p(self):
        soup = BeautifulSoup(_HTML_STRONG_FUERA_DE_P_NO_MATCHEA, "html.parser")
        self.assertFalse(_es_ficha_sucursal(soup))

    def test_no_detecta_pagina_de_contacto_corporativa_sin_horario(self):
        soup = BeautifulSoup(_HTML_CONTACTO_CORPORATIVO_SIN_HORARIO, "html.parser")
        self.assertFalse(_es_ficha_sucursal(soup))

    def test_no_detecta_pagina_con_horario_pero_sin_direccion(self):
        soup = BeautifulSoup(_HTML_HORARIO_SIN_DIRECCION, "html.parser")
        self.assertFalse(_es_ficha_sucursal(soup))


class ParsearFichaSucursalTest(SimpleTestCase):
    def test_sede_con_ambas_categorias(self):
        soup = BeautifulSoup(_HTML_CANTAGALLO_AMBAS_CATEGORIAS, "html.parser")
        item = _parsear_ficha_sucursal(soup)
        self.assertEqual(item["nombre"], "Cantagallo")
        self.assertEqual(item["direccion"], "Av. Las Condes 12.256, Vitacura.")
        self.assertEqual(sorted(item["categorias"]), ["servicio_tecnico", "ventas"])

    def test_sede_con_horario_generico_default_a_ventas(self):
        soup = BeautifulSoup(_HTML_MALL_PLAZA_SUR_SOLO_VENTAS, "html.parser")
        item = _parsear_ficha_sucursal(soup)
        self.assertEqual(item["nombre"], "Mall Plaza Sur")
        self.assertEqual(item["categorias"], ["ventas"])

    def test_direccion_con_markup_anidado_no_se_rompe(self):
        soup = BeautifulSoup(_HTML_RANCAGUA_DYP_DIRECCION_ANIDADA, "html.parser")
        item = _parsear_ficha_sucursal(soup)
        self.assertEqual(item["nombre"], "RANCAGUA")
        self.assertIn("Longitudinal Sur", item["direccion"])
        self.assertIn("1040", item["direccion"])

    def test_pagina_sin_h1_devuelve_none(self):
        soup = BeautifulSoup("<html><body><p><strong>Dirección:</strong> x</p></body></html>", "html.parser")
        self.assertIsNone(_parsear_ficha_sucursal(soup))

    def test_horario_texto_concatena_los_dos_parrafos_con_su_label(self):
        # El valor exacto importa: son 179 chars contra un
        # Sucursal.horario_texto de max_length=200 (21 de holgura), y el
        # backend real es SQL Server, que revienta duro si se pasa.
        soup = BeautifulSoup(_HTML_CANTAGALLO_AMBAS_CATEGORIAS, "html.parser")
        item = _parsear_ficha_sucursal(soup)
        self.assertEqual(
            item["horario_texto"],
            "Horario Ventas: Lunes a viernes 09:00 a 19:00 hrs. Sábado 10:00 a 14:00 hrs. / "
            "Horario Servicio Técnico: Lunes a Jueves 8:00 a 17:30 hrs. "
            "Viernes 8:00 a 16:30 hrs. Sábado cerrado.",
        )

    def test_pagina_de_contacto_corporativa_sin_horario_devuelve_none(self):
        # Aunque alguien llame al parser directo sin pasar por
        # _es_ficha_sucursal: sin horario no es una sede, no se inventa una.
        soup = BeautifulSoup(_HTML_CONTACTO_CORPORATIVO_SIN_HORARIO, "html.parser")
        self.assertIsNone(_parsear_ficha_sucursal(soup))

    def test_pagina_con_horario_pero_sin_direccion_devuelve_none(self):
        soup = BeautifulSoup(_HTML_HORARIO_SIN_DIRECCION, "html.parser")
        self.assertIsNone(_parsear_ficha_sucursal(soup))

    def test_label_de_horario_fuera_del_vocabulario_conocido_cae_a_ventas(self):
        soup = BeautifulSoup(_HTML_LABEL_DE_HORARIO_DESCONOCIDO, "html.parser")
        with self.assertLogs("bot.scraping.estructurados", level="WARNING") as logs:
            item = _parsear_ficha_sucursal(soup)
        self.assertEqual(item["categorias"], ["ventas"])
        # El warning tiene que traer el label CRUDO (sin sluggear) para que un
        # operador reconozca el texto tal cual aparece en el sitio.
        self.assertIn("Horario de atención:", "\n".join(logs.output))

    def test_categorias_repetidas_en_la_misma_pagina_se_deduplican(self):
        soup = BeautifulSoup(_HTML_HORARIO_VENTAS_DUPLICADO, "html.parser")
        item = _parsear_ficha_sucursal(soup)
        self.assertEqual(item["categorias"], ["ventas"])
        # El horario_texto SI conserva los dos parrafos (son horarios
        # distintos), solo la categoria se deduplica.
        self.assertIn("Lunes a viernes", item["horario_texto"])
        self.assertIn("Sábado", item["horario_texto"])


class ExtraerEstructuradosTest(SimpleTestCase):
    def test_pagina_de_sucursal_devuelve_sucursales_con_un_item(self):
        soup = BeautifulSoup(_HTML_CANTAGALLO_AMBAS_CATEGORIAS, "html.parser")
        resultado = extraer_estructurados(soup)
        self.assertEqual(len(resultado["sucursales"]), 1)
        self.assertEqual(resultado["sucursales"][0]["nombre"], "Cantagallo")

    def test_pagina_que_no_matchea_ningun_extractor_devuelve_vacio(self):
        soup = BeautifulSoup(_HTML_FICHA_VEHICULO_NO_MATCHEA, "html.parser")
        self.assertEqual(extraer_estructurados(soup), {})

    def test_pagina_de_contacto_corporativa_no_produce_sucursal(self):
        soup = BeautifulSoup(_HTML_CONTACTO_CORPORATIVO_SIN_HORARIO, "html.parser")
        self.assertEqual(extraer_estructurados(soup), {})

    def test_pagina_con_horario_pero_sin_direccion_no_produce_sucursal(self):
        soup = BeautifulSoup(_HTML_HORARIO_SIN_DIRECCION, "html.parser")
        self.assertEqual(extraer_estructurados(soup), {})

    def test_resultado_no_vacio_se_loguea_para_auditar_falsos_positivos(self):
        # El extractor corre sin supervision (bot/scraping/scheduler.py) para
        # TODOS los clientes: un falso positivo tiene que ser visible en los
        # logs de inmediato, no descubrirse porque un cliente vio una sucursal
        # inventada por WhatsApp.
        soup = BeautifulSoup(_HTML_CANTAGALLO_AMBAS_CATEGORIAS, "html.parser")
        with self.assertLogs("bot.scraping.estructurados", level="INFO") as logs:
            extraer_estructurados(soup)
        salida = "\n".join(logs.output)
        self.assertIn("Cantagallo", salida)
        self.assertIn("Av. Las Condes 12.256, Vitacura.", salida)
        self.assertIn("servicio_tecnico", salida)

    def test_pagina_sin_match_no_loguea_nada(self):
        soup = BeautifulSoup(_HTML_FICHA_VEHICULO_NO_MATCHEA, "html.parser")
        with self.assertNoLogs("bot.scraping.estructurados", level="INFO"):
            extraer_estructurados(soup)


class DetectarHrefFichaTecnicaTest(SimpleTestCase):
    # Senal INDEPENDIENTE de extraer_estructurados/_REGISTRO a proposito: no
    # es una clasificacion excluyente de la pagina (una pagina de modelo de
    # vehiculo no es "una ficha tecnica", simplemente puede CONTENER un link
    # a una) -- ver docs/PENDIENTES.md, seccion "[URGENTE] Astara --
    # auditoría completa de catálogo", punto 5.
    def test_detecta_link_con_texto_ficha_tecnica_terminado_en_pdf(self):
        html = (
            '<html><body><h1>Outlander PHEV</h1>'
            '<a href="/wp-content/uploads/2024/10/ficha-outlander-phev.pdf">Ficha Técnica</a>'
            '</body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(
            _detectar_href_ficha_tecnica(soup),
            "/wp-content/uploads/2024/10/ficha-outlander-phev.pdf",
        )

    def test_matchea_sin_acentos_ni_mayusculas_en_el_texto_del_link(self):
        html = '<html><body><a href="/x.pdf">ficha tecnica</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(_detectar_href_ficha_tecnica(soup), "/x.pdf")

    def test_matchea_por_href_aunque_el_texto_del_link_no_diga_ficha(self):
        html = '<html><body><a href="/descargas/ficha-tecnica-koleos.pdf">Descargar</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(_detectar_href_ficha_tecnica(soup), "/descargas/ficha-tecnica-koleos.pdf")

    def test_no_matchea_pdf_sin_relacion_a_ficha_tecnica(self):
        html = '<html><body><a href="/legal/terminos-y-condiciones.pdf">Términos y condiciones</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(_detectar_href_ficha_tecnica(soup))

    def test_no_matchea_link_ficha_tecnica_que_no_es_pdf(self):
        # El texto dice "ficha tecnica" pero apunta a otra pagina HTML, no a
        # un PDF descargable -- no hay nada que fetchear como documento.
        html = '<html><body><a href="/outlander-phev/ficha-tecnica/">Ficha Técnica</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(_detectar_href_ficha_tecnica(soup))

    def test_pagina_sin_ningun_link_devuelve_none(self):
        soup = BeautifulSoup("<html><body><h1>Sin links</h1></body></html>", "html.parser")
        self.assertIsNone(_detectar_href_ficha_tecnica(soup))

    def test_devuelve_el_primer_match_si_hay_varios(self):
        html = (
            '<html><body>'
            '<a href="/uno-ficha.pdf">Ficha Técnica</a>'
            '<a href="/dos-ficha.pdf">Ficha Técnica</a>'
            '</body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(_detectar_href_ficha_tecnica(soup), "/uno-ficha.pdf")


# Recorte real de astararetail.cl/seminuevos/seminuevos/ficha/1282567
# (fetch directo con el user-agent del crawler, no una version resumida) --
# ver docs/PENDIENTES.md, seccion "[URGENTE] Astara -- auditoría completa
# de catálogo", punto 5 (b).
_HTML_FICHA_SEMINUEVO_REAL = """
<html><body>
<h4 class="font-weight-bold marcaVehiculo">ALFA ROMEO TONALE</h4>
<h5><i class="fas fa-angle-right amber-text"></i>&nbsp; Equipamiento</h5>
<div class="car-detail-block features">
    <ul class="col-12" id="dCaracteristicas">
        <li> <i class="fa fa-check"></i>6 Air bag</li>
        <li> <i class="fa fa-check"></i>Alarma</li>
        <li> <i class="fa fa-check"></i>Asientos de Cuero</li>
    </ul>
</div>
<h5><i class="fas fa-angle-right amber-text"></i>&nbsp; Descripción</h5>
<div id="descr" class="tab-pane">
    <div id="dDescripcion">Precio publicado Incluye Bono de financiamiento
Precio al contado: $42.990.000
Precio con financiamiento o vehículo en parte de pago: $41.990.000

Con 16.000 KM, este Alfa Romeo Tonale 2025 combina diseño italiano y tecnología.
El equipamiento puede variar, favor consultar con un ejecutivo para más detalles.</div>
</div>
</body></html>
"""


class DetectarDatosSeminuevoTest(SimpleTestCase):
    def test_detecta_equipamiento_y_precios_de_una_ficha_real(self):
        soup = BeautifulSoup(_HTML_FICHA_SEMINUEVO_REAL, "html.parser")
        datos = _detectar_datos_seminuevo(soup)
        self.assertEqual(datos["equipamiento"], ["6 Air bag", "Alarma", "Asientos de Cuero"])
        self.assertEqual(datos["precio_contado"], "$42.990.000")
        self.assertEqual(datos["precio_financiado"], "$41.990.000")

    def test_extrae_el_nombre_del_vehiculo_para_poder_matchearlo_con_el_catalogo(self):
        # Falla real del ScrapeRun 86 (2026-09-01): el join se hacia por URL
        # de la pagina, pero el LLM extrae los usados desde la pagina de
        # LISTADO (/seminuevos/seminuevos?promocion=...), no desde cada
        # ficha -- las claves nunca matcheaban y quedaba 0 equipamiento.
        # El <h4 class="marcaVehiculo"> tiene marca+modelo igual que el
        # nombre que produce el LLM ("ALFA ROMEO TONALE").
        soup = BeautifulSoup(_HTML_FICHA_SEMINUEVO_REAL, "html.parser")
        self.assertEqual(_detectar_datos_seminuevo(soup)["nombre"], "ALFA ROMEO TONALE")

    def test_sin_h4_de_marca_el_nombre_queda_vacio_pero_no_rompe(self):
        html = (
            '<html><body>'
            '<ul id="dCaracteristicas"><li>Alarma</li></ul>'
            '<div id="dDescripcion">Precio al contado: $10.000.000</div>'
            '</body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        datos = _detectar_datos_seminuevo(soup)
        self.assertEqual(datos["nombre"], "")
        self.assertEqual(datos["equipamiento"], ["Alarma"])

    def test_sin_dCaracteristicas_no_matchea(self):
        html = '<html><body><div id="dDescripcion">Solo descripcion.</div></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(_detectar_datos_seminuevo(soup))

    def test_sin_dDescripcion_no_matchea(self):
        html = '<html><body><ul id="dCaracteristicas"><li>Alarma</li></ul></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(_detectar_datos_seminuevo(soup))

    def test_descripcion_sin_precios_reconocibles_deja_esos_campos_en_none(self):
        html = (
            '<html><body>'
            '<ul id="dCaracteristicas"><li>Alarma</li></ul>'
            '<div id="dDescripcion">Un auto muy lindo, consulta por precio.</div>'
            '</body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        datos = _detectar_datos_seminuevo(soup)
        self.assertIsNone(datos["precio_contado"])
        self.assertIsNone(datos["precio_financiado"])
        self.assertEqual(datos["equipamiento"], ["Alarma"])

    def test_equipamiento_vacio_devuelve_lista_vacia(self):
        html = (
            '<html><body>'
            '<ul id="dCaracteristicas"></ul>'
            '<div id="dDescripcion">Precio al contado: $10.000.000</div>'
            '</body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        datos = _detectar_datos_seminuevo(soup)
        self.assertEqual(datos["equipamiento"], [])
        self.assertEqual(datos["precio_contado"], "$10.000.000")
