"""Las tools del catálogo.

Dos cosas que se prueban y que no son obvias:
  1. Son `async` con sync_to_async(thread_sensitive=True) y no funciones sync.
     Una tool sync cae a run_in_executor en un hilo genérico del pool, lo que
     rompe la transacción por-test de Django y filtra filas a otros tests.
  2. `requiere_evaluacion_tecnica` viaja en el resultado. Si no viaja, el bot
     no tiene de dónde saber que esa capacidad va sujeta a evaluación y el
     prompt §2 queda sin cumplir.
"""
import asyncio

from django.conf import settings
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from bot.business.soluciones import (
    _consultar_solucion_impl, _listar_modelos_operacion_impl, _listar_soluciones_impl,
    consultar_solucion, listar_modelos_operacion, listar_soluciones,
)
from bot.models import ModeloOperacion, SolucionInTouch


def _crear_fixtures_catalogo():
    """Las filas de catálogo que usan todos los grupos de tests de este módulo.

    Función aparte (no un método de `BaseCatalogoTest`) para que
    `LasToolsSonAsyncTest` también pueda usarla sin heredar de `TestCase`.
    """
    SolucionInTouch.objects.create(
        cliente=settings.CLIENTE_ACTIVO, slug="agentes-conversacionales",
        nombre="Agentes conversacionales con IA", categoria="agentes_ia",
        descripcion="Agentes para WhatsApp, voz, chat y correo.",
        canales=["whatsapp", "voz"], modelos_operacion=["automatizado"],
        ejemplos_uso="Consultas frecuentes en WhatsApp.", orden=1,
    )
    SolucionInTouch.objects.create(
        cliente=settings.CLIENTE_ACTIVO, slug="integraciones",
        nombre="Integraciones con CRM y ERP", categoria="integracion",
        descripcion="Integración con el CRM o el ERP de la empresa.",
        requiere_evaluacion_tecnica=True, orden=2,
    )
    SolucionInTouch.objects.create(
        cliente=settings.CLIENTE_ACTIVO, slug="inactiva", nombre="Solución retirada",
        categoria="analitica", descripcion="Ya no se ofrece.", activa=False, orden=3,
    )
    ModeloOperacion.objects.create(
        cliente=settings.CLIENTE_ACTIVO, slug="humano", nombre="Humano",
        descripcion="Agentes especializados.", cuando_aplica="Casos complejos.", orden=1,
    )


class BaseCatalogoTest(TestCase):
    def setUp(self):
        _crear_fixtures_catalogo()


class ListarSolucionesTest(BaseCatalogoTest):
    def test_lista_solo_las_activas(self):
        # Una solución retirada que el bot sigue ofreciendo es una promesa que
        # la empresa ya no puede cumplir.
        resultado = _listar_soluciones_impl()
        slugs = [s["slug"] for s in resultado["soluciones"]]
        self.assertEqual(slugs, ["agentes-conversacionales", "integraciones"])

    def test_filtra_por_categoria(self):
        resultado = _listar_soluciones_impl(categoria="integracion")
        self.assertEqual([s["slug"] for s in resultado["soluciones"]], ["integraciones"])

    def test_filtra_por_canal(self):
        resultado = _listar_soluciones_impl(canal="whatsapp")
        self.assertEqual([s["slug"] for s in resultado["soluciones"]],
                         ["agentes-conversacionales"])

    def test_una_categoria_desconocida_no_miente_con_lista_vacia(self):
        # Devolver [] haría que el bot dijera "no tenemos nada de eso", que es
        # falso: lo que pasa es que la categoría no existe.
        resultado = _listar_soluciones_impl(categoria="no-existe")
        self.assertFalse(resultado["ok"])
        self.assertIn("categorias_validas", resultado)

    def test_la_evaluacion_tecnica_viaja_en_el_resultado(self):
        resultado = _listar_soluciones_impl(categoria="integracion")
        self.assertTrue(resultado["soluciones"][0]["requiere_evaluacion_tecnica"])

    def test_un_canal_desconocido_no_miente_con_lista_vacia(self):
        # Mismo defecto que la categoría, y `canal` no se validaba. El caso
        # medido: `canal="correo"` -- la palabra que usan el prompt global, el
        # del especialista y el docstring de la tool -- devolvía
        # `{"ok": True, "soluciones": []}` porque la semilla guardaba "email".
        # El modelo lee [] como "InTouch no atiende por correo" y se lo afirma
        # al contacto.
        resultado = _listar_soluciones_impl(canal="fax")
        self.assertFalse(resultado["ok"])
        self.assertIn("canales_validos", resultado)

    def test_correo_es_un_canal_valido(self):
        # El vocabulario es UNO y está en español: "correo", no "email". Un
        # canal válido con cero soluciones sí puede devolver lista vacía -- eso
        # es verdad, y distinto de un canal que no existe.
        resultado = _listar_soluciones_impl(canal="correo")
        self.assertTrue(resultado["ok"])

    def test_el_canal_se_compara_normalizado(self):
        resultado = _listar_soluciones_impl(canal="  WhatsApp ")
        self.assertEqual([s["slug"] for s in resultado["soluciones"]],
                         ["agentes-conversacionales"])

    def test_la_categoria_se_compara_normalizada(self):
        # El modelo escribe "agentes ia" o "agentes_ia" según el turno.
        for forma in ("agentes_ia", "agentes ia", "Agentes IA"):
            with self.subTest(forma=forma):
                resultado = _listar_soluciones_impl(categoria=forma)
                self.assertTrue(resultado["ok"], resultado)
                self.assertEqual([s["slug"] for s in resultado["soluciones"]],
                                 ["agentes-conversacionales"])


class CatalogoVacioTest(TestCase):
    """Sin fixtures a propósito: un `CLIENTE_ACTIVO` mal puesto basta para que
    el manager filtre por cliente y no quede ninguna fila.

    Las tres tools devolvían `ok=True` con lista vacía, y eso es lo peor que
    puede pasar: el modelo lo lee como "InTouch no ofrece nada" y se lo afirma
    al contacto. El doctor lo cubre como FALLA en deploy, pero eso no es una
    guarda en runtime.
    """

    def test_listar_soluciones_devuelve_un_error_explicito(self):
        resultado = _listar_soluciones_impl()
        self.assertFalse(resultado["ok"])
        self.assertNotIn("soluciones", resultado)
        self.assertIn("vacío", resultado["motivo"])

    def test_consultar_solucion_no_dice_que_no_existe(self):
        # El motivo NO puede ser "no tengo X en el catálogo": eso afirma que el
        # catálogo se consultó y X no estaba.
        resultado = _consultar_solucion_impl("agentes conversacionales")
        self.assertFalse(resultado["ok"])
        self.assertIn("vacío", resultado["motivo"])

    def test_listar_modelos_operacion_devuelve_un_error_explicito(self):
        resultado = _listar_modelos_operacion_impl()
        self.assertFalse(resultado["ok"])
        self.assertNotIn("modelos", resultado)


class ElVocabularioDeCanalesEsUnoTest(SimpleTestCase):
    """El desalineamiento medido: la semilla guardaba "email" y todos los
    prompts decían "correo". Sin este test vuelve a pasar en silencio, porque
    el síntoma no es una excepción sino una lista vacía.
    """

    def test_todos_los_canales_de_la_semilla_son_validos(self):
        from bot.management.commands.seed_intouch import SOLUCIONES

        validos = set(SolucionInTouch.CANALES_VALIDOS)
        for solucion in SOLUCIONES:
            for canal in solucion.get("canales", []):
                with self.subTest(slug=solucion["slug"], canal=canal):
                    self.assertIn(canal, validos)

    def test_el_vocabulario_esta_en_espanol(self):
        # Todo lo que un LLM lee en este bot va en español: "email" era el
        # único token en inglés, y era justo el que el modelo no iba a usar.
        self.assertIn("correo", SolucionInTouch.CANALES_VALIDOS)
        self.assertNotIn("email", SolucionInTouch.CANALES_VALIDOS)


class ConsultarSolucionTest(BaseCatalogoTest):
    def test_encuentra_una_referencia_sin_tildes(self):
        # Medido: "operacion de contact center" daba ok=False y con tilde daba
        # ok=True. El que escribe la referencia es un LLM.
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="contact-center",
            nombre="Operación de Contact Center", categoria="operacion",
            descripcion="Operación completa.", orden=4,
        )
        resultado = _consultar_solucion_impl("operacion de contact center")
        self.assertTrue(resultado["ok"], resultado)
        self.assertEqual(resultado["solucion"]["slug"], "contact-center")

    def test_la_puntuacion_del_nombre_no_rompe_el_match(self):
        # `"Paneles, supervisión y dashboards".split()` deja el token
        # "paneles," y el subconjunto nunca calzaba.
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="paneles-y-dashboards",
            nombre="Paneles, supervisión y dashboards", categoria="analitica",
            descripcion="Paneles de supervisión.", orden=5,
        )
        resultado = _consultar_solucion_impl("paneles y dashboards")
        self.assertTrue(resultado["ok"], resultado)
        self.assertEqual(resultado["solucion"]["slug"], "paneles-y-dashboards")

    def test_el_slug_que_devuelve_el_fallback_vuelve_a_entrar(self):
        # EL CÍRCULO: el fallback devuelve slugs, que van sin tilde, así que el
        # modelo reintentaba con la forma que la tool rechazaba. Cada slug que
        # ofrecemos tiene que resolver.
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="operacion-a-medida",
            nombre="Diseño de una operación a medida", categoria="operacion",
            descripcion="Operación a medida.", orden=6,
        )
        fallback = _consultar_solucion_impl("blockchain")
        for slug in fallback["soluciones_disponibles"]:
            with self.subTest(slug=slug):
                self.assertTrue(_consultar_solucion_impl(slug)["ok"])

    def test_encuentra_por_slug(self):
        resultado = _consultar_solucion_impl("integraciones")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["solucion"]["nombre"], "Integraciones con CRM y ERP")

    def test_encuentra_por_nombre_parcial(self):
        # El LLM la va a nombrar como se la nombró al cliente, no por slug.
        resultado = _consultar_solucion_impl("agentes conversacionales")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["solucion"]["slug"], "agentes-conversacionales")

    def test_una_referencia_desconocida_devuelve_las_opciones(self):
        resultado = _consultar_solucion_impl("blockchain")
        self.assertFalse(resultado["ok"])
        self.assertIn("agentes-conversacionales", resultado["soluciones_disponibles"])

    def test_no_devuelve_una_solucion_inactiva(self):
        resultado = _consultar_solucion_impl("inactiva")
        self.assertFalse(resultado["ok"])


class ModelosDeOperacionTest(BaseCatalogoTest):
    def test_devuelve_los_modelos_con_cuando_aplica(self):
        resultado = _listar_modelos_operacion_impl()
        self.assertEqual(resultado["modelos"][0]["slug"], "humano")
        self.assertEqual(resultado["modelos"][0]["cuando_aplica"], "Casos complejos.")


class LasToolsSonAsyncTest(TransactionTestCase):
    """TransactionTestCase y no TestCase (ver test_supervisor_historial.py):
    las tools escriben/leen la BD vía sync_to_async desde otro thread, y con
    la transacción por-test sin commitear, sqlite en shared-cache bloquea esa
    lectura -- 'database table is locked'.
    """

    def setUp(self):
        _crear_fixtures_catalogo()

    def test_las_tres_tools_se_pueden_await(self):
        # Ver el docstring del módulo: una tool sync rompe la transacción
        # por-test y filtra filas al test siguiente.
        for tool, kwargs in (
            (listar_soluciones, {}),
            (consultar_solucion, {"referencia": "integraciones"}),
            (listar_modelos_operacion, {}),
        ):
            with self.subTest(tool=tool.name):
                resultado = asyncio.run(tool.ainvoke(kwargs))
                self.assertIsInstance(resultado, dict)

    def test_los_nombres_de_las_tools_son_los_del_prompt(self):
        self.assertEqual(listar_soluciones.name, "listar_soluciones")
        self.assertEqual(consultar_solucion.name, "consultar_solucion")
        self.assertEqual(listar_modelos_operacion.name, "listar_modelos_operacion")
