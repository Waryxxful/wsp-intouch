from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bot.models import ScrapingSource, ScrapeRun, Servicio, Sucursal, VehiculoCatalogo, ScrapedPage
from bot.scraping.runner import run_scrape, _parsear_entero, _upsert_catalogo


class ParsearEnteroTest(TestCase):
    def test_numero_directo_se_mantiene(self):
        self.assertEqual(_parsear_entero(15000, "precio", "x"), 15000)

    def test_string_con_simbolo_de_moneda_y_separadores_de_miles_se_limpia(self):
        self.assertEqual(_parsear_entero("$11.990.000", "precio", "x"), 11990000)

    def test_gratuito_y_gratis_son_cero(self):
        self.assertEqual(_parsear_entero("gratuito", "precio", "x"), 0)
        self.assertEqual(_parsear_entero("Gratis", "precio", "x"), 0)

    def test_none_es_none(self):
        self.assertIsNone(_parsear_entero(None, "precio", "x"))

    def test_texto_no_interpretable_es_none(self):
        self.assertIsNone(_parsear_entero("a consultar", "precio", "x"))


class RunnerTest(TestCase):
    def _source(self, url="https://x.cl/"):
        return ScrapingSource.objects.create(url=url)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_exitoso_crea_paginas_y_actualiza_catalogo(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [{"nombre": "Corte", "duracion_min": 30, "precio": None}],
            # Con direccion: desde el fix del ScrapeRun 86 una sede nueva sin
            # direccion no se crea (ver test_upsert_no_crea_sucursal_nueva_sin_direccion).
            "sucursales": [{"nombre": "Centro", "direccion": "Av. Siempre Viva 742", "horario_texto": None}],
        }
        run = run_scrape(self._source())
        self.assertEqual(run.estado, "ok")
        self.assertEqual(run.paginas_procesadas, 1)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.pages.count(), 1)
        self.assertEqual(run.catalogo_extraido, mock_extract.return_value)
        self.assertEqual(run.paginas_con_error, [])
        self.assertTrue(Servicio.objects.filter(nombre="Corte", duracion_min=30).exists())
        self.assertTrue(Sucursal.objects.filter(nombre="Centro").exists())

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_indexa_cada_pagina_en_supabase(self, mock_crawl, mock_extract, mock_indexar):
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": []}

        run = run_scrape(self._source())

        mock_indexar.assert_called_once()
        pagina_pasada = mock_indexar.call_args[0][0]
        self.assertEqual(pagina_pasada.url, "https://x.cl/")
        self.assertEqual(run.pages.count(), 1)

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_purga_paginas_de_runs_anteriores_del_mismo_source(
        self, mock_crawl, mock_extract, mock_indexar,
    ):
        source = self._source()
        run_viejo = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        pagina_vieja = ScrapedPage.objects.create(run=run_viejo, url="https://x.cl/vieja", texto="contenido viejo")
        mock_crawl.return_value = ([{"url": "https://x.cl/nueva", "texto": "contenido nuevo"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_nuevo = run_scrape(source)

        self.assertFalse(ScrapedPage.objects.filter(pk=pagina_vieja.pk).exists())
        self.assertEqual(run_nuevo.pages.count(), 1)
        self.assertEqual(run_nuevo.pages.first().url, "https://x.cl/nueva")

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_no_purga_paginas_de_otro_source(self, mock_crawl, mock_extract, mock_indexar):
        otro_source = self._source(url="https://otro.cl/")
        otro_run = ScrapeRun.objects.create(source=otro_source, url=otro_source.url, estado="ok")
        pagina_de_otro = ScrapedPage.objects.create(run=otro_run, url="https://otro.cl/x", texto="otro contenido")
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(self._source())

        self.assertTrue(ScrapedPage.objects.filter(pk=pagina_de_otro.pk).exists())

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_no_purga_paginas_de_run_hermano_todavia_corriendo(
        self, mock_crawl, mock_extract, mock_indexar,
    ):
        source = self._source()
        run_hermano = ScrapeRun.objects.create(source=source, url=source.url, estado="corriendo")
        pagina_hermano = ScrapedPage.objects.create(run=run_hermano, url="https://x.cl/hermano", texto="en curso")
        mock_crawl.return_value = ([{"url": "https://x.cl/nueva", "texto": "contenido nuevo"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(source)

        self.assertTrue(ScrapedPage.objects.filter(pk=pagina_hermano.pk).exists())

    @patch("bot.scraping.runner.borrar_chunks_de_paginas_purgadas")
    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_purga_paginas_de_run_hermano_colgado_hace_mas_de_60_min(
        self, mock_crawl, mock_extract, mock_indexar, mock_borrar_chunks,
    ):
        # Un run "corriendo" mas viejo que _CORRIENDO_STALE_MINUTOS esta muerto,
        # no en curso -- el mismo umbral que ya usaba el scheduler para arrancar
        # un run nuevo. Sin esto, un ScrapeRun colgado para siempre protegia sus
        # ScrapedPage de la purga eternamente (ver docs/PENDIENTES.md).
        source = self._source()
        run_colgado = ScrapeRun.objects.create(source=source, url=source.url, estado="corriendo")
        ScrapeRun.objects.filter(pk=run_colgado.pk).update(started_at=timezone.now() - timedelta(minutes=90))
        pagina_colgada = ScrapedPage.objects.create(run=run_colgado, url="https://x.cl/colgada", texto="vieja")
        mock_crawl.return_value = ([{"url": "https://x.cl/nueva", "texto": "contenido nuevo"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(source)

        self.assertFalse(ScrapedPage.objects.filter(pk=pagina_colgada.pk).exists())

    @patch("bot.scraping.runner.borrar_chunks_de_paginas_purgadas")
    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_borra_los_chunks_de_supabase_de_las_paginas_purgadas(
        self, mock_crawl, mock_extract, mock_indexar, mock_borrar_chunks,
    ):
        source = self._source()
        run_viejo = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        pagina_vieja = ScrapedPage.objects.create(run=run_viejo, url="https://x.cl/vieja", texto="vieja")
        mock_crawl.return_value = ([{"url": "https://x.cl/nueva", "texto": "contenido nuevo"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(source)

        mock_borrar_chunks.assert_called_once_with([pagina_vieja.pk], cliente="renault")

    @patch("bot.scraping.runner.borrar_chunks_de_paginas_purgadas")
    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_no_llama_a_borrar_chunks_si_no_hay_nada_que_purgar(
        self, mock_crawl, mock_extract, mock_indexar, mock_borrar_chunks,
    ):
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(self._source())

        mock_borrar_chunks.assert_not_called()

    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_crawl_falla_no_purga_paginas_viejas(self, mock_crawl):
        source = self._source(url="http://127.0.0.1/")
        run_viejo = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        pagina_vieja = ScrapedPage.objects.create(run=run_viejo, url="http://127.0.0.1/vieja", texto="vieja")
        mock_crawl.side_effect = ValueError("IP no permitida")

        run_scrape(source)

        self.assertTrue(ScrapedPage.objects.filter(pk=pagina_vieja.pk).exists())

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_no_purga_paginas_viejas_si_crawl_no_encuentra_nada(
        self, mock_crawl, mock_extract, mock_indexar,
    ):
        source = self._source()
        run_viejo = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        pagina_vieja = ScrapedPage.objects.create(run=run_viejo, url="https://x.cl/vieja", texto="vieja")
        mock_crawl.return_value = ([], [{"url": source.url, "error": "timeout"}])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(source)

        self.assertTrue(ScrapedPage.objects.filter(pk=pagina_vieja.pk).exists())

    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_crawl_falla_marca_error(self, mock_crawl):
        mock_crawl.side_effect = ValueError("IP no permitida")
        run = run_scrape(self._source(url="http://127.0.0.1/"))
        self.assertEqual(run.estado, "error")
        self.assertIn("IP no permitida", run.error_detalle)
        self.assertEqual(run.pages.count(), 0)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_extractor_falla_conserva_paginas_ya_guardadas(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.side_effect = ValueError("json invalido")
        run = run_scrape(self._source())
        self.assertEqual(run.estado, "error")
        self.assertEqual(run.pages.count(), 1)
        self.assertEqual(run.paginas_procesadas, 1)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_no_borra_servicios_existentes_no_mencionados(self, mock_crawl, mock_extract):
        Servicio.objects.create(nombre="Manicure", duracion_min=45)
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [{"nombre": "Corte", "duracion_min": None, "precio": None}], "sucursales": []}
        run_scrape(self._source())
        self.assertTrue(Servicio.objects.filter(nombre="Manicure").exists())
        self.assertTrue(Servicio.objects.filter(nombre="Corte").exists())

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_actualiza_servicio_existente_sin_duplicar(self, mock_crawl, mock_extract):
        Servicio.objects.create(nombre="corte", duracion_min=20)
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [{"nombre": "Corte", "duracion_min": 30, "precio": None}], "sucursales": []}
        run_scrape(self._source())
        self.assertEqual(Servicio.objects.count(), 1)
        self.assertEqual(Servicio.objects.first().duracion_min, 30)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_precio_con_formato_no_numerico_no_aborta_la_corrida(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [
                {"nombre": "Mantencion 15 dias", "duracion_min": None, "precio": "gratuito"},
                {"nombre": "Cambio de aceite", "duracion_min": None, "precio": "$11.990.000"},
            ],
            "sucursales": [],
        }
        run = run_scrape(self._source())
        self.assertEqual(run.estado, "ok")
        self.assertEqual(Servicio.objects.get(nombre="Mantencion 15 dias").precio, 0)
        self.assertEqual(Servicio.objects.get(nombre="Cambio de aceite").precio, 11990000)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_paginas_con_error_se_guardan_en_el_run(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([], [{"url": "https://x.cl/rota", "error": "timeout"}])
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run = run_scrape(self._source())
        self.assertEqual(run.paginas_con_error, [{"url": "https://x.cl/rota", "error": "timeout"}])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_matchea_servicio_existente_pese_a_acentos_y_puntuacion(self, mock_crawl, mock_extract):
        # Bug real detectado en produccion (wsp_demo, renault.cl): el LLM
        # nombra el mismo servicio con variaciones de acentos/mayusculas/
        # puntuacion entre corridas ("Garantía legal 3×3" vs "Garantia legal
        # 3x3") y antes cada variacion creaba una fila nueva en vez de
        # actualizar la existente.
        Servicio.objects.create(nombre="Garantía legal 3×3")
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [{"nombre": "Garantia legal 3x3", "duracion_min": None, "precio": 0}],
            "sucursales": [],
        }
        run_scrape(self._source())
        self.assertEqual(Servicio.objects.count(), 1)
        self.assertEqual(Servicio.objects.first().precio, 0)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_matchea_sucursal_existente_por_direccion_aunque_el_nombre_difiera(
        self, mock_crawl, mock_extract
    ):
        # Bug real detectado en produccion (wsp_demo, renault.cl): la misma
        # sucursal aparecia con nombres distintos segun la pagina/chunk de
        # origen (ej. "centro La Dehesa AUTOKAS" vs "AUTOKAS" vs "Autokas -
        # La Dehesa"), todas con la MISMA direccion -- antes cada nombre
        # distinto creaba una fila nueva. La direccion es una señal mas
        # confiable de que es el mismo lugar.
        Sucursal.objects.create(
            nombre="centro La Dehesa AUTOKAS",
            direccion="Av. Jose Alcalde Delano #10501, Lo Barnechea",
            horario_texto="Lun a Jue 08:30 a 18:30 hrs.",
        )
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [
                {
                    "nombre": "AUTOKAS",
                    "direccion": "Av. Jose Alcalde Delano #10501, Lo Barnechea",
                    "horario_texto": None,
                }
            ],
        }
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 1)
        sucursal = Sucursal.objects.first()
        self.assertEqual(sucursal.nombre, "centro La Dehesa AUTOKAS")
        self.assertEqual(sucursal.horario_texto, "Lun a Jue 08:30 a 18:30 hrs.")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_no_crea_sucursal_nueva_sin_direccion(self, mock_crawl, mock_extract):
        # Regresion real del ScrapeRun 86 (2026-09-01): el scrape de Astara
        # creo 8 sucursales nuevas basura -- 5 duplicados tipo "ASTARA
        # RETAIL (CANTAGALLO)" y encabezados de pagina como "Sala de
        # ventas" -- todas SIN direccion, y todas visibles al cliente por
        # WhatsApp via listar_catalogo. Es la SEGUNDA vez que pasa: las
        # mismas filas se borraron a mano el 2026-08-27 sin arreglar la
        # causa. Una sede sin direccion no se puede geocodificar
        # (bot/signals.py) ni aparece en buscar_sucursales_cercanas (filtra
        # por latitud no nula) -- no aporta nada y solo ensucia.
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [
                {"nombre": "Sala de ventas", "direccion": None, "horario_texto": None},
                {"nombre": "ASTARA RETAIL (CANTAGALLO)", "direccion": "", "horario_texto": None},
            ],
        }
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 0)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_si_crea_sucursal_nueva_cuando_trae_direccion(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [
                {"nombre": "Movicenter", "direccion": "Av. Américo Vespucio 1155, Huechuraba.",
                 "horario_texto": None},
            ],
        }
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 1)
        self.assertEqual(Sucursal.objects.first().nombre, "Movicenter")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_sin_direccion_igual_actualiza_una_sucursal_que_ya_existe(self, mock_crawl, mock_extract):
        # El guard es solo para CREAR: si la sede ya existe, un scrape que
        # trae su horario (pero no la direccion) igual debe poder
        # completarlo -- no se pierde informacion util.
        Sucursal.objects.create(nombre="Movicenter", direccion="Av. Américo Vespucio 1155, Huechuraba.")
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [
                {"nombre": "Movicenter", "direccion": None, "horario_texto": "Lun a Vie 09:00 a 19:00"},
            ],
        }
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 1)
        self.assertEqual(Sucursal.objects.first().horario_texto, "Lun a Vie 09:00 a 19:00")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_guarda_las_imagenes_capturadas_por_pagina(self, mock_crawl, mock_extract):
        mock_crawl.return_value = (
            [{"url": "https://x.cl/modelo/koleos/", "texto": "koleos", "imagenes": [{"url": "https://x.cl/koleos.webp", "alt": "techno"}]}],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run = run_scrape(self._source())
        pagina = run.pages.get()
        self.assertEqual(pagina.imagenes, [{"url": "https://x.cl/koleos.webp", "alt": "techno"}])

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_guarda_las_secciones_de_cada_pagina(self, mock_crawl, mock_extract, mock_indexar):
        mock_crawl.return_value = (
            [{"url": "https://x.cl/", "texto": "hola", "secciones": [{"titulo": "Hola", "texto": "hola"}]}],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}

        run_scrape(self._source())

        pagina = ScrapedPage.objects.get(url="https://x.cl/")
        self.assertEqual(pagina.secciones, [{"titulo": "Hola", "texto": "hola"}])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_crea_vehiculo_con_specs_precio_y_url_fuente(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://renault.cl/cotizar/koleos/techno-2-0t/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Koleos", "version": "techno 2.0T", "precio": "$27.990.000",
                "specs": {"motor": "2.0 turbo"}, "url_fuente": "https://renault.cl/cotizar/koleos/techno-2-0t/",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Koleos", version="techno 2.0T")
        self.assertEqual(vehiculo.precio, 27990000)
        self.assertEqual(vehiculo.specs, {"motor": "2.0 turbo"})
        self.assertEqual(vehiculo.url_fuente, "https://renault.cl/cotizar/koleos/techno-2-0t/")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_vehiculo_usa_la_ficha_tecnica_descubierta_como_url_fuente(self, mock_crawl, mock_extract):
        # crawl() (bot/scraping/crawler.py) descubre la ficha tecnica PDF de
        # la pagina de modelo y la agrega a `paginas` con una clave
        # "ficha_tecnica_de" apuntando a esa pagina -- ver
        # docs/PENDIENTES.md, seccion "[URGENTE] Astara -- auditoría
        # completa de catálogo", punto 5. No existe un campo dedicado en
        # VehiculoCatalogo: el sistema YA trata url_fuente terminado en
        # .pdf como "hay ficha tecnica real" (ver
        # bot/business/ventas.py::_resolver_ficha_tecnica_url/
        # _vehiculo_a_dict) -- el fix es que url_fuente termine apuntando
        # al PDF, no a la pagina HTML que lo menciona.
        mock_crawl.return_value = (
            [
                {"url": "https://astararetail.cl/mitsubishi-outlander-phev/", "texto": "precio $23.990.000"},
                {
                    "url": "https://astararetail.cl/wp-content/uploads/2024/10/ficha-outlander-phev.pdf",
                    "texto": "motor 2.4 hibrido enchufable, 221 hp, autonomia electrica 55 km",
                    "ficha_tecnica_de": "https://astararetail.cl/mitsubishi-outlander-phev/",
                },
            ],
            [],
        )
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Outlander Phev", "version": "4x4 AT GLS", "precio": "$23.990.000",
                "specs": {}, "url_fuente": "https://astararetail.cl/mitsubishi-outlander-phev/",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Outlander Phev")
        self.assertEqual(
            vehiculo.url_fuente,
            "https://astararetail.cl/wp-content/uploads/2024/10/ficha-outlander-phev.pdf",
        )

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_sin_ficha_tecnica_descubierta_no_cambia_url_fuente(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/jeep-compass/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Jeep Compass", "version": "Sport", "precio": "$25.990.000",
                "specs": {}, "url_fuente": "https://astararetail.cl/jeep-compass/",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Jeep Compass")
        self.assertEqual(vehiculo.url_fuente, "https://astararetail.cl/jeep-compass/")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_seminuevo_matchea_por_nombre_aunque_la_url_no_coincida(self, mock_crawl, mock_extract):
        # Reproduce la falla REAL del ScrapeRun 86 (2026-09-01, quedo en 0
        # equipamiento): la ficha individual vive en /ficha/<id>, pero el
        # LLM extrae el vehiculo desde la pagina de LISTADO, asi que su
        # url_fuente es la del listado y el join por URL nunca matchea.
        # El match tiene que ser por nombre normalizado.
        url_ficha = "https://astararetail.cl/seminuevos/seminuevos/ficha/1282567?&page=1"
        url_listado = "https://astararetail.cl/seminuevos/seminuevos?promocion=the%20market"
        mock_crawl.return_value = (
            [
                {"url": url_listado, "texto": "listado"},
                {
                    "url": url_ficha, "texto": "ficha",
                    "estructurados": {"seminuevo": {
                        "nombre": "ALFA ROMEO TONALE",
                        "equipamiento": ["6 Air bag", "Alarma"],
                        "descripcion": "...",
                        "precio_contado": "$42.990.000",
                        "precio_financiado": "$41.990.000",
                    }},
                },
            ],
            [],
        )
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Alfa Romeo Tonale", "version": "", "precio": "$42.990.000",
                "specs": {}, "url_fuente": url_listado,
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Alfa Romeo Tonale")
        self.assertEqual(vehiculo.specs["equipamiento"], ["6 Air bag", "Alarma"])
        self.assertEqual(vehiculo.precio_contado, 42990000)
        self.assertEqual(vehiculo.precio_financiado, 41990000)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_seminuevo_no_aplica_datos_de_otro_modelo(self, mock_crawl, mock_extract):
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/seminuevos/seminuevos/ficha/999", "texto": "ficha",
                "estructurados": {"seminuevo": {
                    "nombre": "BYD SEAL", "equipamiento": ["Alarma"], "descripcion": "...",
                    "precio_contado": "$30.000.000", "precio_financiado": None,
                }},
            }],
            [],
        )
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Chery Tiggo 8", "version": "", "precio": "$20.000.000",
                "specs": {}, "url_fuente": "https://astararetail.cl/seminuevos/seminuevos?promocion=x",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Chery Tiggo 8")
        self.assertNotIn("equipamiento", vehiculo.specs)
        self.assertIsNone(vehiculo.precio_contado)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_seminuevo_completa_equipamiento_y_precios_faltantes(self, mock_crawl, mock_extract):
        # estructurados["seminuevo"] (bot/scraping/estructurados.py::
        # _detectar_datos_seminuevo) llega en la misma pagina de la ficha
        # de seminuevo -- ver docs/PENDIENTES.md, seccion "[URGENTE] Astara
        # -- auditoría completa de catálogo", punto 5 (b). El LLM no trajo
        # precio_contado/precio_financiado ni equipamiento (no se
        # estructuran listas de equipamiento a proposito, ver el prompt de
        # bot/scraping/extractor.py) -- deben completarse desde acá.
        url = "https://astararetail.cl/seminuevos/seminuevos/ficha/123"
        mock_crawl.return_value = (
            [{
                "url": url, "texto": "hola",
                "estructurados": {
                    "seminuevo": {
                        "nombre": "ALFA ROMEO TONALE",
                        "equipamiento": ["6 Air bag", "Alarma"],
                        "descripcion": "...",
                        "precio_contado": "$42.990.000",
                        "precio_financiado": "$41.990.000",
                    },
                },
            }],
            [],
        )
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Alfa Romeo Tonale", "version": "1.3 Tributo PHEV Hybrid",
                "precio": "$42.990.000", "specs": {}, "url_fuente": url,
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Alfa Romeo Tonale")
        self.assertEqual(vehiculo.precio_contado, 42990000)
        self.assertEqual(vehiculo.precio_financiado, 41990000)
        self.assertEqual(vehiculo.specs["equipamiento"], ["6 Air bag", "Alarma"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_seminuevo_no_pisa_precios_que_el_llm_ya_trajo(self, mock_crawl, mock_extract):
        # "Complementa, no reemplaza": si el LLM ya trajo un precio_contado/
        # precio_financiado (ej. de otra seccion de la misma pagina), el
        # dato deterministico de #dDescripcion no lo pisa.
        url = "https://astararetail.cl/seminuevos/seminuevos/ficha/456"
        mock_crawl.return_value = (
            [{
                "url": url, "texto": "hola",
                "estructurados": {
                    "seminuevo": {
                        "equipamiento": ["Alarma"], "descripcion": "...",
                        "precio_contado": "$99.999.999", "precio_financiado": "$88.888.888",
                    },
                },
            }],
            [],
        )
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Compass", "version": "", "precio": "$20.000.000",
                "precio_contado": "$19.000.000", "precio_financiado": "$18.000.000",
                "specs": {}, "url_fuente": url,
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Compass")
        self.assertEqual(vehiculo.precio_contado, 19000000)
        self.assertEqual(vehiculo.precio_financiado, 18000000)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_vehiculo_de_seminuevos_se_etiqueta_como_usado(self, mock_crawl, mock_extract):
        # Bug real (docs/PENDIENTES.md, auditoria previa a la prueba de
        # Astara del 2026-09-01): la seccion de seminuevos del sitio usa
        # nombres genericos que colisionan con el 0km real -- etiquetar por
        # URL de origen es lo que permite excluirlos despues en
        # bot/business/ventas.py::_buscar_en_catalogo.
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/seminuevos/seminuevos?promocion=x", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Compass", "version": "", "precio": "$15.990.000", "specs": {},
                "url_fuente": "https://astararetail.cl/seminuevos/seminuevos?promocion=x",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Compass")
        self.assertEqual(vehiculo.condicion, "usado")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_vehiculo_de_pagina_normal_se_etiqueta_como_0km(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/jeep-compass/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Jeep Compass", "version": "Sport", "precio": "$25.990.000", "specs": {},
                "url_fuente": "https://astararetail.cl/jeep-compass/",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Jeep Compass")
        self.assertEqual(vehiculo.condicion, "0km")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_guarda_los_3_tiers_de_precio_por_separado(self, mock_crawl, mock_extract):
        # Bug real (docs/PENDIENTES.md, auditoria previa a la prueba de
        # Astara del 2026-09-01): el sitio publica 3 precios del mismo
        # vehiculo (lista/contado/financiado) -- deben guardarse en 3 campos
        # separados, nunca mezclados en "precio".
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/jeep-avenger/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{
                "modelo": "Jeep Avenger", "version": "ALTITUDE 1.2 HÍBRIDO AT",
                "precio": "$24.990.000", "precio_contado": "$21.990.000", "precio_financiado": "$17.990.000",
                "specs": {}, "url_fuente": "https://astararetail.cl/jeep-avenger/",
            }],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get(modelo="Jeep Avenger")
        self.assertEqual(vehiculo.precio, 24_990_000)
        self.assertEqual(vehiculo.precio_contado, 21_990_000)
        self.assertEqual(vehiculo.precio_financiado, 17_990_000)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_vehiculo_existente_agrega_specs_nuevos_sin_perder_los_previos(self, mock_crawl, mock_extract):
        VehiculoCatalogo.objects.create(modelo="Koleos", version="techno 2.0T", specs={"motor": "2.0 turbo"})
        mock_crawl.return_value = ([{"url": "https://renault.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{"modelo": "koleos", "version": "Techno 2.0t", "precio": None, "specs": {"torque_nm": "325 Nm"}}],
        }
        run_scrape(self._source())
        self.assertEqual(VehiculoCatalogo.objects.count(), 1)
        vehiculo = VehiculoCatalogo.objects.first()
        self.assertEqual(vehiculo.specs, {"motor": "2.0 turbo", "torque_nm": "325 Nm"})

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_no_borra_vehiculo_existente_no_mencionado(self, mock_crawl, mock_extract):
        VehiculoCatalogo.objects.create(modelo="Arkana", version="intens turbo")
        mock_crawl.return_value = ([{"url": "https://renault.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}
        run_scrape(self._source())
        self.assertTrue(VehiculoCatalogo.objects.filter(modelo="Arkana").exists())

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_no_sobrescribe_specs_existentes_con_null_del_nuevo_scrape(self, mock_crawl, mock_extract):
        # Verificar que si un nuevo scrape trae specs con valores null explícitos,
        # no sobrescriben specs existentes con valores no-null de scrapes anteriores.
        VehiculoCatalogo.objects.create(modelo="Koleos", version="techno 2.0T", specs={"motor": "2.0 turbo"})
        mock_crawl.return_value = ([{"url": "https://renault.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{"modelo": "Koleos", "version": "techno 2.0T", "precio": None, "specs": {"motor": None, "torque_nm": "325 Nm"}}],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.first()
        # El motor existente debe sobrevivir porque el nuevo scrape trae null
        # El torque_nm nuevo debe agregarse
        self.assertEqual(vehiculo.specs, {"motor": "2.0 turbo", "torque_nm": "325 Nm"})

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_trunca_sucursal_nueva_nombre_direccion_horario_a_su_max_length(
        self, mock_crawl, mock_extract,
    ):
        # Bug real en produccion (2026-08-28, docs/PENDIENTES.md): una
        # sucursal de Astara con un horario_texto largo ("Horario Ventas:
        # Lunes a Jueves: ... Sabados: ...") supero los 200 chars de
        # Sucursal.horario_texto y SQL Server (mssql-django, backend real)
        # aborto la corrida entera con "String or binary data would be
        # truncated" -- sqlite (tests) no lo detecta porque no valida
        # max_length en save(). Esta rama (upsert por LLM, la de SIEMPRE,
        # no la estructurada de bot/scraping/estructurados.py que ya trunca
        # desde la ronda de fix anterior) nunca trunco.
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [{
                "nombre": "N" * 250, "direccion": "D" * 350, "horario_texto": "H" * 250,
            }],
        }
        run_scrape(self._source())
        sucursal = Sucursal.objects.get()
        self.assertEqual(len(sucursal.nombre), 200)
        self.assertEqual(len(sucursal.direccion), 300)
        self.assertEqual(len(sucursal.horario_texto), 200)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_trunca_sucursal_existente_al_actualizarla(self, mock_crawl, mock_extract):
        # Mismo bug que el test de arriba, pero en la rama de UPDATE (la
        # sucursal ya existe y matchea por direccion) -- codigo distinto
        # (setattr en un loop, no Sucursal.objects.create()), necesita su
        # propia cobertura.
        Sucursal.objects.create(nombre="Cantagallo", direccion="Av. Las Condes 12256, Vitacura")
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [{
                "nombre": "Cantagallo",
                "direccion": "Av. Las Condes 12256, Vitacura",
                "horario_texto": "H" * 250,
            }],
        }
        run_scrape(self._source())
        sucursal = Sucursal.objects.get()
        self.assertEqual(len(sucursal.horario_texto), 200)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_trunca_modelo_version_y_url_fuente_a_su_max_length(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://renault.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{"modelo": "K" * 150, "version": "V" * 200, "precio": None, "specs": {}, "url_fuente": "https://x.cl/" + "a" * 250}],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get()
        self.assertEqual(len(vehiculo.modelo), 100)
        self.assertEqual(len(vehiculo.version), 150)
        self.assertLessEqual(len(vehiculo.url_fuente), 200)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_upsert_descarta_precio_inverosimil_por_concatenacion_de_digitos(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://renault.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [], "sucursales": [],
            "vehiculos": [{"modelo": "Koleos", "version": "techno 2.0T", "precio": "desde $27.990.000 (bono $500.000)", "specs": {}}],
        }
        run_scrape(self._source())
        vehiculo = VehiculoCatalogo.objects.get()
        self.assertIsNone(vehiculo.precio)

    def test_scraped_page_ordena_las_mas_antiguas_primero(self):
        source = self._source()
        run = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        p1 = ScrapedPage.objects.create(run=run, url="https://x.cl/1", texto="a")
        p2 = ScrapedPage.objects.create(run=run, url="https://x.cl/2", texto="b")
        p3 = ScrapedPage.objects.create(run=run, url="https://x.cl/3", texto="c")
        self.assertEqual(list(ScrapedPage.objects.all()), [p1, p2, p3])

    def test_scraped_page_es_documento_default_false(self):
        run = ScrapeRun.objects.create(source=self._source(), url="https://x.cl/", estado="ok")
        pagina = ScrapedPage.objects.create(run=run, url="https://x.cl/pagina", texto="hola")
        self.assertFalse(pagina.es_documento)

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_guarda_es_documento_de_cada_pagina(self, mock_crawl, mock_extract, mock_indexar):
        mock_crawl.return_value = (
            [
                {"url": "https://x.cl/ficha.pdf", "texto": "specs", "es_documento": True},
                {"url": "https://x.cl/pagina", "texto": "hola", "es_documento": False},
            ],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}

        run_scrape(self._source())

        paginas = {p.url: p.es_documento for p in ScrapedPage.objects.all()}
        self.assertTrue(paginas["https://x.cl/ficha.pdf"])
        self.assertFalse(paginas["https://x.cl/pagina"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_de_fuente_astara_etiqueta_el_catalogo_nuevo_como_astara(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [{"nombre": "Mantención", "duracion_min": 60, "precio": None}],
            "sucursales": [{"nombre": "Vitacura", "direccion": "Av. Kennedy 5413", "horario_texto": None}],
            "vehiculos": [{"modelo": "Compass", "version": "Longitude", "precio": None, "specs": {}}],
        }
        source = ScrapingSource.todos_los_clientes.create(url="https://astararetail.cl/", cliente="astara")
        run_scrape(source)
        self.assertEqual(Servicio.todos_los_clientes.get(nombre="Mantención").cliente, "astara")
        self.assertEqual(Sucursal.todos_los_clientes.get(nombre="Vitacura").cliente, "astara")
        self.assertEqual(VehiculoCatalogo.todos_los_clientes.get(modelo="Compass").cliente, "astara")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_de_fuente_astara_no_matchea_ni_toca_catalogo_existente_de_renault(
        self, mock_crawl, mock_extract,
    ):
        # Mismo nombre normalizado que un servicio/vehiculo real de Renault -- el
        # upsert de la fuente astara no debe encontrarlo (esta escopeado por
        # cliente) y por lo tanto debe crear una fila nueva, nunca actualizar la
        # de Renault.
        servicio_renault = Servicio.objects.create(nombre="Mantención", duracion_min=30, precio=15000)
        vehiculo_renault = VehiculoCatalogo.objects.create(modelo="Koleos", version="Zen", precio=20000000)
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [{"nombre": "Mantención", "duracion_min": 60, "precio": 99999}],
            "sucursales": [],
            "vehiculos": [{"modelo": "Koleos", "version": "Zen", "precio": 1, "specs": {}}],
        }
        source = ScrapingSource.todos_los_clientes.create(url="https://astararetail.cl/", cliente="astara")
        run_scrape(source)

        servicio_renault.refresh_from_db()
        vehiculo_renault.refresh_from_db()
        self.assertEqual(servicio_renault.duracion_min, 30)
        self.assertEqual(servicio_renault.precio, 15000)
        self.assertEqual(vehiculo_renault.precio, 20000000)
        self.assertEqual(Servicio.todos_los_clientes.filter(nombre="Mantención").count(), 2)
        self.assertEqual(VehiculoCatalogo.todos_los_clientes.filter(modelo="Koleos", version="Zen").count(), 2)

    @patch("bot.scraping.runner.indexar_pagina_en_supabase")
    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_de_fuente_astara_indexa_al_rag_con_cliente_astara(
        self, mock_crawl, mock_extract, mock_indexar,
    ):
        # Regresion directa del bug real 2026-08-27/28 (docs/PENDIENTES.md):
        # indexar_pagina_en_supabase se llamaba sin cliente, dependiendo en
        # silencio de RAG_SCHEMA (env var global de proceso) -- un scrape de
        # Astara con el proceso todavia sirviendo Renault (RAG_SCHEMA=renault)
        # mando 297 chunks de contenido de Astara al schema renault en
        # Supabase, confirmado y purgado contra el proyecto real.
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": [], "vehiculos": []}
        source = ScrapingSource.todos_los_clientes.create(url="https://astararetail.cl/", cliente="astara")

        run_scrape(source)

        mock_indexar.assert_called_once()
        self.assertEqual(mock_indexar.call_args.kwargs.get("cliente"), "astara")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_run_scrape_sin_source_asociado_al_run_usa_renault_por_default(self, mock_crawl, mock_extract):
        # execute_scrape() tambien se puede llamar sobre un ScrapeRun sin
        # source_id (campo nullable, ver ScrapeRun.source) -- el upsert no debe
        # reventar en ese caso, y debe conservar el comportamiento de siempre
        # (todo el catalogo historico es renault).
        mock_crawl.return_value = ([{"url": "https://x.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {
            "servicios": [{"nombre": "Lavado", "duracion_min": 20, "precio": None}],
            "sucursales": [],
        }
        from bot.scraping.runner import execute_scrape
        run = ScrapeRun.objects.create(source=None, url="https://x.cl/", estado="corriendo")
        execute_scrape(run)
        self.assertEqual(run.estado, "ok")
        self.assertEqual(Servicio.objects.get(nombre="Lavado").cliente, "renault")

    def test_upsert_catalogo_cliente_es_obligatorio(self):
        with self.assertRaises(TypeError):
            _upsert_catalogo({"servicios": [], "sucursales": []})

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_estructurado_crea_sucursal_nueva_con_categorias(self, mock_crawl, mock_extract):
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                "estructurados": {"sucursales": [{
                    "nombre": "Cantagallo", "direccion": "Av. Las Condes 12.256, Vitacura.",
                    "horario_texto": "Horario Ventas: ...", "categorias": ["ventas", "servicio_tecnico"],
                }]},
            }],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run_scrape(self._source())
        sucursal = Sucursal.objects.get(nombre="Cantagallo")
        self.assertEqual(sorted(sucursal.categorias), ["servicio_tecnico", "ventas"])
        self.assertIsNotNone(sucursal.categorias_actualizado_en)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_estructurado_corrige_sucursal_que_el_llm_ya_creo_con_direccion_similar(
        self, mock_crawl, mock_extract,
    ):
        # El LLM (extract_catalog) ya creo la sucursal con "Av." abreviado;
        # el paso estructurado la encuentra por _normalizar_direccion (que
        # SI expande abreviaturas) y la corrige/completa en vez de duplicar.
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                "estructurados": {"sucursales": [{
                    "nombre": "Cantagallo", "direccion": "Avenida Las Condes 12256, Vitacura",
                    "horario_texto": "...", "categorias": ["ventas"],
                }]},
            }],
            [],
        )
        mock_extract.return_value = {
            "servicios": [],
            "sucursales": [{"nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura", "horario_texto": None}],
        }
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 1)
        sucursal = Sucursal.objects.first()
        self.assertEqual(sucursal.categorias, ["ventas"])
        # La correccion no es solo de categorias: la direccion tambien queda
        # con el valor estructurado (mas confiable que el del LLM).
        self.assertEqual(sucursal.direccion, "Avenida Las Condes 12256, Vitacura")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_categorias_se_reemplazan_no_se_unen_entre_corridas(self, mock_crawl, mock_extract):
        sucursal = Sucursal.objects.create(
            nombre="Cantagallo", direccion="Av. Las Condes 12256, Vitacura",
            categorias=["ventas", "servicio_tecnico"],
        )
        # Esta corrida solo confirma "ventas" para esa direccion -- Servicio
        # Tecnico se saco del sitio real.
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                "estructurados": {"sucursales": [{
                    "nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura",
                    "horario_texto": "...", "categorias": ["ventas"],
                }]},
            }],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run_scrape(self._source())
        sucursal.refresh_from_db()
        self.assertEqual(sucursal.categorias, ["ventas"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_sucursal_no_mencionada_en_esta_corrida_no_pierde_sus_categorias(
        self, mock_crawl, mock_extract,
    ):
        sucursal = Sucursal.objects.create(
            nombre="Movicenter", direccion="Av. Américo Vespucio 1155, Huechuraba",
            categorias=["ventas"],
        )
        # Esta corrida no toca Movicenter para nada (crawl parcial, o la
        # pagina no se pudo visitar esta vez).
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/", "texto": "x"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run_scrape(self._source())
        sucursal.refresh_from_db()
        self.assertEqual(sucursal.categorias, ["ventas"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_pagina_sin_estructurados_no_rompe_el_scrape(self, mock_crawl, mock_extract):
        # La mayoria de las paginas de un crawl real (fichas de vehiculo,
        # etc.) no tienen "estructurados" en absoluto -- confirma que
        # execute_scrape maneja bien pages.get("estructurados") faltante,
        # no solo vacio.
        mock_crawl.return_value = ([{"url": "https://astararetail.cl/jeep/", "texto": "x"}], [])
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run = run_scrape(self._source())
        self.assertEqual(run.estado, "ok")

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_estructurado_trunca_nombre_direccion_y_horario_a_su_max_length(
        self, mock_crawl, mock_extract,
    ):
        # Sucursal.nombre=200, direccion=300, horario_texto=200. sqlite acepta
        # el overflow en silencio, pero el backend real es SQL Server
        # (mssql-django) y revienta duro, abortando execute_scrape a mitad de
        # camino (el upsert de vehiculos nunca corre). Mismo patron de
        # truncado que ya usan modelo/version/url_fuente mas abajo.
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/sucursal-larga/", "texto": "x",
                "estructurados": {"sucursales": [{
                    "nombre": "N" * 250, "direccion": "D" * 350,
                    "horario_texto": "H" * 250, "categorias": ["ventas"],
                }]},
            }],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run = run_scrape(self._source())
        self.assertEqual(run.estado, "ok")
        sucursal = Sucursal.objects.get()
        self.assertEqual(len(sucursal.nombre), 200)
        self.assertEqual(len(sucursal.direccion), 300)
        self.assertEqual(len(sucursal.horario_texto), 200)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_estructurado_trunca_tambien_al_actualizar_una_sucursal_ya_existente(
        self, mock_crawl, mock_extract,
    ):
        # La rama de UPDATE (sucursal ya existente matcheada por direccion
        # normalizada) tambien escribe direccion/horario_texto -- tiene que
        # truncar igual que la de create.
        sucursal = Sucursal.objects.create(nombre="Larga", direccion="D" * 300)
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/sucursal-larga/", "texto": "x",
                "estructurados": {"sucursales": [{
                    "nombre": "Larga", "direccion": "D" * 350,
                    "horario_texto": "H" * 250, "categorias": ["ventas"],
                }]},
            }],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run = run_scrape(self._source())
        self.assertEqual(run.estado, "ok")
        self.assertEqual(Sucursal.objects.count(), 1)
        sucursal.refresh_from_db()
        self.assertEqual(len(sucursal.direccion), 300)
        self.assertEqual(len(sucursal.horario_texto), 200)

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_dos_paginas_con_la_misma_direccion_en_una_corrida_unen_sus_categorias(
        self, mock_crawl, mock_extract,
    ):
        # Spec, "Vigencia de categorias" bullet 1: DENTRO de una misma corrida,
        # dos paginas distintas que resuelven a la misma direccion normalizada
        # se UNEN -- la segunda no pisa a la primera.
        mock_crawl.return_value = (
            [
                {
                    "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                    "estructurados": {"sucursales": [{
                        "nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura",
                        "horario_texto": "Horario Ventas: ...", "categorias": ["ventas"],
                    }]},
                },
                {
                    "url": "https://astararetail.cl/servicio-tecnico-cantagallo/", "texto": "y",
                    "estructurados": {"sucursales": [{
                        "nombre": "Cantagallo Servicio", "direccion": "Avenida Las Condes 12256, Vitacura",
                        "horario_texto": "Horario Servicio Técnico: ...", "categorias": ["servicio_tecnico"],
                    }]},
                },
            ],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 1)
        sucursal = Sucursal.objects.get()
        self.assertEqual(sucursal.categorias, ["ventas", "servicio_tecnico"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_la_union_intra_corrida_no_se_arrastra_a_la_corrida_siguiente(
        self, mock_crawl, mock_extract,
    ):
        # El set de "claves ya escritas" es por corrida: la corrida siguiente
        # tiene que REEMPLAZAR (no seguir uniendo) aunque la anterior haya
        # unido dos paginas para esa misma direccion.
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        source = self._source()
        mock_crawl.return_value = (
            [
                {
                    "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                    "estructurados": {"sucursales": [{
                        "nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura",
                        "horario_texto": "a", "categorias": ["ventas"],
                    }]},
                },
                {
                    "url": "https://astararetail.cl/servicio-tecnico-cantagallo/", "texto": "y",
                    "estructurados": {"sucursales": [{
                        "nombre": "Cantagallo", "direccion": "Avenida Las Condes 12256, Vitacura",
                        "horario_texto": "b", "categorias": ["servicio_tecnico"],
                    }]},
                },
            ],
            [],
        )
        run_scrape(source)
        self.assertEqual(Sucursal.objects.get().categorias, ["ventas", "servicio_tecnico"])

        # Segunda corrida: el sitio ya no ofrece Servicio Tecnico ahi.
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                "estructurados": {"sucursales": [{
                    "nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura",
                    "horario_texto": "a", "categorias": ["ventas"],
                }]},
            }],
            [],
        )
        run_scrape(source)
        self.assertEqual(Sucursal.objects.count(), 1)
        self.assertEqual(Sucursal.objects.get().categorias, ["ventas"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_varias_sucursales_estructuradas_en_una_misma_pagina_se_aplican_todas(
        self, mock_crawl, mock_extract,
    ):
        # extraer_estructurados devuelve una LISTA a proposito: un extractor
        # futuro (ej. una pagina-listado con varias sedes) puede devolver mas
        # de un item sin tocar el pipeline. La recoleccion en execute_scrape no
        # debe quedarse solo con el primero de cada pagina.
        mock_crawl.return_value = (
            [{
                "url": "https://astararetail.cl/nuestras-sucursales/", "texto": "x",
                "estructurados": {"sucursales": [
                    {
                        "nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura",
                        "horario_texto": "a", "categorias": ["ventas"],
                    },
                    {
                        "nombre": "El Cortijo", "direccion": "Av. El Cortijo 100, Conchalí",
                        "horario_texto": "b", "categorias": ["servicio_tecnico"],
                    },
                ]},
            }],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 2)
        self.assertEqual(Sucursal.objects.get(nombre="Cantagallo").categorias, ["ventas"])
        self.assertEqual(Sucursal.objects.get(nombre="El Cortijo").categorias, ["servicio_tecnico"])

    @patch("bot.scraping.runner.extract_catalog")
    @patch("bot.scraping.runner.crawl")
    def test_estructurados_de_dos_paginas_distintas_se_aplican_ambos(
        self, mock_crawl, mock_extract,
    ):
        mock_crawl.return_value = (
            [
                {
                    "url": "https://astararetail.cl/sucursal-cantagallo/", "texto": "x",
                    "estructurados": {"sucursales": [{
                        "nombre": "Cantagallo", "direccion": "Av. Las Condes 12256, Vitacura",
                        "horario_texto": "a", "categorias": ["ventas"],
                    }]},
                },
                {
                    "url": "https://astararetail.cl/sucursal-el-cortijo/", "texto": "y",
                    "estructurados": {"sucursales": [{
                        "nombre": "El Cortijo", "direccion": "Av. El Cortijo 100, Conchalí",
                        "horario_texto": "b", "categorias": ["servicio_tecnico"],
                    }]},
                },
            ],
            [],
        )
        mock_extract.return_value = {"servicios": [], "sucursales": []}
        run_scrape(self._source())
        self.assertEqual(Sucursal.objects.count(), 2)
        self.assertEqual(Sucursal.objects.get(nombre="Cantagallo").categorias, ["ventas"])
        self.assertEqual(Sucursal.objects.get(nombre="El Cortijo").categorias, ["servicio_tecnico"])
