"""El doctor es la puerta antes de un deploy, y cero fallas es el piso.

La regla de oro de este archivo: un doctor que cría lobos deja de leerse. Un
chequeo que falla siempre -- como pedir sucursales a un bot que no las tiene --
enseña a ignorar la salida completa.
"""
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
    correr, cero fallas). Reapuntado a bot/fixtures/prompt_comercial.md (la
    Task 8 todavía no lo crea), la ausencia tiene que ser FALLA.

    Los dos casos -- presente y ausente -- se prueban sin depender de que el
    archivo esté: el caso "ausente" usa el estado real del repo (hoy no
    existe) y el caso "presente" lo crea y lo borra dentro del propio test."""

    RUTA = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_comercial.md"

    def _correr(self):
        from bot.management.commands.doctor import chequear_prompt_contra_fixture

        return list(chequear_prompt_contra_fixture({}))

    def _fallas_del_fixture(self, hallazgos):
        return [h for h in hallazgos if h.nivel == FALLA
                and "prompt_comercial.md" in h.titulo]

    def test_fixture_ausente_es_falla_no_silencio(self):
        self.assertFalse(self.RUTA.is_file(), "este test asume el estado real del repo")
        fallas = self._fallas_del_fixture(self._correr())
        self.assertEqual(len(fallas), 1, self._correr())
        # Accionable: qué falta y qué lo crea.
        self.assertIn("prompt_comercial.md", fallas[0].titulo)
        self.assertIn("Task 8", fallas[0].detalle)

    def test_fixture_presente_no_es_falla(self):
        self.RUTA.parent.mkdir(parents=True, exist_ok=True)
        self.RUTA.write_text("Contenido de prueba del prompt comercial.", encoding="utf-8")
        try:
            hallazgos = self._correr()
        finally:
            self.RUTA.unlink()
        self.assertEqual(self._fallas_del_fixture(hallazgos), [])

    def test_menciona_seed_intouch_y_no_seed_cavem(self):
        # Hallazgo de una task anterior: el doctor sugería `seed_cavem
        # --republicar-prompt`, un comando que no existe en este repo. Se
        # necesita el fixture presente para que la comparación se ejecute.
        from bot.models import save_prompt_version

        self.RUTA.parent.mkdir(parents=True, exist_ok=True)
        self.RUTA.write_text("Contenido de git.", encoding="utf-8")
        save_prompt_version("comercial", "Un prompt distinto al que hay en git.")
        try:
            hallazgos = self._correr()
        finally:
            self.RUTA.unlink()
        avisos = [h for h in hallazgos if h.nivel == "aviso" and "comercial" in h.titulo]
        self.assertTrue(avisos, hallazgos)
        self.assertIn("seed_intouch", avisos[0].detalle)
        self.assertNotIn("seed_cavem", avisos[0].detalle)
