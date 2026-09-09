"""Los precios llegan al LLM ya formateados, listos para copiar.

Dos fallas reales de una conversación con el vendedor (2026-09-02, ver
docs/PENDIENTES.md #20):

1. El bot escribió "valor referencial desde **$XX**" al cotizar un servicio de
   taller. `listar_catalogo` devolvía id/nombre/duración pero **no el precio**,
   mientras el prompt de agendamiento le decía al LLM que cotizara con esa
   tool. El dato estaba en la BD y nunca llegaba a la tool.
2. El bot cotizó la Subaru XV en $18.990.000 cuando su precio real es
   $19.890.000 — y $18.990.000 es el precio del Kia Sportage, que había
   mostrado tres mensajes antes. La tool SÍ entregaba el precio correcto: el
   modelo lo reescribió mal.

Por eso los montos viajan también como string ya formateado: un LLM copia un
string de forma mucho más confiable de la que reformatea un número.
"""
import json

from django.conf import settings
from django.test import TestCase

from bot.business.catalogo import _clp, _listar_catalogo_impl
from bot.models import Servicio, Sucursal, VehiculoUsado


class ClpTest(TestCase):
    def test_usa_el_separador_de_miles_chileno(self):
        # f"{n:,}" da "19,890,000" — en Chile el separador es el punto.
        self.assertEqual(_clp(19890000), "$19.890.000")
        self.assertEqual(_clp(89900), "$89.900")

    def test_monto_vacio_no_revienta(self):
        self.assertEqual(_clp(None), "")


class CatalogoConPrecioTest(TestCase):
    def setUp(self):
        # cliente=CLIENTE_ACTIVO y no "cavem" fijo: manager filtrado + la suite
        # corre como renault (ver CLAUDE.md).
        Servicio.todos_los_clientes.create(
            nombre="Cambio de aceite y filtro", duracion_min=60, precio=89900,
            cliente=settings.CLIENTE_ACTIVO)
        Sucursal.todos_los_clientes.create(
            nombre="Cavem La Reina", direccion="Av. Bilbao 1234",
            cliente=settings.CLIENTE_ACTIVO)

    def test_los_servicios_llegan_con_precio(self):
        servicio = _listar_catalogo_impl()["servicios"][0]
        self.assertEqual(servicio["precio"], 89900)
        self.assertEqual(servicio["precio_formateado"], "$89.900")

    def test_el_precio_no_es_decimal_para_que_serialice(self):
        # El resultado viaja como ToolMessage: un Decimal rompe json.dumps.
        self.assertIsInstance(_listar_catalogo_impl()["servicios"][0]["precio"], int)
        self.assertTrue(json.dumps(_listar_catalogo_impl()))


class VehiculoConPrecioFormateadoTest(TestCase):
    def setUp(self):
        VehiculoUsado.objects.create(
            codigo="US022", marca="Subaru", modelo="XV", version="2.0i AWD CVT",
            anio=2021, km=53700, precio_lista=20490000, precio_oferta=19890000,
            tipo_vehiculo="SUV", disponibilidad="Disponible")

    def test_la_ficha_trae_el_precio_listo_para_copiar(self):
        from bot.business.usados import _consultar_ficha_vehiculo_impl
        r = _consultar_ficha_vehiculo_impl("Subaru XV")
        v = r.get("vehiculo") or r
        self.assertEqual(v["precio_formateado"], "$19.890.000")
        self.assertEqual(v["precio_lista_formateado"], "$20.490.000")

    def test_el_formateado_coincide_con_el_numerico(self):
        # Si alguna vez divergen, el LLM copiaría un precio que no es el real:
        # exactamente el fallo que esto viene a prevenir.
        from bot.business.usados import _consultar_ficha_vehiculo_impl
        v = _consultar_ficha_vehiculo_impl("Subaru XV")
        v = v.get("vehiculo") or v
        self.assertEqual(v["precio_formateado"], _clp(v["precio"]))


class ServicioSinPrecioTest(TestCase):
    """Un Servicio sin precio no debe romper la tool.

    El campo es opcional y hay fixtures que lo dejan vacío: `int(None)` tiraba
    TypeError y se llevaba abajo `listar_catalogo` entero.
    """

    def setUp(self):
        Servicio.todos_los_clientes.create(
            nombre="Servicio sin precio", duracion_min=30, cliente=settings.CLIENTE_ACTIVO)

    def test_no_revienta_y_no_inventa_un_monto(self):
        servicio = _listar_catalogo_impl()["servicios"][0]
        self.assertIsNone(servicio["precio"])
        # Cadena vacía, no "$0": un $0 le daría al LLM un precio que copiar.
        self.assertEqual(servicio["precio_formateado"], "")
