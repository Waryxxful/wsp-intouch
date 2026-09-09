"""Cobertura del stock de usados de Cavem y de lo que se construyo sobre el.

Reemplaza a los tests de wsp_demo que apuntaban a VehiculoCatalogo (ver los
@skip en test_agents.py): en este bot el precio sale de la planilla comercial,
no del scraper.

Dominio automotriz desregistrado, no borrado (ver CLAUDE.md): se conserva
porque prueba defensas reales de la biblia §IV.1 sobre los modelos
VehiculoUsado/LeadComercial/VehiculoPartePago/Servicio/Sucursal/Reserva, que
siguen existiendo aunque este bot no los rutee.

Se sacó `ImportadorTest` (probaba `fila_a_campos` de
`bot.management.commands.importar_stock_cavem`): ese comando no existe en
este repo, se borró junto con el resto de lo específico de Cavem en el
commit que copió el árbol (9429197, "Borrado lo de Cavem: seeds, stock,
fixtures del RAG..."). InTouch no tiene una planilla de stock que importar,
así que no hay nada que reapuntar. Fue ese import roto el que tumbaba todo
el módulo (y, en cadena, `test_simulator_code_evaluators.py`, que importa
`crear_vehiculo` desde acá) -- el resto de la cobertura de este archivo
nunca estuvo rota.
"""
import asyncio
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from bot.business.prospeccion import _registrar_datos_lead_impl, _registrar_parte_pago_impl
from bot.business.usados import (
    _buscar_vehiculos_impl, _consultar_ficha_vehiculo_impl, _resolver_precio_usado,
)
from bot.business.ventas import _simular_financiamiento_impl, _simular_por_cuota_impl
from bot.models import (
    Conversation, LeadComercial, Message, VehiculoPartePago, VehiculoUsado,
)


def crear_vehiculo(**kwargs):
    datos = {
        "codigo": "US001", "marca": "Hyundai", "modelo": "Tucson", "version": "2.0 AT Value",
        "anio": 2023, "km": 31800, "precio_lista": 20490000, "precio_oferta": 19990000,
        "tipo_vehiculo": "SUV", "combustible": "Gasolina", "transmision": "Automática 6AT",
        "es_automatico": True, "traccion": "4x2", "disponibilidad": "Disponible",
    }
    datos.update(kwargs)
    return VehiculoUsado.objects.create(**datos)


class PrecioVigenteTest(TestCase):
    def test_precio_vigente_prefiere_la_oferta(self):
        self.assertEqual(crear_vehiculo().precio_vigente, 19990000)

    def test_sin_oferta_usa_el_precio_de_lista(self):
        self.assertEqual(crear_vehiculo(precio_oferta=None).precio_vigente, 20490000)


class BuscarVehiculosTest(TestCase):
    def setUp(self):
        crear_vehiculo()
        crear_vehiculo(codigo="US004", marca="Kia", modelo="Sportage", anio=2022,
                       precio_lista=19490000, precio_oferta=18990000)
        crear_vehiculo(codigo="US007", marca="Ford", modelo="Ranger", tipo_vehiculo="Pick-up",
                       combustible="Diésel", traccion="4x4", precio_lista=26990000,
                       precio_oferta=25990000)
        crear_vehiculo(codigo="US011", marca="Suzuki", modelo="Swift", tipo_vehiculo="Hatchback",
                       precio_lista=12990000, precio_oferta=12490000)

    def test_filtra_por_presupuesto_usando_el_precio_vigente(self):
        codigos = {v["codigo"] for v in _buscar_vehiculos_impl(precio_max=20000000)["vehiculos"]}
        self.assertEqual(codigos, {"US001", "US004", "US011"})

    def test_con_presupuesto_ordena_del_mas_caro_al_mas_barato(self):
        # El mejor auto que entra en el presupuesto va primero: es la respuesta
        # que espera el docx S6 (Tucson antes que Sportage antes que las mas
        # baratas). Ordenar ascendente rompe el guion de la demo.
        codigos = [v["codigo"] for v in _buscar_vehiculos_impl(precio_max=20000000)["vehiculos"]]
        self.assertEqual(codigos, ["US001", "US004", "US011"])

    def test_ordenar_por_precio_asc_pone_primero_lo_mas_economico(self):
        codigos = [v["codigo"] for v in
                   _buscar_vehiculos_impl(precio_max=20000000, ordenar_por="precio_asc")["vehiculos"]]
        self.assertEqual(codigos[0], "US011")

    def test_camioneta_es_sinonimo_de_pick_up(self):
        # docx S24 pregunta textual: "Busco una camioneta para trabajo".
        resultado = _buscar_vehiculos_impl(tipo_vehiculo="camioneta")
        self.assertEqual([v["codigo"] for v in resultado["vehiculos"]], ["US007"])

    def test_tipo_de_vehiculo_matchea_sin_acentos(self):
        crear_vehiculo(codigo="US009", modelo="Clase C", tipo_vehiculo="Sedán")
        self.assertEqual(
            [v["codigo"] for v in _buscar_vehiculos_impl(tipo_vehiculo="sedan")["vehiculos"]],
            ["US009"],
        )

    def test_excluye_vehiculos_no_disponibles(self):
        crear_vehiculo(codigo="US099", disponibilidad="Vendido", precio_oferta=1000000)
        codigos = {v["codigo"] for v in _buscar_vehiculos_impl()["vehiculos"]}
        self.assertNotIn("US099", codigos)

    def test_sin_resultados_ofrece_la_alternativa_mas_barata(self):
        # Nunca dejar al LLM en un callejon sin salida: sin esto el bot corta
        # con "no tengo nada" y se pierde el lead.
        resultado = _buscar_vehiculos_impl(precio_max=5000000, tipo_vehiculo="SUV")
        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["alternativa_mas_cercana"]["codigo"], "US004")

    def test_informa_el_total_cuando_hay_mas_de_los_que_muestra(self):
        for i in range(10):
            crear_vehiculo(codigo=f"UX{i:03d}", precio_oferta=15000000 + i)
        resultado = _buscar_vehiculos_impl(precio_max=20000000)
        self.assertGreater(resultado["total_encontrados"], len(resultado["vehiculos"]))
        self.assertIn("nota", resultado)


class ResolverPrecioTest(TestCase):
    def setUp(self):
        crear_vehiculo()

    def test_resuelve_por_modelo(self):
        self.assertEqual(_resolver_precio_usado("Tucson"), 19990000)

    def test_resuelve_incluyendo_la_marca(self):
        self.assertEqual(_resolver_precio_usado("Hyundai Tucson"), 19990000)

    def test_es_insensible_a_mayusculas_y_acentos(self):
        crear_vehiculo(codigo="US009", marca="Mercedes-Benz", modelo="Clase C",
                       precio_oferta=27990000, precio_lista=28990000)
        self.assertEqual(_resolver_precio_usado("clase c"), 27990000)

    def test_sin_match_devuelve_none_en_vez_de_inventar(self):
        self.assertIsNone(_resolver_precio_usado("Ferrari"))

    def test_varias_unidades_del_mismo_modelo_toma_la_mas_barata(self):
        crear_vehiculo(codigo="US050", precio_oferta=17990000, precio_lista=18490000)
        self.assertEqual(_resolver_precio_usado("Tucson"), 17990000)


class SimulacionAncladaTest(TestCase):
    def setUp(self):
        crear_vehiculo()

    def test_el_precio_del_stock_pisa_al_que_manda_el_llm(self):
        # Guardrail del docx S15: el bot no cotiza sobre un precio inventado.
        resultado = _simular_financiamiento_impl(precio=1, pie=5000000, plazo_meses=48, modelo="Tucson")
        self.assertEqual(resultado["precio"], 19990000)
        self.assertEqual(resultado["monto_total_financiado"], 14990000)

    def test_plazo_menor_a_12_meses_se_rechaza(self):
        # docx S7 fija el financiamiento entre 12 y 60 meses.
        self.assertFalse(_simular_financiamiento_impl(
            precio=19990000, pie=5000000, plazo_meses=6)["ok"])

    def test_plazo_de_12_y_de_60_son_validos(self):
        for plazo in (12, 60):
            self.assertTrue(_simular_financiamiento_impl(
                precio=19990000, pie=5000000, plazo_meses=plazo)["ok"])


class SimularPorCuotaTest(TestCase):
    def setUp(self):
        crear_vehiculo()
        crear_vehiculo(codigo="US011", modelo="Swift", precio_lista=12990000, precio_oferta=12490000)

    def test_de_la_cuota_despeja_el_precio_alcanzable(self):
        resultado = _simular_por_cuota_impl(cuota_objetivo=400000, pie=5000000, plazo_meses=48)
        self.assertTrue(resultado["ok"])
        self.assertGreater(resultado["precio_alcanzable"], 5000000)
        self.assertEqual(
            resultado["precio_alcanzable"],
            resultado["monto_financiable"] + resultado["pie"],
        )

    def test_sugiere_vehiculos_reales_dentro_del_rango(self):
        resultado = _simular_por_cuota_impl(cuota_objetivo=400000, pie=5000000, plazo_meses=48)
        for v in resultado["vehiculos_en_ese_rango"]:
            self.assertLessEqual(v["precio"], resultado["precio_alcanzable"])

    def test_siempre_devuelve_el_disclaimer_del_docx(self):
        self.assertIn("sujeto a evaluacion",
                      _simular_por_cuota_impl(cuota_objetivo=400000)["disclaimer"])

    def test_cuota_negativa_se_rechaza(self):
        self.assertFalse(_simular_por_cuota_impl(cuota_objetivo=-1)["ok"])


class LeadComercialTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911111111", name="Felipe")
        # El cliente dijo CUANDO compra. Hace falta de verdad: plazo_compra solo
        # se guarda si hay evidencia en los mensajes del propio cliente (ver
        # _validar_cuando_compra y el test de abajo sobre "inmediato").
        self.decir("me sirve, quiero comprar en 30 dias")

    def decir(self, texto: str):
        Message.objects.create(conversation=self.conv, role="user", content=texto)

    def test_el_score_sube_a_medida_que_se_capturan_datos(self):
        primero = _registrar_datos_lead_impl("56911111111", {"vehiculo_interes": "Tucson"})
        segundo = _registrar_datos_lead_impl("56911111111", {
            "presupuesto": 20000000, "pie_disponible": 5000000, "plazo_compra": "30 dias"})
        self.assertGreater(segundo["lead_score"], primero["lead_score"])
        self.assertEqual(segundo["temperatura"], "HOT")

    def test_un_turno_sin_datos_nuevos_no_borra_lo_capturado(self):
        # El LLM manda el dict completo cada turno y omite lo que no se hablo;
        # sin esta guarda el lead terminaba la conversacion mas pobre que a la mitad.
        _registrar_datos_lead_impl("56911111111", {"nombre": "Felipe Rojas", "presupuesto": 20000000})
        _registrar_datos_lead_impl("56911111111", {"nombre": "", "presupuesto": None})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.nombre, "Felipe Rojas")
        self.assertEqual(lead.presupuesto, 20000000)

    def test_no_crea_un_lead_por_turno(self):
        for _ in range(3):
            _registrar_datos_lead_impl("56911111111", {"nombre": "Felipe"})
        self.assertEqual(LeadComercial.objects.count(), 1)

    def test_un_campo_desconocido_se_reporta_y_no_revienta(self):
        resultado = _registrar_datos_lead_impl("56911111111", {"color_favorito": "azul"})
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["campos_ignorados"], ["color_favorito"])

    def test_la_temperatura_no_depende_de_conversation_lead_class(self):
        # Regresion: antes se espejaba en Conversation.lead_class, columna que
        # el JSON del LLM pisa al final de cada turno con el valor previo al
        # turno. La fuente de verdad es LeadComercial.temperatura.
        _registrar_datos_lead_impl("56911111111", {
            "nombre": "Felipe", "vehiculo_interes": "Tucson", "presupuesto": 20000000,
            "pie_disponible": 5000000, "plazo_compra": "30 dias"})
        self.conv.lead_class = ""   # simula el pisado del handler al cerrar el turno
        self.conv.save()
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.temperatura, "HOT")

    def test_el_texto_largo_se_recorta_al_max_length_de_la_columna(self):
        # En SQL Server (produccion) el exceso no se trunca: levanta "String or
        # binary data would be truncated" y la tool revienta a mitad de
        # conversacion, perdiendo todo lo capturado en esa llamada.
        _registrar_datos_lead_impl("56911111111", {
            "sentimiento": "positivo, muy interesado en cerrar la compra esta semana"})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(len(lead.sentimiento), 30)

    def test_sin_conversacion_no_crea_lead_huerfano(self):
        self.assertFalse(_registrar_datos_lead_impl("56999999999", {"nombre": "X"})["ok"])


class CuandoCompraTest(TestCase):
    """El campo que vale 18 de los 100 puntos del lead score y que el LLM
    llenaba a ojo (conversacion 29, 2026-09-03): el cliente pidio una
    simulacion "a 24 cuotas", nunca dijo cuando compraria, y el lead quedo
    HOT (83) en vez de WARM (65) con un dato que no existia."""

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911111111", name="Tomas")

    def decir(self, texto: str):
        Message.objects.create(conversation=self.conv, role="user", content=texto)

    def lead(self):
        return LeadComercial.objects.get(conversation=self.conv)

    def test_el_plazo_del_credito_no_se_guarda_como_fecha_de_compra(self):
        self.decir("cuanto me quedaria con un pie de 8 millones, no quiero pasar las 24 cuotas")
        resultado = _registrar_datos_lead_impl("56911111111", {
            "vehiculo_interes": "Suzuki Swift 2023", "plazo_compra": "24 cuotas"})
        self.assertEqual(self.lead().plazo_compra, "")
        self.assertIn("cuando_compra", resultado["campos_rechazados"])

    def test_sin_el_dato_falso_el_lead_no_se_sobrecalienta(self):
        # Los mismos datos de la conversacion 29. Los 18 puntos de plazo_compra
        # eran exactamente la diferencia entre el 83 = HOT que vio el equipo
        # comercial y el 65 = WARM que correspondia.
        self.decir("no quiero pasar las 24 cuotas")
        resultado = _registrar_datos_lead_impl("56911111111", {
            "nombre": "Tomas", "vehiculo_interes": "Suzuki Swift 2023",
            "presupuesto": 12490000, "pie_disponible": 8000000,
            "plazo_compra": "24 cuotas"})
        self.assertEqual(resultado["lead_score"], 65)
        self.assertEqual(resultado["temperatura"], "WARM")

    def test_una_fecha_que_el_cliente_nunca_dijo_no_se_guarda(self):
        # Segunda forma del mismo error, medida contra el modelo real: en 3 de
        # 10 corridas invento "inmediato" sin que nadie hablara de fechas.
        self.decir("cuanto me quedaria con un pie de 8 millones")
        resultado = _registrar_datos_lead_impl("56911111111", {"plazo_compra": "inmediato"})
        self.assertEqual(self.lead().plazo_compra, "")
        self.assertIn("cuando_compra", resultado["campos_rechazados"])

    def test_una_fecha_que_el_cliente_si_dijo_se_guarda(self):
        self.decir("me interesa, el viernes puedo pasar a verlo")
        _registrar_datos_lead_impl("56911111111", {"plazo_compra": "este viernes"})
        self.assertEqual(self.lead().plazo_compra, "este viernes")

    def test_la_tool_expone_cuando_compra_y_escribe_la_columna_plazo_compra(self):
        # El parametro se llama distinto que la columna a proposito: "plazo"
        # colisiona con plazo_meses de simular_financiamiento, que es de donde
        # el modelo saco "24 cuotas". El panel y bot/seguimiento.py siguen
        # leyendo plazo_compra, asi que el mapeo tiene que sobrevivir.
        from langchain.tools import ToolRuntime
        from bot.business.prospeccion import registrar_datos_lead

        # .args es lo que el LLM ve al bindear la tool (tool_call_schema, sin
        # los argumentos inyectados como runtime).
        esquema = registrar_datos_lead.args
        self.assertIn("cuando_compra", esquema)
        self.assertNotIn("plazo_compra", esquema)
        # El codigo del stock tampoco se le pide al LLM: lo deriva el sistema.
        self.assertNotIn("vehiculo_codigo", esquema)

        runtime = ToolRuntime(
            state={"wa_id": "56911111111"}, context=None, config={},
            stream_writer=lambda x: None, tool_call_id=None, store=None, tools=[],
        )
        with patch("bot.business.prospeccion._registrar_datos_lead_impl") as impl:
            impl.return_value = {"ok": True}
            asyncio.run(registrar_datos_lead.ainvoke(
                {"cuando_compra": "este viernes", "runtime": runtime}))
        wa_id, datos = impl.call_args.args
        self.assertEqual(wa_id, "56911111111")
        self.assertEqual(datos["plazo_compra"], "este viernes")

    def test_el_campo_que_falta_se_pide_con_el_nombre_del_parametro(self):
        # datos_que_faltan vuelve al LLM: pedirle "plazo_compra", que no existe
        # en el schema de la tool, es pedirle un campo que no puede mandar.
        resultado = _registrar_datos_lead_impl("56911111111", {"nombre": "Tomas"})
        self.assertIn("cuando_compra", resultado["datos_que_faltan"])
        self.assertNotIn("plazo_compra", resultado["datos_que_faltan"])


class CodigoDeStockDelLeadTest(TestCase):
    """vehiculo_codigo quedaba vacio (conversacion 29: "Suzuki Swift 2023" sin
    el US011 al lado) y el vendedor tenia que buscar el auto a mano en la
    planilla. Se deriva del stock en vez de pedirselo al LLM."""

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911111111", name="Tomas")
        crear_vehiculo(codigo="US011", marca="Suzuki", modelo="Swift",
                       version="1.2 GLX CVT", precio_lista=12490000, precio_oferta=None)

    def lead(self):
        return LeadComercial.objects.get(conversation=self.conv)

    def test_el_codigo_se_deriva_del_vehiculo_de_interes(self):
        _registrar_datos_lead_impl("56911111111", {"vehiculo_interes": "Suzuki Swift 2023"})
        self.assertEqual(self.lead().vehiculo_codigo, "US011")

    def test_un_codigo_inventado_por_el_llm_se_descarta(self):
        # Peor que vacio: el vendedor busca US999 en la planilla y no existe
        # (o peor, existe y es otro auto).
        resultado = _registrar_datos_lead_impl("56911111111", {
            "vehiculo_interes": "Suzuki Swift 2023", "vehiculo_codigo": "US999"})
        self.assertEqual(self.lead().vehiculo_codigo, "US011")
        self.assertIn("vehiculo_codigo", resultado["campos_rechazados"])

    def test_un_codigo_real_del_llm_se_respeta(self):
        crear_vehiculo(codigo="US012", marca="Suzuki", modelo="Swift",
                       version="1.2 GL MT", anio=2022, precio_oferta=None)
        _registrar_datos_lead_impl("56911111111", {
            "vehiculo_interes": "Suzuki Swift", "vehiculo_codigo": "us012"})
        # Y con la escritura de la planilla, que es la que busca el vendedor.
        self.assertEqual(self.lead().vehiculo_codigo, "US012")

    def test_varias_unidades_calzan_y_no_se_elige_una_al_azar(self):
        crear_vehiculo(codigo="US012", marca="Suzuki", modelo="Swift",
                       version="1.2 GL MT", anio=2022, precio_oferta=None)
        _registrar_datos_lead_impl("56911111111", {"vehiculo_interes": "Suzuki Swift"})
        self.assertEqual(self.lead().vehiculo_codigo, "")

    def test_si_el_cliente_cambia_de_auto_el_codigo_viejo_no_queda_pegado(self):
        _registrar_datos_lead_impl("56911111111", {"vehiculo_interes": "Suzuki Swift 2023"})
        crear_vehiculo(codigo="US001", marca="Hyundai", modelo="Tucson")
        _registrar_datos_lead_impl("56911111111", {"vehiculo_interes": "Hyundai Tucson"})
        self.assertEqual(self.lead().vehiculo_codigo, "US001")


class PartePagoTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911111111", name="Felipe")

    def test_registra_el_vehiculo_y_marca_el_lead(self):
        resultado = _registrar_parte_pago_impl("56911111111", marca_modelo="Mazda CX-5", anio=2019)
        self.assertTrue(resultado["ok"])
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertTrue(lead.tiene_parte_pago)
        self.assertEqual(lead.vehiculo_actual, "Mazda CX-5")

    def test_completar_datos_despues_no_duplica_la_solicitud(self):
        # Mismo problema real que ya tuvimos con los Incident de handoff: un
        # registro nuevo por cada dato que llega genera solicitudes repetidas
        # para el equipo comercial por un solo cliente.
        _registrar_parte_pago_impl("56911111111", marca_modelo="Mazda CX-5")
        _registrar_parte_pago_impl("56911111111", marca_modelo="Mazda CX-5", km=80000)
        self.assertEqual(VehiculoPartePago.objects.count(), 1)
        self.assertEqual(VehiculoPartePago.objects.first().km, 80000)

    def test_sin_marca_ni_modelo_se_rechaza(self):
        self.assertFalse(_registrar_parte_pago_impl("56911111111", marca_modelo="  ")["ok"])

    def test_no_expone_ningun_campo_de_monto_tasado(self):
        # El guardrail del docx S15 prohibe comprometer tasaciones: el modelo
        # deliberadamente no tiene donde guardar un valor.
        campos = {f.name for f in VehiculoPartePago._meta.get_fields()}
        self.assertFalse({"valor", "monto", "tasacion", "precio"} & campos)


class MatchPorReferenciaTest(TestCase):
    """Regresiones de la revision de codigo del 2026-09-02.

    El matching era por SUBSTRING sobre "marca modelo version": exigia que la
    frase del cliente apareciera literal y contigua. Las dos formas mas
    naturales de nombrar un auto -- y las que el propio prompt le sugiere al
    LLM -- no matcheaban, y en _resolver_precio_usado eso significa que la
    simulacion se queda con el precio que invento el LLM.
    """

    def setUp(self):
        crear_vehiculo()  # US001 Hyundai Tucson "2.0 AT Value" 2023, $19.990.000

    def test_nombre_completo_con_version_resuelve_el_precio(self):
        self.assertEqual(_resolver_precio_usado("Hyundai Tucson 2.0 AT Value"), 19990000)

    def test_modelo_con_anio_resuelve_el_precio(self):
        self.assertEqual(_resolver_precio_usado("Tucson 2023"), 19990000)

    def test_solo_el_modelo_sigue_resolviendo(self):
        self.assertEqual(_resolver_precio_usado("Tucson"), 19990000)

    def test_palabras_de_relleno_alrededor_del_modelo_resuelven(self):
        self.assertEqual(_resolver_precio_usado("la Hyundai Tucson que vimos"), 19990000)

    def test_una_version_parafraseada_cae_al_modelo_en_vez_de_no_anclar(self):
        # "Full Hybrid" no aparece en la version real, pero el modelo si: es
        # preferible anclar al precio "desde" del modelo que dejar que el LLM
        # ponga el precio.
        self.assertEqual(_resolver_precio_usado("Tucson", version="Full Hybrid"), 19990000)

    def test_un_modelo_inexistente_sigue_devolviendo_none(self):
        self.assertIsNone(_resolver_precio_usado("Ferrari 488"))


class DisponibilidadTest(TestCase):
    """El prompt global promete (regla 8) no afirmar disponibilidad sin
    verificarla. Antes de la revision, la ficha y el anclaje de precio leian
    VehiculoUsado.objects.all(): el bot cotizaba una unidad ya vendida."""

    def setUp(self):
        crear_vehiculo(codigo="US001", disponibilidad="Vendido")
        crear_vehiculo(codigo="US004", marca="Kia", modelo="Sportage",
                       precio_lista=19490000, precio_oferta=18990000)

    def test_no_se_ancla_el_precio_de_una_unidad_vendida(self):
        self.assertIsNone(_resolver_precio_usado("Tucson"))

    def test_la_ficha_avisa_que_la_unidad_ya_no_esta(self):
        resultado = _consultar_ficha_vehiculo_impl("Tucson")
        self.assertFalse(resultado["ok"])
        self.assertIn("YA NO ESTA DISPONIBLE", resultado["motivo"])

    def test_un_modelo_que_nunca_existio_da_un_motivo_distinto(self):
        # "no lo tenemos" y "ya se vendio" habilitan respuestas distintas: la
        # segunda es una oportunidad comercial.
        resultado = _consultar_ficha_vehiculo_impl("Ferrari")
        self.assertFalse(resultado["ok"])
        self.assertNotIn("YA NO ESTA DISPONIBLE", resultado["motivo"])

    def test_la_unidad_disponible_del_mismo_stock_si_responde(self):
        self.assertEqual(_resolver_precio_usado("Sportage"), 18990000)


class AlternativaRespetaElPisoTest(TestCase):
    def setUp(self):
        crear_vehiculo(codigo="US011", modelo="Swift", tipo_vehiculo="Hatchback",
                       precio_lista=12990000, precio_oferta=12490000)
        crear_vehiculo(codigo="US026", marca="Volvo", modelo="XC40",
                       precio_lista=31990000, precio_oferta=30990000)

    def test_con_piso_de_precio_la_alternativa_no_baja_del_piso(self):
        # "Algo mas premium, de 25 millones para arriba" y no hay match: ofrecer
        # el auto mas barato del catalogo no es una alternativa, es ignorar lo
        # que el cliente pidio.
        resultado = _buscar_vehiculos_impl(precio_min=25000000, precio_max=28000000)
        self.assertFalse(resultado["ok"])
        alternativa = resultado["alternativa_mas_cercana"]
        self.assertIsNotNone(alternativa)
        self.assertGreaterEqual(alternativa["precio"], 25000000)


class AgendamientoTallerTest(TestCase):
    """docx S10/S11: para una mantencion hay que saber que auto entra al taller
    y con que kilometraje, y el vehiculo y la patente tienen que quedar
    visibles en la plataforma."""

    def setUp(self):
        from bot.models import Servicio, Sucursal
        self.servicio = Servicio.objects.create(
            nombre="Mantencion 20.000 km", duracion_min=180, precio=219900,
            cliente=settings.CLIENTE_ACTIVO)
        self.sucursal = Sucursal.objects.create(
            nombre="Cavem La Reina", direccion="Av. Bilbao 1234, La Reina",
            horario_texto="Lun-Vie 8:30-18:00", cliente=settings.CLIENTE_ACTIVO)

    def _agendar(self, **extra):
        from bot.business.agendamiento import _agendar_hora_impl
        datos = {
            "servicio_id": self.servicio.id, "sucursal_id": self.sucursal.id,
            "fecha": "2026-09-04", "hora": "11:30", "contacto": "56911111111",
            "nombre": "Felipe Rojas",
        }
        datos.update(extra)
        return _agendar_hora_impl(**datos)

    def test_la_reserva_guarda_vehiculo_patente_anio_y_kilometraje(self):
        from bot.models import Reserva
        resultado = self._agendar(vehiculo="Hyundai Tucson", patente="jklm12",
                                  vehiculo_anio=2023, vehiculo_km=31800)
        self.assertTrue(resultado["ok"])
        reserva = Reserva.objects.get(codigo=resultado["codigo"])
        self.assertEqual(reserva.vehiculo, "Hyundai Tucson")
        self.assertEqual(reserva.vehiculo_anio, 2023)
        self.assertEqual(reserva.vehiculo_km, 31800)

    def test_la_patente_se_normaliza_a_mayusculas(self):
        from bot.models import Reserva
        resultado = self._agendar(patente="jklm12")
        self.assertEqual(Reserva.objects.get(codigo=resultado["codigo"]).patente, "JKLM12")

    def test_buscar_reserva_devuelve_el_vehiculo_para_confirmarselo_al_cliente(self):
        from bot.business.agendamiento import _buscar_reserva_impl
        codigo = self._agendar(vehiculo="Hyundai Tucson", patente="JKLM12",
                               vehiculo_km=31800)["codigo"]
        datos = _buscar_reserva_impl(codigo)
        self.assertEqual(datos["vehiculo"], "Hyundai Tucson")
        self.assertEqual(datos["patente"], "JKLM12")
        self.assertEqual(datos["vehiculo_km"], 31800)

    def test_se_puede_agendar_sin_datos_de_vehiculo(self):
        # El mismo modelo agenda servicios que no son de taller (ej. una prueba
        # de manejo), asi que los campos son opcionales.
        self.assertTrue(self._agendar()["ok"])

    def test_el_prompt_del_taller_pide_kilometraje_y_patente(self):
        from bot.flow.agents.agendamiento import SYSTEM_PROMPT
        self.assertIn("KILOMETRAJE", SYSTEM_PROMPT)
        self.assertIn("patente", SYSTEM_PROMPT)
        # El prompt heredado era agnostico de rubro ("peluqueria, clinica...")
        # y nunca preguntaba por el vehiculo.
        self.assertNotIn("peluqueria", SYSTEM_PROMPT)
