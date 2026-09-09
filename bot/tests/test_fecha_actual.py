"""El LLM tiene que saber qué día es hoy.

Bug real del 2026-09-03 (docs/PENDIENTES.md #22): un contacto pidió hora para
"este viernes a las 16:00", el bot respondió *"Quedó agendado"* y **no llamó a
ninguna tool de agenda**. No podía: `consultar_disponibilidad`/`agendar_hora`
piden `fecha`, y la fecha de hoy no estaba en ningún prompt. Lo que quedó en el
estado fue `2025-06-06T16:00` — año y mes inventados. El cliente se iba a
presentar a una hora que no existía en el sistema.
"""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from bot.flow.agents._common import bloque_fecha_actual


class BloqueFechaActualTest(TestCase):
    def test_incluye_la_fecha_de_hoy_marcada(self):
        hoy = timezone.localtime().date().isoformat()
        bloque = bloque_fecha_actual()
        self.assertIn(hoy, bloque)
        self.assertIn("(HOY)", bloque)

    def test_resuelve_los_proximos_siete_dias(self):
        # La tabla se entrega ya calculada para que el modelo no haga
        # aritmética de calendario: ahí es donde se equivoca.
        bloque = bloque_fecha_actual()
        hoy = timezone.localtime().date()
        for offset in range(8):
            with self.subTest(offset=offset):
                self.assertIn((hoy + timedelta(days=offset)).isoformat(), bloque)

    def test_nombra_el_dia_de_la_semana_en_espanol(self):
        dias = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
        bloque = bloque_fecha_actual().lower()
        self.assertTrue(any(d in bloque for d in dias))

    def test_incluye_la_hora(self):
        # Un contacto real pidió "que me contacte hoy después de las 23:00":
        # sin la hora, el bot no sabe si eso ya pasó.
        self.assertIn("hora de Chile", bloque_fecha_actual())

    def test_prohibe_inventar_fechas_fuera_de_la_tabla(self):
        self.assertIn("nunca la inventes", bloque_fecha_actual())

    def test_usa_la_zona_horaria_de_chile(self):
        # timezone.localtime() respeta TIME_ZONE=America/Santiago; con utcnow()
        # el bot se equivocaría de día cerca de medianoche.
        # El `return_value` se calcula ANTES de entrar al patch: si se calcula
        # adentro (como estaba), `timezone.localtime()` ya esta interceptado y
        # le devuelve un MagicMock a la funcion, que entonces nunca ejecuta su
        # cuerpo de verdad -- sobrevivia solo porque MagicMock soporta
        # __index__ y __format__ sin spec, y se rompio al formatear el minuto
        # con :02d. Con un datetime real el test prueba lo que dice probar.
        real_ahora = timezone.localtime()
        with patch("django.utils.timezone.localtime", return_value=real_ahora) as mock_local:
            bloque = bloque_fecha_actual()
        self.assertTrue(mock_local.called)
        self.assertIn(real_ahora.date().isoformat(), bloque)


class TodosLosEspecialistasVenLaFechaTest(TestCase):
    def test_cada_especialista_registrado_la_incluye(self):
        # Si alguien agrega un especialista nuevo y olvida el bloque, su flujo
        # con fechas se rompe igual que el de agendamiento.
        from bot.flow.agents import build_agent_registry

        for slug, agente in build_agent_registry().items():
            with self.subTest(agente=slug):
                prompt = agente.build_system_prompt(
                    {"flow_data": {}, "name": "Felipe"}, "PROMPT")
                self.assertIn("## FECHA", prompt)


class HoraRedondeadaParaLaCacheTest(TestCase):
    """La hora va redondeada a 5 minutos porque este bloque es el prefijo de
    cache del prompt (ver bloque_fecha_actual). Al minuto exacto, cada minuto
    de reloj invalidaba las ~2.500 tokens de instrucciones y las 11 tools.
    Medido: la primera llamada de cada minuto tenia 41,4% de cache hit contra
    76,4% las siguientes del mismo minuto.
    """

    def _bloque_con_minuto(self, minuto):
        from django.utils import timezone as tz
        ahora = tz.localtime().replace(hour=14, minute=minuto, second=0, microsecond=0)
        with patch("django.utils.timezone.localtime", return_value=ahora):
            return bloque_fecha_actual()

    def test_el_minuto_se_redondea_hacia_abajo_al_multiplo_de_cinco(self):
        self.assertIn("14:10", self._bloque_con_minuto(13))
        self.assertIn("14:35", self._bloque_con_minuto(39))
        self.assertIn("14:00", self._bloque_con_minuto(4))

    def test_el_minuto_exacto_queda_igual_si_ya_es_multiplo_de_cinco(self):
        self.assertIn("14:20", self._bloque_con_minuto(20))

    def test_el_minuto_siempre_va_con_dos_digitos(self):
        # "14:5" en vez de "14:05" le daria al LLM una hora ambigua.
        self.assertIn("14:05", self._bloque_con_minuto(7))
        self.assertNotIn("14:5 ", self._bloque_con_minuto(7))

    def test_cinco_minutos_seguidos_dan_EXACTAMENTE_el_mismo_bloque(self):
        # Este es el invariante que produce la ganancia de cache: si dos
        # llamadas dentro de la misma ventana difieren en un solo caracter, el
        # prefijo se invalida igual y el redondeo no sirvio para nada.
        bloques = {self._bloque_con_minuto(m) for m in (30, 31, 32, 33, 34)}
        self.assertEqual(len(bloques), 1)

    def test_al_cruzar_la_ventana_el_bloque_si_cambia(self):
        self.assertNotEqual(self._bloque_con_minuto(34), self._bloque_con_minuto(35))


class BloqueSucursalUnicaTest(TestCase):
    """Con una sola sucursal, el bot no debe preguntar cuál le queda más cerca.

    Caso real (conversación de Quintin, docs/PENDIENTES.md #25c): el bot le
    preguntó *"¿en qué comuna estás? Así te recomiendo la sucursal que te quede
    más cerca"* teniendo Cavem **una sola**, haciéndole perder un turno por una
    elección que no existe.

    La regla en el prompt global no alcanzó: medido contra el LLM real, seguía
    preguntando 1 de cada 2 veces. Con la sucursal ya en el contexto no hay
    nada que preguntar ni que recordar — 0 de 4. Mismo patrón que
    `bloque_fecha_actual`.
    """

    def setUp(self):
        from bot.flow.agents._common import refrescar_sucursal_unica
        from bot.models import Sucursal
        Sucursal.todos_los_clientes.create(
            nombre="Cavem La Reina", direccion="Av. Bilbao 1234, La Reina",
            horario_texto="Lunes a viernes 08:30-18:00",
            cliente=settings.CLIENTE_ACTIVO)
        refrescar_sucursal_unica()

    def test_nombra_la_sucursal_y_prohibe_preguntar(self):
        from bot.flow.agents._common import bloque_sucursal_unica
        bloque = bloque_sucursal_unica()
        self.assertIn("Cavem La Reina", bloque)
        self.assertIn("No le preguntes", bloque)

    def test_no_incluye_la_direccion_ni_el_horario(self):
        """A propósito: si el bloque trae la dirección, el modelo responde sin
        llamar a `buscar_sucursales_cercanas`, y el pin de ubicación de
        WhatsApp se resuelve de los RESULTADOS de esa tool.

        Medido con la dirección adentro: la dirección salía bien 6/6 pero el
        pin solo 4/6, y en las 2 fallas no se llamó ninguna tool. Sacándola:
        pin 6/6, y el bot sigue sin preguntar la comuna (0/4). Es una
        interacción entre dos arreglos del mismo día, ver
        docs/PENDIENTES.md #25c."""
        from bot.flow.agents._common import bloque_sucursal_unica
        bloque = bloque_sucursal_unica()
        self.assertNotIn("Av. Bilbao 1234", bloque)
        self.assertNotIn("08:30-18:00", bloque)
        # Y en cambio manda usar la tool, que es la que alimenta el pin.
        self.assertIn("buscar_sucursales_cercanas", bloque)

    def test_con_dos_sucursales_no_inyecta_nada(self):
        # Con 2+ preguntar la comuna SÍ aporta, y el ranking por cercanía de
        # buscar_sucursales_cercanas hace su trabajo.
        from bot.flow.agents._common import bloque_sucursal_unica, refrescar_sucursal_unica
        from bot.models import Sucursal
        Sucursal.todos_los_clientes.create(
            nombre="Cavem Maipú", direccion="Otra 456", cliente=settings.CLIENTE_ACTIVO)
        refrescar_sucursal_unica()
        self.assertEqual(bloque_sucursal_unica(), "")

    def test_sin_sucursales_no_inyecta_nada(self):
        from bot.flow.agents._common import bloque_sucursal_unica, refrescar_sucursal_unica
        from bot.models import Sucursal
        Sucursal.todos_los_clientes.all().delete()
        refrescar_sucursal_unica()
        self.assertEqual(bloque_sucursal_unica(), "")

    def test_el_registry_lo_refresca_en_contexto_sincrono(self):
        # bloque_sucursal_unica() NO puede consultar la BD: build_system_prompt
        # se invoca desde un nodo async del grafo y una query ahí levanta
        # SynchronousOnlyOperation (pasó en la primera versión de esto).
        # build_agent_registry corre bajo sync_to_async, así que es el punto
        # correcto para refrescarlo.
        import inspect
        from bot.flow import agents
        self.assertIn("refrescar_sucursal_unica()", inspect.getsource(agents.build_agent_registry))

    def test_todos_los_especialistas_lo_incluyen(self):
        from bot.flow.agents import build_agent_registry
        for slug, agente in build_agent_registry().items():
            with self.subTest(agente=slug):
                prompt = agente.build_system_prompt({"flow_data": {}, "name": "Quintin"}, "PROMPT")
                self.assertIn("## SUCURSAL", prompt)
