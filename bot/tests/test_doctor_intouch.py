"""El doctor es la puerta antes de un deploy, y cero fallas es el piso.

La regla de oro de este archivo: un doctor que cría lobos deja de leerse. Un
chequeo que falla siempre -- como pedir sucursales a un bot que no las tiene --
enseña a ignorar la salida completa.
"""
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from bot.management.commands.doctor import FALLA, OK, SECCIONES
from bot.models import ModeloOperacion, SolucionInTouch


class SeccionDatosTest(TestCase):
    def test_ya_no_se_piden_sucursales(self):
        nombres = {c.__name__ for c in SECCIONES["datos"]}
        self.assertNotIn("chequear_sucursales", nombres)

    def test_se_chequea_el_catalogo_de_intouch(self):
        nombres = {c.__name__ for c in SECCIONES["datos"]}
        self.assertIn("chequear_catalogo_intouch", nombres)

    def test_ya_no_se_pide_stock_de_vehiculos(self):
        nombres = {c.__name__ for c in SECCIONES["datos"]}
        self.assertNotIn("chequear_catalogo_de_negocio", nombres)


class CatalogoTest(TestCase):
    def _correr(self):
        from bot.management.commands.doctor import chequear_catalogo_intouch

        return list(chequear_catalogo_intouch({}))

    def test_falla_si_no_hay_soluciones(self):
        # Sin catálogo, listar_soluciones no tiene qué devolver y el bot no
        # puede afirmar nada de lo que InTouch hace.
        hallazgos = self._correr()
        self.assertIn(FALLA, [h.nivel for h in hallazgos])

    def test_ok_con_catalogo_y_modelos_cargados(self):
        # canales explícito: sin él, categoria="agentes_ia" dispara el aviso
        # de "sin canales declarados" y el test deja de ser el caso feliz que
        # su nombre promete.
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="s1", nombre="Solución",
            categoria="agentes_ia", descripcion="Una solución.", canales=["whatsapp"])
        for slug in ("humano", "hibrido", "automatizado"):
            ModeloOperacion.objects.create(
                cliente=settings.CLIENTE_ACTIVO, slug=slug, nombre=slug.title(),
                descripcion="Descripción.", cuando_aplica="Cuándo aplica.")
        self.assertEqual({h.nivel for h in self._correr()}, {OK})

    def test_falla_si_faltan_los_tres_modelos_de_operacion(self):
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="s1", nombre="Solución",
            categoria="agentes_ia", descripcion="Una solución.")
        ModeloOperacion.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="humano", nombre="Humano",
            descripcion="Descripción.", cuando_aplica="Cuándo aplica.")
        self.assertIn(FALLA, [h.nivel for h in self._correr()])

    def test_avisa_si_una_solucion_no_tiene_descripcion(self):
        from bot.management.commands.doctor import AVISO

        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="s1", nombre="Solución",
            categoria="agentes_ia", descripcion="")
        for slug in ("humano", "hibrido", "automatizado"):
            ModeloOperacion.objects.create(
                cliente=settings.CLIENTE_ACTIVO, slug=slug, nombre=slug.title(),
                descripcion="Descripción.", cuando_aplica="Cuándo aplica.")
        self.assertIn(AVISO, [h.nivel for h in self._correr()])


class SchemaEfectivoTest(TestCase):
    def test_el_chequeo_esta_en_la_seccion_config(self):
        # DB_SCHEMA en el .env es decorativo: el schema efectivo lo fija el
        # DEFAULT_SCHEMA del login SQL, y reusar el login de otro bot hace que
        # este escriba en la producción del otro (pasó el 2026-09-02).
        nombres = {c.__name__ for c in SECCIONES["config"]}
        self.assertIn("chequear_schema_efectivo", nombres)

    def test_no_sale_a_la_red(self):
        # Consulta la BD del bot, no un servicio externo: tiene que correr
        # también con --sin-red, que es como se corre en desarrollo.
        from bot.management.commands.doctor import (
            CHEQUEOS_CON_RED, chequear_schema_efectivo,
        )

        self.assertNotIn(chequear_schema_efectivo, CHEQUEOS_CON_RED)

    def test_en_sqlite_se_saltea_con_ok(self):
        # La suite corre sobre sqlite (USE_SQLITE=true): no tiene schemas, así
        # que el chequeo no tiene nada que comparar y no puede fallar por eso.
        from bot.management.commands.doctor import chequear_schema_efectivo

        hallazgos = list(chequear_schema_efectivo({}))
        self.assertEqual([h.nivel for h in hallazgos], [OK])


class ToolsDelPromptTest(TestCase):
    def test_el_modulo_de_soluciones_esta_en_el_universo_de_tools(self):
        # Sin esto, chequear_tools_del_prompt no conoce las tools nuevas y
        # reportaría que el prompt nombra tools inexistentes.
        from bot.management.commands.doctor import MODULOS_CON_TOOLS

        self.assertIn("bot.business.soluciones", MODULOS_CON_TOOLS)


class PromptContraFixtureTest(TestCase):
    """Hallazgo de una task anterior: `chequear_prompt_contra_fixture` se
    degradaba en silencio cuando el fixture no estaba (pasó de verdad: una
    task anterior borró bot/fixtures/prompt_ventas.md y el chequeo dejó de
    correr, cero fallas). Reapuntado a bot/fixtures/prompt_comercial.md, la
    ausencia tiene que ser FALLA.

    `chequear_prompt_contra_fixture` acepta `opciones["fixture_comercial"]`
    para que estos tests prueben los casos "presente" y "ausente" contra una
    ruta temporal, nunca contra bot/fixtures/prompt_comercial.md -- ese
    archivo está trackeado en git y es el prompt real del especialista
    comercial en producción. Ningún test de este archivo lo lee, escribe ni
    borra."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # No existe hasta que un test lo escriba: cubre el caso "ausente"
        # sin depender del estado del repo.
        self.ruta_tmp = Path(self._tmpdir.name) / "prompt_comercial.md"

    def _correr(self, ruta=None):
        from bot.management.commands.doctor import chequear_prompt_contra_fixture

        opciones = {"fixture_comercial": ruta} if ruta is not None else {}
        return list(chequear_prompt_contra_fixture(opciones))

    def _fallas_del_fixture(self, hallazgos):
        return [h for h in hallazgos if h.nivel == FALLA
                and "prompt_comercial.md" in h.titulo]

    def test_fixture_real_del_repo_esta_presente(self):
        # El estado real del repo hoy es "presente" (lo crea la Task 8). Este
        # test corre el chequeo con la ruta real (sin override) y confirma
        # que no hay FALLA por ausencia -- sin escribir ni borrar nada.
        ruta_real = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_comercial.md"
        self.assertTrue(ruta_real.is_file(), "este test asume el estado real del repo")
        self.assertEqual(self._fallas_del_fixture(self._correr()), [])

    def test_fixture_ausente_es_falla_no_silencio(self):
        self.assertFalse(self.ruta_tmp.is_file())
        fallas = self._fallas_del_fixture(self._correr(self.ruta_tmp))
        self.assertEqual(len(fallas), 1, fallas)
        # Accionable: qué falta y qué lo crea.
        self.assertIn("prompt_comercial.md", fallas[0].titulo)
        self.assertIn("Task 8", fallas[0].detalle)

    def test_fixture_presente_no_es_falla(self):
        self.ruta_tmp.write_text("Contenido de prueba del prompt comercial.", encoding="utf-8")
        hallazgos = self._correr(self.ruta_tmp)
        self.assertEqual(self._fallas_del_fixture(hallazgos), [])

    def test_menciona_seed_intouch_y_no_seed_cavem(self):
        # Hallazgo de una task anterior: el doctor sugería `seed_cavem
        # --republicar-prompt`, un comando que no existe en este repo. Se
        # necesita el fixture presente para que la comparación se ejecute.
        from bot.models import save_prompt_version

        self.ruta_tmp.write_text("Contenido de git.", encoding="utf-8")
        save_prompt_version("comercial", "Un prompt distinto al que hay en git.")
        hallazgos = self._correr(self.ruta_tmp)
        avisos = [h for h in hallazgos if h.nivel == "aviso" and "comercial" in h.titulo]
        self.assertTrue(avisos, hallazgos)
        self.assertIn("seed_intouch", avisos[0].detalle)
        self.assertNotIn("seed_cavem", avisos[0].detalle)


class VocabularioDelPromptArmadoTest(TestCase):
    """El chequeo que cubre lo que el modelo lee DE VERDAD.

    `chequear_tools_del_prompt` escanea `effective_prompt()` y busca nombres de
    tool; eso dejaba dos huecos por los que pasó corpus de otro vertical hasta la
    review final de rama: los bloques que `build_system_prompt` pega en cada
    turno, y el CONTENIDO de los docstrings (no su nombre).

    Los dos tests que importan son el par: que detecte el defecto real y que no
    grite en falso con el vocabulario legítimo de este bot. Un chequeo que no
    falla ante lo que persigue no sirve, y uno que grita en falso enseña a
    ignorar la salida completa del doctor.
    """

    def _correr(self):
        from bot.management.commands.doctor import chequear_vocabulario_del_prompt_armado

        return list(chequear_vocabulario_del_prompt_armado({}))

    def test_el_estado_real_del_repo_esta_limpio(self):
        from bot.management.commands.doctor import FALLA

        self.assertEqual([h for h in self._correr() if h.nivel == FALLA], [])

    def test_detecta_vocabulario_de_otro_vertical_en_un_bloque_del_prompt(self):
        # El defecto real que encontró la review: `_BLOQUE_PROSA` se pega al
        # prompt en CADA turno y ofrecía "buscar en el stock, simular un
        # financiamiento, agendar" a un bot que no tiene nada de eso.
        import re

        from bot.management.commands.doctor import VOCABULARIO_DE_OTRO_VERTICAL

        defectuoso = ("Si necesitas ejecutar una acción, podés buscar en el stock, "
                      "simular un financiamiento o agendar una hora en el taller.")
        encontradas = {re.search(r"\b" + forma + r"\b", defectuoso, re.IGNORECASE).group(0).lower()
                       for forma in VOCABULARIO_DE_OTRO_VERTICAL
                       if re.search(r"\b" + forma + r"\b", defectuoso, re.IGNORECASE)}
        self.assertEqual(encontradas, {"stock", "financiamiento", "taller"})

    def test_no_grita_en_falso_con_el_vocabulario_legitimo(self):
        # "automatización" y "automotriz" son legítimas: la primera es una
        # solución del catálogo y la segunda el rubro de la EMPRESA del contacto,
        # no un vehículo que este bot venda. Si el chequeo las marcara, alguien
        # lo desactivaría y con él se perdería la cobertura entera.
        import re

        from bot.management.commands.doctor import VOCABULARIO_DE_OTRO_VERTICAL

        legitimo = ("Automatización y agentes conversacionales con IA. El subtipo "
                    "automotriz del contacto puede ser Concesionario, Automotora o "
                    "Financiera Automotriz. Operación de Contact Center híbrido.")
        for forma in VOCABULARIO_DE_OTRO_VERTICAL:
            with self.subTest(forma=forma):
                self.assertIsNone(re.search(r"\b" + forma + r"\b", legitimo, re.IGNORECASE))

    def test_el_patron_de_taller_cubre_el_singular(self):
        # `talleres?` significaba "tallere" + "s" opcional, así que no matcheaba
        # "taller" -- la forma más frecuente. Un patrón mal formado hace que el
        # chequeo pase sin mirar.
        import re

        from bot.management.commands.doctor import VOCABULARIO_DE_OTRO_VERTICAL

        patrones = " ".join(VOCABULARIO_DE_OTRO_VERTICAL)
        self.assertIn("taller(?:es)?", patrones)
        for texto in ("una hora en el taller", "los talleres de la red"):
            with self.subTest(texto=texto):
                self.assertTrue(re.search(r"\btaller(?:es)?\b", texto, re.IGNORECASE))
