"""Normalizacion de flow_data: sin vacios, una clave por concepto, montos numero.

El caso testigo es la conversacion 29 de produccion (2026-09-03): despues de 4
mensajes de un cliente real, flow_data tenia 4 claves vacias, tres claves para
"plazo" contradiciendose entre si (24 vs 12 cuotas) y los montos como string
formateado. Ese diccionario entero se le inyecta al especialista en el system
prompt en cada turno, asi que la basura no solo cuesta tokens: la lee el modelo.
Ver bot/flow/flow_data.py para el detalle.
"""
from django.test import SimpleTestCase

from bot.flow.flow_data import normalizar_flow_data

# Textual, tal como estaba en la BD.
FLOW_DATA_CONVERSACION_29 = {
    "modelo_interes": "", "plazo_compra": "", "presupuesto": "$8.000.000",
    "comuna": "", "monto_pie": 8000000, "plazo_actual": 24,
    "plazo_preferido": None, "plazo": "12", "cuota_mensual": "$424.312",
    "monto_financiar": "$4.490.000", "costo_total": "$13.091.743",
}


class CasoRealConversacion29Test(SimpleTestCase):
    def test_queda_limpio_y_sin_contradicciones(self):
        self.assertEqual(normalizar_flow_data(FLOW_DATA_CONVERSACION_29), {
            "presupuesto": 8000000,
            "pie_disponible": 8000000,
            "plazo": 12,
            "cuota_mensual": 424312,
            "monto_financiar": 4490000,
            "costo_total": 13091743,
        })

    def test_de_11_claves_quedan_6(self):
        # Casi la mitad del bloque que leia el modelo en cada turno era ruido.
        self.assertEqual(len(normalizar_flow_data(FLOW_DATA_CONVERSACION_29)), 6)


class VaciosTest(SimpleTestCase):
    def test_descarta_string_vacio_y_none(self):
        self.assertEqual(normalizar_flow_data({"comuna": "", "plazo_compra": None}), {})

    def test_descarta_los_placeholders_que_escribe_el_modelo(self):
        # "no especificado" no es un dato: guardarlo hace que el especialista
        # lo lea como si lo fuera.
        for basura in ("no especificado", "N/A", "-", "null", "  "):
            self.assertEqual(normalizar_flow_data({"comuna": basura}), {}, basura)

    def test_false_y_cero_se_conservan(self):
        # `False` y `0` son datos reales (no tiene parte de pago / pie 0). Si
        # se filtrara con `if not valor` se perderian.
        self.assertEqual(normalizar_flow_data({"tiene_parte_pago": False, "pie_disponible": 0}),
                         {"tiene_parte_pago": False, "pie_disponible": 0})

    def test_descarta_colecciones_vacias(self):
        self.assertEqual(normalizar_flow_data({"sucursales": [], "reserva_actual": {}}), {})


class AliasTest(SimpleTestCase):
    def test_un_solo_plazo(self):
        salida = normalizar_flow_data({"plazo_actual": 24, "plazo_preferido": 36, "plazo": "12"})
        self.assertEqual(salida, {"plazo": 12})

    def test_el_alias_nuevo_pisa_al_valor_viejo_al_hacer_merge(self):
        # Este es el punto de canonizar ANTES del merge que hacen graph.py y
        # cola_envio.py: el turno nuevo dice 36 cuotas y tiene que ganar.
        previo = normalizar_flow_data({"plazo": 12})
        nuevo = normalizar_flow_data({"plazo_preferido": 36})
        self.assertEqual({**previo, **nuevo}, {"plazo": 36})

    def test_el_nombre_canonico_explicito_le_gana_al_alias(self):
        self.assertEqual(normalizar_flow_data({"plazo": 12, "plazo_actual": 24}), {"plazo": 12})

    def test_pie_y_vehiculo_usan_el_nombre_del_crm(self):
        # Mismo vocabulario que LeadComercial / registrar_datos_lead, para que
        # flow_data y el lead no se contradigan.
        self.assertEqual(
            normalizar_flow_data({"monto_pie": 3000000, "modelo_interes": "Tucson"}),
            {"pie_disponible": 3000000, "vehiculo_interes": "Tucson"})

    def test_plazo_compra_no_es_alias_de_plazo(self):
        # `plazo_compra` es CUANDO compra (campo de LeadComercial, 18 puntos de
        # lead score); `plazo` es el numero de cuotas. Unirlos romperia los dos.
        self.assertEqual(
            normalizar_flow_data({"plazo_compra": "30 dias", "plazo": 12}),
            {"plazo_compra": "30 dias", "plazo": 12})

    def test_la_cuota_objetivo_no_se_mezcla_con_la_simulada(self):
        # Son datos distintos: lo que el cliente QUIERE pagar y lo que dio la
        # simulacion. La familia de cuota_objetivo se evalua primero para que
        # el patron amplio de cuota_mensual no se la lleve.
        self.assertEqual(
            normalizar_flow_data({"cuota_deseada": 300000, "cuota_estimada": 424312}),
            {"cuota_objetivo": 300000, "cuota_mensual": 424312})

    def test_cuota_inicial_es_el_pie_y_no_una_cuota(self):
        # "cuota inicial" es como se le dice al pie fuera de Chile, y el modelo
        # a veces lo escribe asi.
        self.assertEqual(normalizar_flow_data({"cuota_inicial": 8000000}),
                         {"pie_disponible": 8000000})


class ClavesInventadasPorElLLMTest(SimpleTestCase):
    """Nombres REALES que devolvio el extractor contra el LLM de produccion.

    Se lo sondeo 4 veces sobre los dos turnos de financiamiento de la
    conversacion 29 (2026-09-03): para el mismo concepto invento estos nombres.
    Por eso las familias son patrones y no una lista de sinonimos -- una lista
    cerrada ya habria envejecido con estas 4 corridas.
    """

    def test_todas_las_variantes_de_plazo_observadas(self):
        for clave in ("plazo_actual", "plazo_financiado", "plazo_cliente_deseado",
                      "plazo_minimo_aceptable", "plazo_minimo_aceptado",
                      "preferencia_plazo", "cuotas"):
            self.assertEqual(normalizar_flow_data({clave: 24}), {"plazo": 24}, clave)

    def test_variantes_de_presupuesto_y_de_auto_observadas(self):
        self.assertEqual(normalizar_flow_data({"monto_presupuesto": "8.000.000"}),
                         {"presupuesto": 8000000})
        self.assertEqual(normalizar_flow_data({"modelo_auto": "Swift"}),
                         {"vehiculo_interes": "Swift"})

    def test_el_numero_con_la_unidad_pegada_queda_numero(self):
        # El modelo alterna entre 24 y "24 cuotas" segun la corrida; sin esto
        # `plazo` no se puede comparar entre turnos.
        self.assertEqual(normalizar_flow_data({"plazo_actual": "24 cuotas"}), {"plazo": 24})
        self.assertEqual(normalizar_flow_data({"plazo": "12 meses"}), {"plazo": 12})

    def test_no_se_come_conceptos_vecinos(self):
        # Todos estos caen cerca de una familia y NO son lo mismo: el auto que
        # entrega en parte de pago, el codigo del stock, cuando le entregan el
        # auto, y en cuanto tiempo piensa comprar.
        vecinos = {"vehiculo_actual": "Yaris 2015", "modelo_actual": "Yaris",
                   "vehiculo_codigo": "US003", "plazo_entrega": "15 dias",
                   "plazo_compra": "30 dias"}
        self.assertEqual(normalizar_flow_data(vecinos), vecinos)


class MontosTest(SimpleTestCase):
    def test_formatos_chilenos(self):
        casos = {"$8.000.000": 8000000, "424.312": 424312, "12": 12,
                 "8000000": 8000000, "$ 13.091.743": 13091743,
                 "8000000 CLP": 8000000, "$8,000,000": 8000000}
        for crudo, esperado in casos.items():
            self.assertEqual(normalizar_flow_data({"presupuesto": crudo})["presupuesto"],
                             esperado, crudo)

    def test_decimales_con_coma(self):
        self.assertEqual(normalizar_flow_data({"tasa": "1,25"})["tasa"], 1.25)

    def test_no_toca_lo_que_no_es_un_monto(self):
        intactos = {"nombre": "Tomas", "plazo_compra": "30 dias",
                    "fecha_visita": "2026-09-10", "hora": "10:30",
                    "resumen": "Cliente busca un SUV usado."}
        self.assertEqual(normalizar_flow_data(intactos), intactos)

    def test_montos_coloquiales(self):
        # Medido contra el LLM real el 2026-09-07: pidiendole el monto como
        # entero, "15 millones" y "20 palos" volvian en 0 (3 de 21 presupuestos
        # perdidos, 15 puntos del lead score cada uno). Se le pide como el
        # cliente lo dijo y se convierte aca.
        casos = {"15 millones": 15000000, "20 palos": 20000000,
                 "3 millones": 3000000, "1 millón": 1000000,
                 "500 mil": 500000, "800 lucas": 800000,
                 "18 millones de pesos": 18000000, "2,5 millones": 2500000}
        for crudo, esperado in casos.items():
            self.assertEqual(
                normalizar_flow_data({"presupuesto": crudo})["presupuesto"],
                esperado, crudo)

    def test_una_magnitud_que_no_conocemos_deja_el_texto_intacto(self):
        # Vale mas un string que el vendedor puede leer que un numero inventado.
        for crudo in ("15 chirolas", "un par de millones", "30 dias"):
            self.assertEqual(
                normalizar_flow_data({"presupuesto": crudo})["presupuesto"], crudo)

    def test_la_magnitud_no_aplica_a_las_claves_no_numericas(self):
        # "500 mil" en un resumen es prosa, no un monto que haya que convertir.
        self.assertEqual(
            normalizar_flow_data({"resumen": "500 mil"})["resumen"], "500 mil")

    def test_no_convierte_identificadores(self):
        # Un RUT/telefono/codigo pasado a int pierde ceros a la izquierda y
        # deja de ser lo que era.
        identificadores = {"rut": "12345678", "telefono": "0912345678",
                           "vehiculo_codigo": "003"}
        self.assertEqual(normalizar_flow_data(identificadores), identificadores)


class RobustezTest(SimpleTestCase):
    def test_es_idempotente(self):
        una = normalizar_flow_data(FLOW_DATA_CONVERSACION_29)
        self.assertEqual(normalizar_flow_data(una), una)

    def test_lo_que_no_es_dict_devuelve_vacio(self):
        for basura in (None, "", [], "texto", 3):
            self.assertEqual(normalizar_flow_data(basura), {})

    def test_conserva_las_claves_legitimas_y_las_inventadas(self):
        # No se trata de imponer un vocabulario cerrado: el especialista guarda
        # claves segun la conversacion y solo se matan las vacias y las
        # redundantes.
        util = {"nombre": "Tomas", "intencion": "compra vehiculo",
                "resumen": "Busca un SUV", "vehiculo_interes": "Tucson",
                "servicio_id": 3, "sucursal_id": 1, "fecha_visita": "2026-09-10",
                "reserva_actual": {"codigo": "ABC123"},
                "prefiere_color_rojo": True, "modo": "HUMAN"}
        self.assertEqual(normalizar_flow_data(util), util)

    def test_no_toca_el_contenido_de_los_dicts_anidados(self):
        # `reserva_actual` lo arma el codigo (admin_panel/views.py), no el LLM:
        # ahi no hay ruido que limpiar y recursar solo agregaria riesgo.
        anidado = {"reserva_actual": {"codigo": "ABC123", "hora": "", "precio": "$10.000"}}
        self.assertEqual(normalizar_flow_data(anidado), anidado)


class MontoAbreviadoTest(SimpleTestCase):
    """Caso real: conversacion del 2026-09-07, turno 7.

    El cliente dijo "27 millones" y quedo guardado como el entero 27000000.
    Dos turnos despues el extractor lo escribio como "$27M", que este modulo no
    sabia leer, y el bot le contesto "Segun tu presupuesto de $27M" -- leyendo
    el string degradado en vez del numero. "M"/"MM" es como se abrevia un monto
    en Chile y faltaba, teniendo "k" desde el principio.
    """

    def test_abreviatura_de_millones(self):
        casos = {"$27M": 27000000, "27M": 27000000, "27 M": 27000000,
                 "$27MM": 27000000, "1,5M": 1500000}
        for crudo, esperado in casos.items():
            self.assertEqual(
                normalizar_flow_data({"presupuesto": crudo})["presupuesto"],
                esperado, crudo)

    def test_la_abreviatura_no_aplica_a_plazo(self):
        # "12m" en un plazo son 12 MESES, no 12 millones. `plazo` es clave
        # numerica igual que los montos, asi que sin esta separacion el mismo
        # sufijo significaria dos cosas incompatibles.
        self.assertEqual(
            normalizar_flow_data({"plazo": "12m"})["plazo"], "12m")


class FusionTest(SimpleTestCase):
    """Que un valor nuevo PEOR no pise a uno bueno que ya estaba.

    Mismo caso real del 2026-09-07: `{**previo, **nuevo}` dejaba que un
    "$27M" ilegible reemplazara al 27000000 que ya se sabia. El string se
    sigue guardando cuando no hay nada mejor (ver
    test_una_magnitud_que_no_conocemos_deja_el_texto_intacto), pero no puede
    degradar un numero ya capturado.
    """

    def test_un_string_ilegible_no_pisa_un_numero(self):
        from bot.flow.flow_data import fusionar_flow_data
        self.assertEqual(
            fusionar_flow_data({"presupuesto": 27000000}, {"presupuesto": "un monton"}),
            {"presupuesto": 27000000})

    def test_un_numero_nuevo_si_pisa_al_viejo(self):
        from bot.flow.flow_data import fusionar_flow_data
        # El cliente cambio de idea: ese es el caso que el merge existe para
        # resolver (conversacion 29, plazo 24 -> 12).
        self.assertEqual(
            fusionar_flow_data({"presupuesto": 27000000}, {"presupuesto": "15 millones"}),
            {"presupuesto": 15000000})

    def test_el_string_entra_si_no_habia_nada(self):
        from bot.flow.flow_data import fusionar_flow_data
        self.assertEqual(
            fusionar_flow_data({}, {"presupuesto": "15 chirolas"}),
            {"presupuesto": "15 chirolas"})

    def test_las_claves_no_numericas_se_pisan_normalmente(self):
        from bot.flow.flow_data import fusionar_flow_data
        self.assertEqual(
            fusionar_flow_data({"comuna": "Ñuñoa"}, {"comuna": "La Reina"}),
            {"comuna": "La Reina"})

    def test_normaliza_las_dos_puntas(self):
        from bot.flow.flow_data import fusionar_flow_data
        self.assertEqual(
            fusionar_flow_data({"modelo_interes": "Swift"}, {"presupuesto": "$27M"}),
            {"vehiculo_interes": "Swift", "presupuesto": 27000000})
