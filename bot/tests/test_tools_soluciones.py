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
from django.test import TestCase, TransactionTestCase

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


class ConsultarSolucionTest(BaseCatalogoTest):
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
