from django.test import TestCase

from bot.flow.graph import RESPUESTA_GENERICA_JSON_INVALIDO
from bot.tests.test_usados_cavem import crear_vehiculo
from bot.simulator.code_evaluators import (
    codigo_reserva_no_inventado,
    correr_evaluadores_de_codigo,
    imagen_solo_con_intent_correcto,
    json_valido_cada_turno,
    precio_coincide_con_catalogo,
    sin_imagen_duplicada,
)


class PrecioCoincideConCatalogoTest(TestCase):
    """El evaluador compara el precio que el bot cotizo contra el stock REAL.

    Los fixtures se migraron de VehiculoCatalogo a VehiculoUsado junto con
    _resolver_precio_catalogo (Cavem cotiza sobre la planilla comercial, no
    sobre el catalogo del scraper). No es cosmetico: con filas de
    VehiculoCatalogo el resolver devuelve None, el evaluador se saltea la
    verificacion y estos tests pasaban sin comprobar nada -- un verde falso
    que dejaba el guardia de precios apagado en silencio.
    """

    def test_pasa_si_el_precio_coincide_con_el_stock(self):
        crear_vehiculo(codigo="US001", marca="Hyundai", modelo="Tucson",
                       version="2.0 AT Value", precio_lista=20490000, precio_oferta=19990000)
        turnos = [{"acciones": [{
            "nombre": "simular_financiamiento",
            "params": {"modelo": "Tucson", "version": "2.0 AT Value"},
            "resultado": {"ok": True, "precio": 19990000},
        }]}]
        self.assertIsNone(precio_coincide_con_catalogo(turnos))

    def test_falla_si_el_precio_no_coincide_con_el_stock(self):
        crear_vehiculo(codigo="US001", marca="Hyundai", modelo="Tucson", precio_oferta=19990000)
        crear_vehiculo(codigo="US004", marca="Kia", modelo="Sportage", precio_oferta=18990000)
        turnos = [{"acciones": [{
            "nombre": "simular_financiamiento",
            "params": {"modelo": "Tucson", "version": ""},
            "resultado": {"ok": True, "precio": 18990000},
        }]}]
        self.assertIsNotNone(precio_coincide_con_catalogo(turnos))

    def test_pasa_si_no_hay_dato_de_stock_para_ese_modelo(self):
        turnos = [{"acciones": [{
            "nombre": "simular_financiamiento",
            "params": {"modelo": "Modelo Inexistente"},
            "resultado": {"ok": True, "precio": 10000000},
        }]}]
        self.assertIsNone(precio_coincide_con_catalogo(turnos))

    def test_ignora_turnos_sin_resultado_ok(self):
        turnos = [{"acciones": [{
            "nombre": "simular_financiamiento",
            "params": {"modelo": "Tucson"},
            "resultado": {"ok": False, "motivo": "pie insuficiente"},
        }]}]
        self.assertIsNone(precio_coincide_con_catalogo(turnos))

    def test_revisa_todas_las_acciones_de_un_mismo_turno(self):
        # Un turno puede encadenar mas de una accion -- el evaluador debe
        # revisar TODAS, no solo la primera.
        crear_vehiculo(codigo="US001", marca="Hyundai", modelo="Tucson", precio_oferta=19990000)
        turnos = [{"acciones": [
            {"nombre": "consultar_ficha_vehiculo", "params": {"referencia": "Tucson"}, "resultado": {"ok": True}},
            {"nombre": "simular_financiamiento", "params": {"modelo": "Tucson"}, "resultado": {"ok": True, "precio": 999}},
        ]}]
        self.assertIsNotNone(precio_coincide_con_catalogo(turnos))


class CodigoReservaNoInventadoTest(TestCase):
    def test_pasa_si_el_codigo_viene_de_un_resultado_previo_ok(self):
        turnos = [
            {"acciones": [{"nombre": "agendar_hora", "params": {}, "resultado": {"ok": True, "codigo": "ABC123"}}]},
            {"acciones": [{"nombre": "buscar_reserva", "params": {"codigo": "ABC123"}, "resultado": {"ok": True}}]},
        ]
        self.assertIsNone(codigo_reserva_no_inventado(turnos))

    def test_falla_si_el_codigo_no_viene_de_ningun_resultado_previo(self):
        turnos = [
            {"acciones": [{"nombre": "buscar_reserva", "params": {"codigo": "INVENTADO"}, "resultado": {"ok": False}}]},
        ]
        self.assertIsNotNone(codigo_reserva_no_inventado(turnos))

    def test_no_crashea_si_otra_accion_del_mismo_turno_trae_un_resultado_no_dict_envuelto(self):
        # Hallazgo 1 de la revision final: consultar_disponibilidad devuelve
        # una lista, que _acciones_de_tool_messages envuelve como
        # {"resultado": [...]}. Antes del fix, cualquier evaluador que
        # asumiera dict en TODAS las acciones del turno (no solo la que le
        # importa a este evaluador) podia crashear si la forma envuelta no
        # se manejaba bien -- este test cubre que codigo_reserva_no_inventado
        # sigue funcionando (y da el veredicto correcto para buscar_reserva)
        # aunque el turno tambien traiga esa accion no relacionada.
        turnos = [
            {"acciones": [
                {
                    "nombre": "consultar_disponibilidad",
                    "params": {"servicio_id": 1, "sucursal_id": 1, "fecha": "2026-08-20"},
                    "resultado": {"resultado": [{"hora": "10:00"}, {"hora": "11:00"}]},
                },
                {"nombre": "buscar_reserva", "params": {"codigo": "INVENTADO"}, "resultado": {"ok": False}},
            ]},
        ]
        self.assertIsNotNone(codigo_reserva_no_inventado(turnos))

    def test_revisa_todas_las_acciones_de_un_mismo_turno(self):
        # Un turno puede encadenar mas de una accion (ver Task 2) -- el
        # evaluador debe revisar TODAS las acciones del turno, no solo la
        # primera, tanto para construir el set de codigos conocidos como
        # para detectar el uso de uno inventado. Aqui el codigo se genera
        # (agendar_hora) y se consume (buscar_reserva) dentro del MISMO
        # turno, precedidos por una accion no relacionada -- si el
        # evaluador solo mirara la primera accion de la lista, nunca
        # llegaria a ver ni el codigo valido ni su uso, y este test
        # fallaria por una razon equivocada (falso positivo).
        turnos = [{"acciones": [
            {
                "nombre": "consultar_disponibilidad",
                "params": {"servicio_id": 1, "sucursal_id": 1, "fecha": "2026-08-20"},
                "resultado": {"resultado": [{"hora": "10:00"}]},
            },
            {"nombre": "agendar_hora", "params": {}, "resultado": {"ok": True, "codigo": "ABC123"}},
            {"nombre": "buscar_reserva", "params": {"codigo": "ABC123"}, "resultado": {"ok": True}},
        ]}]
        self.assertIsNone(codigo_reserva_no_inventado(turnos))

    def test_detecta_codigo_inventado_aunque_no_sea_la_primera_accion_del_turno(self):
        # Complemento del test anterior: si la accion violatoria (con un
        # codigo que NO viene de ningun resultado previo con ok=true) no es
        # la primera de la lista del turno, el evaluador igual debe
        # atraparla.
        turnos = [{"acciones": [
            {
                "nombre": "consultar_disponibilidad",
                "params": {"servicio_id": 1, "sucursal_id": 1, "fecha": "2026-08-20"},
                "resultado": {"resultado": [{"hora": "10:00"}]},
            },
            {"nombre": "reagendar_hora", "params": {"codigo": "INVENTADO"}, "resultado": {"ok": False}},
        ]}]
        self.assertIsNotNone(codigo_reserva_no_inventado(turnos))


class ImagenSoloConIntentCorrectoTest(TestCase):
    def test_pasa_si_la_imagen_se_envio_con_intent_permitido(self):
        turnos = [{"imagen_enviada": True, "modelo_imagen": "arkana", "intent": "cotizar"}]
        self.assertIsNone(imagen_solo_con_intent_correcto(turnos))

    def test_falla_si_la_imagen_se_envio_con_intent_no_permitido(self):
        turnos = [{"imagen_enviada": True, "modelo_imagen": "arkana", "intent": "financiar"}]
        self.assertIsNotNone(imagen_solo_con_intent_correcto(turnos))

    def test_pasa_si_no_se_envio_ninguna_imagen(self):
        turnos = [{"imagen_enviada": False, "modelo_imagen": None, "intent": "financiar"}]
        self.assertIsNone(imagen_solo_con_intent_correcto(turnos))


class SinImagenDuplicadaTest(TestCase):
    def test_pasa_si_cada_imagen_se_envia_una_sola_vez(self):
        turnos = [
            {"imagen_enviada": True, "modelo_imagen": "arkana"},
            {"imagen_enviada": False, "modelo_imagen": None},
        ]
        self.assertIsNone(sin_imagen_duplicada(turnos))

    def test_falla_si_la_misma_imagen_se_envia_dos_veces(self):
        turnos = [
            {"imagen_enviada": True, "modelo_imagen": "arkana"},
            {"imagen_enviada": True, "modelo_imagen": "arkana"},
        ]
        self.assertIsNotNone(sin_imagen_duplicada(turnos))


class JsonValidoCadaTurnoTest(TestCase):
    def test_pasa_si_ningun_turno_cae_en_la_respuesta_generica(self):
        self.assertIsNone(json_valido_cada_turno([{"bot_responde": "Hola, en que le ayudo?"}]))

    def test_falla_si_algun_turno_cae_en_la_respuesta_generica(self):
        turnos = [{"bot_responde": RESPUESTA_GENERICA_JSON_INVALIDO}]
        self.assertIsNotNone(json_valido_cada_turno(turnos))


class CorrerEvaluadoresDeCodigoTest(TestCase):
    def test_devuelve_lista_vacia_si_todo_pasa(self):
        turnos = [{
            "bot_responde": "hola", "imagen_enviada": False, "modelo_imagen": None,
            "intent": None, "acciones": [],
        }]
        self.assertEqual(correr_evaluadores_de_codigo(turnos), [])

    def test_devuelve_un_mensaje_por_cada_evaluador_que_falla(self):
        turnos = [{
            "bot_responde": RESPUESTA_GENERICA_JSON_INVALIDO,
            "imagen_enviada": True, "modelo_imagen": "arkana", "intent": "financiar",
            "acciones": [],
        }]
        fallos = correr_evaluadores_de_codigo(turnos)
        self.assertEqual(len(fallos), 2)
