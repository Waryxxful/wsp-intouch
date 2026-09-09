import io
import tempfile
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from bot.models import ImagenConvertida, ScrapingSource
from bot.scraping.imagenes import _buscar_url_original, _convertir_a_jpeg, resolver_imagen_modelo


def _png_bytes(mode="RGBA", color=(255, 0, 0, 128)):
    buf = io.BytesIO()
    Image.new(mode, (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


class BuscarUrlOriginalTest(TestCase):
    def test_prioriza_pagina_de_modelo_sobre_pagina_que_solo_menciona_el_slug(self):
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = source.runs.create(url=source.url, estado="ok")
        run.pages.create(
            url="https://renault.cl/cotizar/koleos/techno/",
            texto="cotizar",
            imagenes=[{"url": "https://renault.cl/logo-generico.png", "alt": ""}],
        )
        run.pages.create(
            url="https://renault.cl/modelo/koleos/",
            texto="koleos",
            imagenes=[{"url": "https://renault.cl/koleos-real.webp", "alt": "techno 2.0t"}],
        )
        self.assertEqual(_buscar_url_original("koleos"), "https://renault.cl/koleos-real.webp")

    def test_sin_ninguna_pagina_con_el_slug_devuelve_none(self):
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = source.runs.create(url=source.url, estado="ok")
        run.pages.create(url="https://renault.cl/modelo/arkana/", texto="arkana", imagenes=[{"url": "x", "alt": ""}])
        self.assertIsNone(_buscar_url_original("koleos"))

    def test_pagina_sin_imagenes_no_es_candidata(self):
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = source.runs.create(url=source.url, estado="ok")
        run.pages.create(url="https://renault.cl/modelo/koleos/", texto="koleos", imagenes=[])
        self.assertIsNone(_buscar_url_original("koleos"))

    def test_slug_en_mayusculas_o_mixto_igual_resuelve_la_pagina(self):
        # El LLM que genera el slug recibe la instruccion de devolverlo en
        # minusculas, pero eso es solo una instruccion de prompt, no una
        # garantia -- un slug como "Koleos" no debe silenciosamente no
        # encontrar nada solo porque pagina.url se compara en minusculas.
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = source.runs.create(url=source.url, estado="ok")
        run.pages.create(
            url="https://renault.cl/modelo/koleos/",
            texto="koleos",
            imagenes=[{"url": "https://renault.cl/koleos-real.webp", "alt": "techno 2.0t"}],
        )
        self.assertEqual(_buscar_url_original("Koleos"), "https://renault.cl/koleos-real.webp")


class ConvertirAJpegTest(TestCase):
    def test_convierte_png_con_alpha_a_jpeg_rgb(self):
        jpeg_bytes = _convertir_a_jpeg(_png_bytes())
        imagen = Image.open(io.BytesIO(jpeg_bytes))
        self.assertEqual(imagen.format, "JPEG")
        self.assertEqual(imagen.mode, "RGB")


@override_settings(PUBLIC_BASE_URL="https://qadash.in-touchcrm.cl/wsp")
class ResolverImagenModeloTest(TestCase):
    def setUp(self):
        self._media_root = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media_root)
        self._override.enable()
        self.addCleanup(self._override.disable)

        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = source.runs.create(url=source.url, estado="ok")
        run.pages.create(
            url="https://renault.cl/modelo/koleos/",
            texto="koleos",
            imagenes=[{"url": "https://renault.cl/koleos.webp", "alt": "techno"}],
        )

    def test_sin_pagina_con_el_slug_devuelve_none(self):
        self.assertIsNone(resolver_imagen_modelo("arkana"))

    @patch("bot.scraping.imagenes.httpx.get")
    def test_sin_cache_descarga_convierte_y_crea_imagenconvertida(self, mock_get):
        mock_get.return_value.content = _png_bytes()
        mock_get.return_value.raise_for_status.return_value = None

        url = resolver_imagen_modelo("koleos")

        self.assertTrue(url.startswith("https://qadash.in-touchcrm.cl/wsp/demo/media/model_images/koleos"))
        mock_get.assert_called_once_with("https://renault.cl/koleos.webp", timeout=10, follow_redirects=True)
        self.assertTrue(ImagenConvertida.objects.filter(url_original="https://renault.cl/koleos.webp").exists())

    @patch("bot.scraping.imagenes.httpx.get")
    def test_con_cache_existente_no_vuelve_a_descargar(self, mock_get):
        existente = ImagenConvertida.objects.create(url_original="https://renault.cl/koleos.webp")
        existente.archivo.save("koleos.jpg", ContentFile(_convertir_a_jpeg(_png_bytes())), save=True)

        url = resolver_imagen_modelo("koleos")

        mock_get.assert_not_called()
        self.assertIn(existente.archivo.url, url)

    @patch("bot.scraping.imagenes.httpx.get", side_effect=Exception("timeout"))
    def test_fallo_de_descarga_o_conversion_devuelve_none_sin_lanzar(self, mock_get):
        self.assertIsNone(resolver_imagen_modelo("koleos"))

    @patch("bot.scraping.imagenes.httpx.get")
    def test_cache_con_archivo_faltante_se_re_descarga_sin_duplicar_fila(self, mock_get):
        # Fila de cache con url_original ya seteada pero sin archivo guardado
        # (create() exitoso seguido de un archivo.save() fallido -- disco
        # lleno/permisos -- o el archivo borrado del disco en algun momento
        # posterior mientras la fila en BD sobrevive). Antes de la fix, el
        # cache-hit devolvia esta fila igual, fallaba con ValueError al leer
        # .archivo.url, y quedaba en None para siempre sin reintentar.
        existente = ImagenConvertida.objects.create(url_original="https://renault.cl/koleos.webp")
        self.assertFalse(existente.archivo)

        mock_get.return_value.content = _png_bytes()
        mock_get.return_value.raise_for_status.return_value = None

        url = resolver_imagen_modelo("koleos")

        mock_get.assert_called_once_with("https://renault.cl/koleos.webp", timeout=10, follow_redirects=True)
        self.assertTrue(url.startswith("https://qadash.in-touchcrm.cl/wsp/demo/media/model_images/koleos"))
        # Reutilizo la fila existente en vez de crear una nueva -- de lo
        # contrario .objects.create() con el mismo url_original (unique)
        # habria lanzado IntegrityError.
        self.assertEqual(ImagenConvertida.objects.filter(url_original="https://renault.cl/koleos.webp").count(), 1)
        existente.refresh_from_db()
        self.assertTrue(existente.archivo)

    @patch("bot.scraping.imagenes.ImagenConvertida.objects.create", side_effect=Exception("IntegrityError simulado"))
    @patch("bot.scraping.imagenes.httpx.get")
    def test_fallo_de_guardado_devuelve_none_sin_lanzar(self, mock_get, mock_create):
        mock_get.return_value.content = _png_bytes()
        mock_get.return_value.raise_for_status.return_value = None

        url = resolver_imagen_modelo("koleos")

        self.assertIsNone(url)
        mock_create.assert_called_once()
