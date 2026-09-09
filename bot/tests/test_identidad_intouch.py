"""La identidad del bot tiene que decir lo mismo en los tres lugares donde vive.

El spec §8 la fija en uno solo: prompt global, WELCOME_IDENTIDAD y el `nombre`
del dios.json. Tres copias que se contradicen es como se le presenta al cliente
un bot con dos nombres.
"""
import json
import re
from pathlib import Path

from django.test import SimpleTestCase

RAIZ = Path(__file__).resolve().parent.parent.parent


class ClienteIntouchTest(SimpleTestCase):
    def test_intouch_es_un_cliente_valido(self):
        from bot.models import CLIENTE_CHOICES

        self.assertIn("intouch", dict(CLIENTE_CHOICES))

    def test_las_marcas_heredadas_siguen_siendo_validas(self):
        # No es nostalgia: la suite heredada estampa renault/astara en casi
        # todos sus fixtures y sacarlas deja cientos de tests en rojo, o sea
        # sin la red que prueba las defensas de biblia §IV.1.
        from bot.models import CLIENTE_CHOICES

        validos = dict(CLIENTE_CHOICES)
        self.assertIn("renault", validos)
        self.assertIn("astara", validos)


class SinRastrosDeCavemTest(SimpleTestCase):
    def test_no_quedan_comandos_de_cavem(self):
        comandos = {p.name for p in (RAIZ / "bot/management/commands").glob("*.py")}
        self.assertNotIn("seed_cavem.py", comandos)
        self.assertNotIn("importar_stock_cavem.py", comandos)

    def test_no_queda_conocimiento_de_otro_cliente(self):
        # Indexar el RAG con los .md de Cavem le haría contestar a un contacto
        # de InTouch sobre financiamiento de autos usados.
        sobrantes = list((RAIZ / "bot/fixtures/rag").glob("*.md"))
        for md in sobrantes:
            texto = md.read_text(encoding="utf-8").lower()
            self.assertNotIn("cavem", texto, md.name)


class PuertoYScopeTest(SimpleTestCase):
    def test_el_compose_publica_el_8040(self):
        compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("8040:8000", compose)
        self.assertNotIn("8030:8000", compose)

    def test_el_scope_de_federation_coincide_con_el_ejemplo_de_dios(self):
        # remote_scope del dios.json y `name` de vite.config.ts tienen que ser
        # idénticos o el shell no encuentra el remote.
        vite = (RAIZ / "frontend/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("name: 'wsp_intouch'", vite)
        ejemplo = json.loads((RAIZ / "dios.json.example").read_text(encoding="utf-8"))
        self.assertEqual(ejemplo["remote_scope"], "wsp_intouch")
        self.assertEqual(ejemplo["remote_entry_url"], "/mf/wsp_intouch/remoteEntry.js")
        self.assertEqual(ejemplo["slug"], "intouch")
        self.assertEqual(ejemplo["route_prefix"], "/wsp/intouch/")
        self.assertEqual(ejemplo["url_publica"], "/wsp/intouch/")
        self.assertEqual(ejemplo["schemas"], ["intouch"])
