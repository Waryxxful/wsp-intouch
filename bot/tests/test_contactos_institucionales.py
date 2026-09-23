"""Teléfono, correo y direcciones de InTouch, sacados del sitio al scrapear.

Caso real (chat 9, 2026-09-23): el gerente comercial preguntó el teléfono, el
correo y dónde estaban para una reunión presencial, y el bot respondió tres
veces que no tenía el dato. Los datos estaban en in-touch.cl y, después del
arreglo del crawler, también en el RAG, pero la búsqueda no los encontraba:
el fragmento del teléfono no llegaba a los 20 candidatos y el de la dirección
lo descartaba el rerank. Son pocos datos y fijos: van a una tabla y a la ficha
de cada turno, no a depender del ranking.
"""
import os
from unittest.mock import AsyncMock, patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from bot.scraping.crawler import _extraer_texto_y_enlaces

_HTML_SITIO = os.path.join(os.path.dirname(__file__), "fixtures", "in-touch.cl.html")


class ContactosDeEnlacesTest(SimpleTestCase):
    def test_telefono_y_correo_del_pie_salen_con_su_etiqueta(self):
        html = (
            b'<html><body><h2>Inicio</h2><p>Hola.</p>'
            b'<footer><div><div>Recursos Humanos</div>'
            b'<a href="mailto:rrhh@x.cl">rrhh@x.cl</a>'
            b'<a href="tel:+56229273619">+56 2 2927 3619</a></div></footer>'
            b'</body></html>'
        )
        _, _, _, _, estructurados = _extraer_texto_y_enlaces("https://x.cl/", html)
        self.assertEqual(estructurados["contactos"], [
            {"tipo": "correo", "valor": "rrhh@x.cl", "etiqueta": "Recursos Humanos"},
            {"tipo": "telefono", "valor": "+56 2 2927 3619", "etiqueta": "Recursos Humanos"},
        ])

    def test_telefono_fuera_del_pie_tambien_cuenta(self):
        html = b'<html><body><h2>Ventas</h2><p>Llama al <a href="tel:+5621234567">22 123 4567</a>.</p></body></html>'
        _, _, _, _, estructurados = _extraer_texto_y_enlaces("https://x.cl/", html)
        self.assertEqual([c["valor"] for c in estructurados["contactos"]], ["22 123 4567"])

    def test_enlace_sin_numero_visible_usa_el_del_href(self):
        html = "<html><body><p><a href=\"tel:+56229273619\">Llámanos</a></p></body></html>".encode()
        _, _, _, _, estructurados = _extraer_texto_y_enlaces("https://x.cl/", html)
        self.assertEqual(estructurados["contactos"][0]["valor"], "+56229273619")

    def test_mismo_contacto_repetido_sale_una_vez(self):
        html = (
            b'<html><body><p><a href="mailto:hola@x.cl">hola@x.cl</a></p>'
            b'<footer><div><a href="mailto:hola@x.cl?subject=Hola">Escr\xc3\xadbenos</a></div></footer>'
            b'</body></html>'
        )
        _, _, _, _, estructurados = _extraer_texto_y_enlaces("https://x.cl/", html)
        self.assertEqual([c["valor"] for c in estructurados["contactos"]], ["hola@x.cl"])

    def test_sitio_real_de_intouch(self):
        with open(_HTML_SITIO, "rb") as f:
            _, _, _, _, estructurados = _extraer_texto_y_enlaces("https://in-touch.cl/", f.read())
        self.assertIn(
            {"tipo": "telefono", "valor": "+56 2 2927 3619", "etiqueta": "Recursos Humanos"},
            estructurados["contactos"],
        )
        self.assertIn(
            {"tipo": "correo", "valor": "rrhh@in-touchcrm.cl", "etiqueta": "Recursos Humanos"},
            estructurados["contactos"],
        )


class ExtraerDireccionesTest(SimpleTestCase):
    _PAGINAS = [{"url": "https://in-touch.cl/", "texto": (
        "Presencia regional\n\nChile\n\nAv. Irarrázaval 2470 Ñuñoa, Santiago\n\nPerú\n\nLima, Perú"
    )}]

    def _extraer(self, respuesta_llm: str):
        from bot.scraping import institucional

        with patch.object(institucional, "_get_llm", AsyncMock()), \
             patch.object(institucional, "_ainvoke_with_retry", AsyncMock(return_value=respuesta_llm)):
            return institucional.extraer_direcciones(self._PAGINAS)

    def test_devuelve_las_direcciones_que_estan_en_la_pagina(self):
        direcciones = self._extraer(
            '{"direcciones": [{"etiqueta": "Chile", "direccion": "Av. Irarrázaval 2470, Ñuñoa, Santiago"},'
            ' {"etiqueta": "Perú", "direccion": "Lima, Perú"}]}'
        )
        self.assertEqual(direcciones, [
            {"tipo": "direccion", "valor": "Av. Irarrázaval 2470, Ñuñoa, Santiago", "etiqueta": "Chile",
             "fuente_url": "https://in-touch.cl/"},
            {"tipo": "direccion", "valor": "Lima, Perú", "etiqueta": "Perú", "fuente_url": "https://in-touch.cl/"},
        ])

    def test_descarta_una_direccion_que_no_aparece_en_el_texto(self):
        # La defensa la hace el código, no el prompt: el LLM puede completar
        # una dirección "razonable" que el sitio no publica.
        direcciones = self._extraer(
            '{"direcciones": [{"etiqueta": "Chile", "direccion": "Av. Providencia 1234, Santiago"}]}'
        )
        self.assertEqual(direcciones, [])

    def test_respuesta_que_no_es_json_levanta(self):
        with self.assertRaises(ValueError):
            self._extraer("no hay direcciones")

    def test_acepta_la_respuesta_envuelta_en_bloque_de_codigo(self):
        direcciones = self._extraer('```json\n{"direcciones": [{"etiqueta": "", "direccion": "Lima, Perú"}]}\n```')
        self.assertEqual([d["valor"] for d in direcciones], ["Lima, Perú"])


@override_settings(CLIENTE_ACTIVO="intouch")
class RunnerGuardaContactosTest(TestCase):
    def _correr(self, source, paginas, direcciones):
        from bot.scraping.runner import run_scrape

        with patch("bot.scraping.runner.crawl", return_value=(paginas, [])), \
             patch("bot.scraping.runner.indexar_pagina_en_supabase"), \
             patch("bot.scraping.runner.extraer_direcciones", return_value=direcciones):
            run = run_scrape(source)
        run.refresh_from_db()
        return run

    def _source(self):
        from bot.models import ScrapingSource

        return ScrapingSource.objects.create(url="https://in-touch.cl", cliente=settings.CLIENTE_ACTIVO)

    def _pagina(self, contactos):
        return {"url": "https://in-touch.cl/", "texto": "hola", "estructurados": {"contactos": contactos}}

    def test_guarda_telefono_correo_y_direcciones(self):
        from bot.models import ContactoInstitucional

        run = self._correr(
            self._source(),
            [self._pagina([{"tipo": "telefono", "valor": "+56 2 2927 3619", "etiqueta": "Recursos Humanos"}])],
            [{"tipo": "direccion", "valor": "Lima, Perú", "etiqueta": "Perú", "fuente_url": "https://in-touch.cl/"}],
        )
        self.assertEqual(run.estado, "ok", run.error_detalle)
        filas = sorted(ContactoInstitucional.objects.values_list("tipo", "valor", "etiqueta", "fuente_url"))
        self.assertEqual(filas, [
            ("direccion", "Lima, Perú", "Perú", "https://in-touch.cl/"),
            ("telefono", "+56 2 2927 3619", "Recursos Humanos", "https://in-touch.cl/"),
        ])

    def test_un_nuevo_scrapeo_reemplaza_los_contactos_de_esa_fuente(self):
        from bot.models import ContactoInstitucional

        source = self._source()
        self._correr(source, [self._pagina([{"tipo": "telefono", "valor": "111", "etiqueta": ""}])], [])
        self._correr(source, [self._pagina([{"tipo": "telefono", "valor": "222", "etiqueta": ""}])], [])
        self.assertEqual(list(ContactoInstitucional.objects.values_list("valor", flat=True)), ["222"])

    @patch("bot.scraping.runner.logger")
    def test_un_scrapeo_sin_contactos_conserva_los_anteriores(self, mock_logger):
        # Un sitio caído a medias, o un rediseño que movió el pie, no puede
        # dejar al bot sin teléfono en silencio.
        from bot.models import ContactoInstitucional

        source = self._source()
        self._correr(source, [self._pagina([{"tipo": "telefono", "valor": "111", "etiqueta": ""}])], [])
        run = self._correr(source, [self._pagina([])], [])
        self.assertEqual(run.estado, "ok")
        self.assertEqual(list(ContactoInstitucional.objects.values_list("valor", flat=True)), ["111"])
        mock_logger.warning.assert_called()

    def test_si_falla_la_extraccion_de_direcciones_el_run_queda_en_error_y_no_borra_nada(self):
        from bot.models import ContactoInstitucional
        from bot.scraping.runner import run_scrape

        source = self._source()
        self._correr(source, [self._pagina([{"tipo": "telefono", "valor": "111", "etiqueta": ""}])], [])
        with patch("bot.scraping.runner.crawl", return_value=([self._pagina([])], [])), \
             patch("bot.scraping.runner.indexar_pagina_en_supabase"), \
             patch("bot.scraping.runner.extraer_direcciones", side_effect=ValueError("el LLM no devolvió JSON")):
            run = run_scrape(source)
        run.refresh_from_db()
        self.assertEqual(run.estado, "error")
        self.assertIn("direcciones", run.error_detalle)
        self.assertEqual(list(ContactoInstitucional.objects.values_list("valor", flat=True)), ["111"])


class FichaDeContactoTest(TestCase):
    def _crear(self, tipo, valor, etiqueta=""):
        from bot.models import ContactoInstitucional

        ContactoInstitucional.objects.create(
            cliente=settings.CLIENTE_ACTIVO, tipo=tipo, valor=valor, etiqueta=etiqueta,
            fuente_url="https://in-touch.cl/",
        )

    def test_la_ficha_trae_los_contactos_con_su_etiqueta(self):
        from bot.flow.contexto_turno import bloque_contexto_turno

        self._crear("telefono", "+56 2 2927 3619", "Recursos Humanos")
        self._crear("direccion", "Av. Irarrázaval 2470, Ñuñoa, Santiago", "Chile")
        bloque = bloque_contexto_turno({})
        self.assertIn("Teléfono (Recursos Humanos): +56 2 2927 3619", bloque)
        self.assertIn("Dirección (Chile): Av. Irarrázaval 2470, Ñuñoa, Santiago", bloque)

    def test_sin_contactos_no_hay_bloque_ni_se_inventa_uno(self):
        from bot.flow.contexto_turno import bloque_contexto_turno

        bloque = bloque_contexto_turno({})
        self.assertNotIn("Teléfono", bloque)
        self.assertNotIn("Dirección", bloque)


class DoctorContactosTest(TestCase):
    def _niveles(self):
        from bot.management.commands.doctor import chequear_contactos_institucionales

        return [h.nivel for h in chequear_contactos_institucionales({})]

    def test_sin_contactos_es_falla(self):
        from bot.management.commands.doctor import FALLA

        self.assertEqual(self._niveles(), [FALLA])

    def test_con_telefono_y_direccion_esta_ok(self):
        from bot.management.commands.doctor import OK
        from bot.models import ContactoInstitucional

        for tipo, valor in (("telefono", "1"), ("direccion", "Lima, Perú")):
            ContactoInstitucional.objects.create(
                cliente=settings.CLIENTE_ACTIVO, tipo=tipo, valor=valor, fuente_url="https://in-touch.cl/")
        self.assertEqual(self._niveles(), [OK])
