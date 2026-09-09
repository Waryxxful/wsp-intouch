import json

from django.conf import settings
from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from admin_panel import proyeccion


class EsIdProyectadoTest(TestCase):
    def test_negativos_son_proyectados(self):
        self.assertTrue(proyeccion.es_id_proyectado(-1))
        self.assertTrue(proyeccion.es_id_proyectado(-42))

    def test_positivos_y_cero_no_lo_son(self):
        self.assertFalse(proyeccion.es_id_proyectado(1))
        self.assertFalse(proyeccion.es_id_proyectado(0))
        # True es instancia de int en Python (isinstance(True, int) == True);
        # sin el chequeo explicito de bool, es_id_proyectado(True) daria True.
        self.assertFalse(proyeccion.es_id_proyectado(True))

    def test_basura_no_revienta(self):
        # El pk llega de la URL o del body JSON: puede no ser un entero.
        self.assertFalse(proyeccion.es_id_proyectado(None))
        self.assertFalse(proyeccion.es_id_proyectado("-1"))


class DashboardProyeccionTest(TestCase):
    def test_marca_la_respuesta_como_proyeccion(self):
        data = proyeccion.dashboard()
        self.assertIs(data["es_proyeccion"], True)

    def test_usa_las_cifras_del_docx(self):
        data = proyeccion.dashboard()
        self.assertEqual(data["conv_count"], proyeccion.CIFRAS["conversaciones"])

    def test_dashboard_es_internamente_coherente(self):
        # La tarjeta grande de "Conversaciones", el embudo y el donut salen
        # todos de la misma pantalla de apertura: si active_flows no coincide
        # con la suma de agent_distribution (y con el total del embudo y
        # conv_count), la demo se contradice a si misma apenas se abre.
        data = proyeccion.dashboard()
        suma_agentes = sum(a["count"] for a in data["agent_distribution"])
        self.assertEqual(data["active_flows"], suma_agentes)
        self.assertEqual(data["active_flows"], data["conv_count"])
        self.assertEqual(data["active_flows"], data["funnel"][0]["count"])

        # Idem para "Mensajes hoy": el desglose por rol tiene que sumar el
        # mismo total que muestra la tarjeta, y ese total tiene que ser el
        # mismo que el punto de hoy en el grafico de mensajes por dia.
        self.assertEqual(sum(data["msg_by_role"].values()), data["msg_today"])
        self.assertEqual(data["msg_today"], data["chart"][-1]["count"])


@override_settings(DEBUG=True)
class LeadsProyeccionTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("op", "op@x.cl", "x")
        self.client.login(username="op", password="x")

    def test_devuelve_veinte_leads_con_ids_negativos(self):
        r = self.client.get("/demo/api/admin/leads?modo=demo")
        self.assertEqual(r.status_code, 200)
        filas = r.json()
        self.assertEqual(len(filas), 20)
        self.assertTrue(all(f["id"] < 0 for f in filas))

    def test_tiene_las_claves_que_espera_el_frontend(self):
        esperadas = {
            "id", "conversation_id", "nombre", "telefono", "email", "comuna",
            "vehiculo_interes", "vehiculo_codigo", "presupuesto", "pie_disponible",
            "cuota_objetivo", "plazo_compra", "tiene_parte_pago", "vehiculo_actual",
            "intencion", "sentimiento", "urgencia", "temperatura", "lead_score",
            "proxima_accion", "resumen", "ultima_interaccion", "created_at",
        }
        fila = self.client.get("/demo/api/admin/leads?modo=demo").json()[0]
        self.assertEqual(set(fila), esperadas)

    def test_la_mezcla_de_temperaturas_sigue_el_embudo_del_dashboard(self):
        # 97 HOT / 214 calificados / 386 leads => mas WARM que HOT, y COLD el resto.
        filas = self.client.get("/demo/api/admin/leads?modo=demo").json()
        temps = [f["temperatura"] for f in filas]
        self.assertGreater(temps.count("WARM"), temps.count("HOT"))
        self.assertEqual(set(temps), {"HOT", "WARM", "COLD"})

    def test_no_devuelve_leads_reales(self):
        from bot.models import Conversation, LeadComercial
        conv = Conversation.objects.create(wa_id="56911111111", name="Real")
        LeadComercial.objects.create(conversation=conv, nombre="Lead Real",
                                     temperatura="HOT", lead_score=99)
        filas = self.client.get("/demo/api/admin/leads?modo=demo").json()
        self.assertNotIn("Lead Real", [f["nombre"] for f in filas])
        self.assertTrue(all(f["id"] < 0 for f in filas))

    def test_sin_modo_demo_sigue_leyendo_la_base(self):
        r = self.client.get("/demo/api/admin/leads")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])  # base vacia en el test

    def test_filtro_temperatura_funciona_en_modo_demo(self):
        # El selector de temperatura no puede quedar muerto en la demo: si
        # el usuario lo mueve delante de un cliente, la tabla tiene que
        # cambiar de verdad.
        filas = self.client.get("/demo/api/admin/leads?modo=demo&temperatura=HOT").json()
        self.assertGreater(len(filas), 0)
        self.assertTrue(all(f["temperatura"] == "HOT" for f in filas))

    def test_filtro_busqueda_funciona_en_modo_demo(self):
        filas = self.client.get("/demo/api/admin/leads?modo=demo&q=Fuentes").json()
        self.assertGreater(len(filas), 0)
        self.assertTrue(all("fuentes" in f["nombre"].lower() for f in filas))


@override_settings(DEBUG=True)
class ReservasProyeccionTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("op2", "op2@x.cl", "x")
        self.client.login(username="op2", password="x")

    def test_devuelve_nueve_reservas_con_codigo_demo(self):
        r = self.client.get("/demo/api/admin/reservas?modo=demo")
        self.assertEqual(r.status_code, 200)
        filas = r.json()
        self.assertEqual(len(filas), 9)
        self.assertTrue(all(f["codigo"].startswith("DEMO-") for f in filas))

    def test_tiene_las_claves_que_espera_el_frontend(self):
        esperadas = {
            "codigo", "cliente", "telefono", "email", "vehiculo", "patente",
            "vehiculo_anio", "vehiculo_km", "servicio", "sucursal", "fecha",
            "hora", "estado", "created_at",
        }
        fila = self.client.get("/demo/api/admin/reservas?modo=demo").json()[0]
        self.assertEqual(set(fila), esperadas)

    def test_usa_servicios_y_sucursal_que_existen_en_el_seed(self):
        servicios_reales = {
            "Mantencion 10.000 km", "Mantencion 20.000 km",
            "Cambio de aceite y filtro", "Diagnostico electronico",
            "Revision precompra", "Alineacion y balanceo",
            "Revision de frenos", "Sanitizacion de aire acondicionado",
        }
        filas = self.client.get("/demo/api/admin/reservas?modo=demo").json()
        self.assertTrue({f["servicio"] for f in filas} <= servicios_reales)
        self.assertEqual({f["sucursal"] for f in filas}, {"Cavem La Reina"})

    def test_muestra_los_dos_estados(self):
        filas = self.client.get("/demo/api/admin/reservas?modo=demo").json()
        estados = [f["estado"] for f in filas]
        self.assertEqual(estados.count("activa"), 8)
        self.assertEqual(estados.count("cancelada"), 1)

    def test_no_devuelve_reservas_reales(self):
        # Antes este test creaba una base vacia y comprobaba algo que era
        # vacuamente cierto: sin ninguna Reserva real, ninguna corrida podia
        # detectar que reservas() mezclara datos reales. Ahora crea una fila
        # real (mismo patron que ReservasEndpointTest en tests_cavem.py) y
        # verifica que NO aparezca en la respuesta de proyeccion.
        from bot.models import Reserva, Servicio, Sucursal
        servicio = Servicio.objects.create(
            nombre="Cambio de aceite y filtro", cliente=settings.CLIENTE_ACTIVO)
        sucursal = Sucursal.objects.create(
            nombre="Cavem La Reina", cliente=settings.CLIENTE_ACTIVO)
        Reserva.objects.create(
            codigo="RESERVA-REAL-001", contacto="56933333333",
            cliente_nombre="Reserva Real", vehiculo="Auto Real", patente="ZZZZ99",
            servicio=servicio, sucursal=sucursal, fecha="2026-09-05", hora="10:00")
        filas = self.client.get("/demo/api/admin/reservas?modo=demo").json()
        self.assertNotIn("RESERVA-REAL-001", [f["codigo"] for f in filas])
        self.assertTrue(all(f["codigo"].startswith("DEMO-") for f in filas))

    def test_sin_modo_demo_sigue_leyendo_la_base(self):
        r = self.client.get("/demo/api/admin/reservas")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])  # base vacia en el test

    def test_filtro_estado_funciona_en_modo_demo(self):
        # El selector Activas/Canceladas no puede quedar muerto en la demo:
        # si el usuario lo mueve delante de un cliente, la tabla tiene que
        # cambiar de verdad.
        activas = self.client.get("/demo/api/admin/reservas?modo=demo&estado=activa").json()
        canceladas = self.client.get("/demo/api/admin/reservas?modo=demo&estado=cancelada").json()
        self.assertEqual(len(activas), 8)
        self.assertTrue(all(f["estado"] == "activa" for f in activas))
        self.assertEqual(len(canceladas), 1)
        self.assertTrue(all(f["estado"] == "cancelada" for f in canceladas))

    def test_la_hora_que_agenda_el_chat_aparece_en_la_pantalla(self):
        # La conversacion -10 cierra entregando el codigo DEMO-7009. Que esa
        # hora despues se encuentre en Agendamientos es medio punto de la
        # demo, y si el codigo del dialogo no existiera en esta lista el
        # cruce lo dejaria en evidencia.
        filas = {f["codigo"]: f for f in
                 self.client.get("/demo/api/admin/reservas?modo=demo").json()}
        self.assertIn("DEMO-7009", filas)
        reserva = filas["DEMO-7009"]
        texto = " ".join(m["content"] for m in proyeccion.mensajes(-10)["items"])
        self.assertIn(reserva["codigo"], texto)
        self.assertIn(reserva["servicio"].replace("Mantencion", "Mantención"), texto)
        self.assertIn(reserva["hora"], texto)
        self.assertIn(reserva["patente"], texto)
        conv = {c["id"]: c for c in proyeccion.conversaciones()["items"]}[-10]
        self.assertEqual(reserva["cliente"], conv["name"])

    def test_filtro_desde_funciona_en_modo_demo(self):
        filas = self.client.get("/demo/api/admin/reservas?modo=demo&desde=2026-09-09").json()
        self.assertGreater(len(filas), 0)
        self.assertTrue(all(f["fecha"] >= "2026-09-09" for f in filas))


@override_settings(DEBUG=True)
class CampanasProyeccionTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("op3", "op3@x.cl", "x")
        self.client.login(username="op3", password="x")

    def test_son_las_tres_campanas_del_seed(self):
        filas = self.client.get("/demo/api/admin/campanas?modo=demo").json()
        self.assertEqual(
            {f["campaign_type"] for f in filas},
            {"cyber_auto_demo", "renueva_tu_auto", "servicio_tecnico_mantencion"},
        )
        self.assertTrue(all(f["id"] < 0 for f in filas))

    def test_tiene_las_claves_que_espera_el_frontend(self):
        # Mismas 16 claves de `interface Campana` en CampanasPage.tsx.
        esperadas = {
            "id", "nombre", "campaign_type", "template", "segmento", "objetivo",
            "mensaje", "palabra_clave", "activa", "contactos", "enviados",
            "entregados", "leidos", "respuestas", "leads", "conversiones",
        }
        fila = self.client.get("/demo/api/admin/campanas?modo=demo").json()[0]
        self.assertEqual(set(fila), esperadas)

    def test_el_embudo_de_cada_campana_es_decreciente(self):
        # entregados/leidos quedan fuera de esta cadena: son de Meta, no del
        # bot (ver test_entregados_y_leidos_son_null_a_proposito), asi que no
        # hay "decreciente" que verificar sobre ellos. La cadena que si es
        # dato propio del bot: contactos >= enviados >= respuestas >= leads
        # >= conversiones. Una campana donde hay mas leads que respuestas se
        # nota en la reunion y arruina la credibilidad de todo el resto.
        for f in self.client.get("/demo/api/admin/campanas?modo=demo").json():
            etapas = [f["enviados"], f["respuestas"], f["leads"], f["conversiones"]]
            self.assertEqual(etapas, sorted(etapas, reverse=True), f["nombre"])
            self.assertLessEqual(f["enviados"], f["contactos"])

    def test_entregados_y_leidos_son_null_a_proposito(self):
        # Esos dos numeros los sabe Meta, no el bot: se completan aparte con
        # GET /api/admin/template-stats contra la Graph API, que en una demo
        # no esta conectada. Ponerles un numero a mano (aunque sea
        # verosimil) es simular una integracion que no existe en la demo, y
        # contradice el texto fijo de la tarjeta ("una raya significa que
        # todavia no hay dato, no cero"). NO "completar" este dato mas
        # adelante creyendo que falta: es None a proposito, igual que en el
        # camino real (Campana.metricas() en bot/models.py).
        for f in self.client.get("/demo/api/admin/campanas?modo=demo").json():
            self.assertIsNone(f["entregados"], f["nombre"])
            self.assertIsNone(f["leidos"], f["nombre"])

    def test_suma_de_leads_queda_bajo_las_cifras_del_dashboard(self):
        # Las campanas son una fuente de leads, no la unica: la suma de las 3
        # tiene que quedar por debajo de CIFRAS["leads"] (386).
        filas = self.client.get("/demo/api/admin/campanas?modo=demo").json()
        self.assertLess(sum(f["leads"] for f in filas), proyeccion.CIFRAS["leads"])

    def test_no_devuelve_campanas_reales(self):
        from bot.models import Campana
        Campana.objects.create(nombre="Campana Real", campaign_type="real_x",
                               cliente=settings.CLIENTE_ACTIVO)
        filas = self.client.get("/demo/api/admin/campanas?modo=demo").json()
        self.assertNotIn("Campana Real", [f["nombre"] for f in filas])

    def test_sin_modo_demo_sigue_leyendo_la_base(self):
        r = self.client.get("/demo/api/admin/campanas")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])  # base vacia en el test


@override_settings(DEBUG=True)
class ChatsProyeccionTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("op4", "op4@x.cl", "x")
        self.client.login(username="op4", password="x")

    def _lista(self, query="modo=demo"):
        return self.client.get(f"/demo/api/conversations?{query}").json()

    def test_lista_doce_conversaciones_marcadas(self):
        d = self._lista()
        self.assertIs(d["es_proyeccion"], True)
        self.assertEqual(len(d["items"]), 12)
        self.assertEqual(d["count"], 12)
        self.assertTrue(all(c["id"] < 0 for c in d["items"]))

    def test_tiene_las_claves_que_espera_el_frontend(self):
        esperadas = {
            "id", "wa_id", "name", "active_agent", "updated_at", "human_mode",
            "archived", "open_incidents", "stage", "intencion", "resumen",
            "lead_score", "temperatura",
        }
        self.assertEqual(set(self._lista()["items"][0]), esperadas)

    def test_vienen_ordenadas_por_actividad_descendente(self):
        # Conversation.Meta.ordering = ["-updated_at"]: la lista del panel
        # muestra primero la conversacion mas reciente, y la proyeccion no
        # puede salir en otro orden que el camino real.
        fechas = [c["updated_at"] for c in self._lista()["items"]]
        self.assertEqual(fechas, sorted(fechas, reverse=True))

    def test_toda_conversacion_proyectada_tiene_mensajes(self):
        for cid in [c["id"] for c in self._lista()["items"]]:
            d = self.client.get(f"/demo/api/messages/{cid}?modo=demo").json()
            self.assertIs(d["es_proyeccion"], True)
            self.assertIs(d["has_more"], False)
            self.assertGreater(len(d["items"]), 0, f"conversacion {cid} sin mensajes")

    def test_tres_conversaciones_tienen_dialogo_completo(self):
        largos = [
            len(self.client.get(f"/demo/api/messages/{c['id']}?modo=demo").json()["items"])
            for c in self._lista()["items"]
        ]
        self.assertGreaterEqual(sum(1 for n in largos if n >= 12), 3)

    def test_los_mensajes_alternan_usuario_y_bot(self):
        d = self.client.get("/demo/api/messages/-1?modo=demo").json()
        roles = [m["role"] for m in d["items"]]
        self.assertEqual(roles[0], "user")
        self.assertIn("assistant", roles)

    def test_los_mensajes_tienen_las_claves_del_frontend_e_ids_negativos(self):
        esperadas = {"id", "role", "content", "created_at", "media_url", "media_type"}
        vistos = set()
        for cid in [c["id"] for c in self._lista()["items"]]:
            for m in self.client.get(f"/demo/api/messages/{cid}?modo=demo").json()["items"]:
                self.assertEqual(set(m), esperadas)
                # 0 no es negativo: se escaparia de es_id_proyectado y podria
                # llegar a una vista de escritura como un pk cualquiera.
                self.assertTrue(proyeccion.es_id_proyectado(m["id"]), m["id"])
                self.assertNotIn(m["id"], vistos, "id de mensaje repetido")
                vistos.add(m["id"])

    def test_los_mensajes_van_en_orden_cronologico(self):
        for cid in [c["id"] for c in self._lista()["items"]]:
            fechas = [m["created_at"] for m in
                      self.client.get(f"/demo/api/messages/{cid}?modo=demo").json()["items"]]
            self.assertEqual(fechas, sorted(fechas), f"conversacion {cid} desordenada")

    def test_los_ids_de_mensaje_crecen_con_el_orden_cronologico(self):
        # En el camino real el id es la pk autoincremental, asi que ordenar
        # por id es ordenar por fecha -- el panel se apoya en eso (mergeNewer
        # en ChatOperatorPage.tsx ordena por id). Con ids al reves, el primer
        # polling que llegue con la lista vacia dibuja el dialogo dado vuelta.
        for cid in [c["id"] for c in self._lista()["items"]]:
            ids = [m["id"] for m in
                   self.client.get(f"/demo/api/messages/{cid}?modo=demo").json()["items"]]
            self.assertEqual(ids, sorted(ids), f"conversacion {cid}")

    def test_conversacion_sin_transcripcion_devuelve_lista_vacia(self):
        d = self.client.get("/demo/api/messages/-999?modo=demo").json()
        self.assertEqual(d["items"], [])

    def test_la_simulacion_de_la_tucson_sale_de_la_tool_real(self):
        # El runbook de la demo (docs/DEPLOY_CAVEM.md S9) verifica EN VIVO que
        # la Tucson con $5.000.000 de pie da $14.990.000 a financiar. Si el
        # dialogo dijera una cuota calculada a ojo y manana alguien le
        # pregunta lo mismo al bot, las dos cifras no coinciden delante del
        # cliente. Por eso la cuota del dialogo se compara contra la salida de
        # la funcion real del simulador, no contra una constante escrita aca.
        from bot.business.ventas import _simular_financiamiento_impl as simular
        r = simular(precio=19990000, pie=5000000, plazo_meses=48)
        self.assertTrue(r["ok"])
        self.assertEqual(r["monto_base_a_financiar"], 14990000)
        texto = " ".join(
            m["content"] for m in
            self.client.get("/demo/api/messages/-1?modo=demo").json()["items"]
        )
        def pesos(n):
            return f"${n:,}".replace(",", ".")
        self.assertIn(pesos(19990000), texto)
        self.assertIn(pesos(5000000), texto)
        self.assertIn(pesos(14990000), texto)
        self.assertIn(pesos(r["cuota_mensual_estimada"]), texto)
        self.assertIn("48 meses", texto)

    def test_las_simulaciones_cierran_con_la_advertencia_del_prompt_global(self):
        # bot/flow/global_prompt.py obliga a cerrar toda simulacion con esa
        # frase. Un dialogo de demostracion que no la lleve muestra un bot
        # que no cumple su propio prompt. La comprobacion es por conversacion
        # y no por mensaje porque el bot real manda cada parrafo como un
        # mensaje aparte (_dividir_en_mensajes en bot/whatsapp/handlers.py):
        # la advertencia queda en el mensaje siguiente al de la cuota.
        for cid in [c["id"] for c in self._lista()["items"]]:
            items = self.client.get(f"/demo/api/messages/{cid}?modo=demo").json()["items"]
            texto = " ".join(m["content"] for m in items)
            if "a financiar" in texto:
                self.assertIn("Simulación referencial", texto, f"conversacion {cid}")

    def test_ningun_dialogo_compromete_un_monto_de_tasacion(self):
        # Regla dura del prompt global y del de ventas: "NUNCA le des un monto
        # de tasacion al cliente, ni siquiera aproximado". Si el dialogo
        # proyectado la rompe, la demo muestra al bot haciendo justo lo que el
        # guardrail prohibe.
        for cid in [c["id"] for c in self._lista()["items"]]:
            items = self.client.get(f"/demo/api/messages/{cid}?modo=demo").json()["items"]
            texto = " ".join(m["content"] for m in items if m["role"] == "assistant")
            if "tasación" in texto:
                self.assertIn("especialista", texto, f"conversacion {cid}")

    def test_no_devuelve_conversaciones_reales(self):
        from bot.models import Conversation
        Conversation.objects.create(wa_id="56922222222", name="Conversacion Real")
        d = self._lista()
        self.assertNotIn("Conversacion Real", [c["name"] for c in d["items"]])

    def test_no_devuelve_mensajes_reales(self):
        from bot.models import Conversation, Message
        conv = Conversation.objects.create(wa_id="56933333333", name="Otra Real")
        Message.objects.create(conversation=conv, role="user", content="Mensaje Real")
        d = self.client.get(f"/demo/api/messages/{conv.pk}?modo=demo").json()
        self.assertNotIn("Mensaje Real", [m["content"] for m in d["items"]])

    def test_sin_modo_demo_los_dos_endpoints_siguen_leyendo_la_base(self):
        r = self.client.get("/demo/api/conversations")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [])
        self.assertNotIn("es_proyeccion", r.json())
        from bot.models import Conversation
        conv = Conversation.objects.create(wa_id="56944444444", name="Real")
        r = self.client.get(f"/demo/api/messages/{conv.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [])
        self.assertNotIn("es_proyeccion", r.json())

    def test_filtro_estado_funciona_en_modo_demo(self):
        # Ninguna proyectada esta archivada ni vencida: "cerradas" tiene que
        # devolver 0. Si el filtro no se aplicara, devolveria las 12.
        self.assertEqual(len(self._lista("modo=demo&estado=abiertas")["items"]), 12)
        self.assertEqual(len(self._lista("modo=demo&estado=cerradas")["items"]), 0)
        self.assertEqual(self._lista("modo=demo&estado=cerradas")["count"], 0)
        self.assertEqual(len(self._lista("modo=demo&estado=todas")["items"]), 12)

    def test_filtro_de_fechas_funciona_en_modo_demo(self):
        from datetime import date, timedelta
        hoy = date.today()
        lunes = hoy - timedelta(days=hoy.weekday())
        # Desde el lunes entran las 12: la proyeccion no se escalona mas
        # atras que el lunes justamente para eso.
        self.assertEqual(len(self._lista(f"modo=demo&desde={lunes}")["items"]), 12)
        self.assertEqual(len(self._lista(f"modo=demo&hasta={hoy}")["items"]), 12)
        # Y filtra de verdad: nada antes del lunes ni despues de hoy.
        self.assertEqual(len(self._lista(f"modo=demo&hasta={lunes - timedelta(days=1)}")["items"]), 0)
        self.assertEqual(len(self._lista(f"modo=demo&desde={hoy + timedelta(days=1)}")["items"]), 0)
        # Un dia suelto deja fuera a las conversaciones de los otros dias
        # (salvo el lunes, cuando la proyeccion entera cae en el mismo dia).
        de_hoy = self._lista(f"modo=demo&desde={hoy}")["items"]
        self.assertTrue(all(c["updated_at"].startswith(hoy.isoformat()) for c in de_hoy))
        self.assertGreater(len(de_hoy), 0)

    def test_fecha_invalida_se_ignora_igual_que_en_el_camino_real(self):
        self.assertEqual(len(self._lista("modo=demo&desde=ayer")["items"]), 12)


class CoherenciaProyeccionTest(TestCase):
    def test_cada_lead_apunta_a_una_conversacion_que_existe(self):
        ids = {c["id"] for c in proyeccion.conversaciones()["items"]}
        for lead in proyeccion.leads():
            if lead["conversation_id"] is not None:
                self.assertIn(lead["conversation_id"], ids, lead["nombre"])

    def test_la_conversacion_dice_lo_mismo_que_su_lead(self):
        # El panel muestra las dos pantallas en la misma demo: si el lead -N y
        # la conversacion -N no hablan de la misma persona, con la misma
        # temperatura y el mismo score, la incoherencia se ve en vivo.
        convs = {c["id"]: c for c in proyeccion.conversaciones()["items"]}
        for lead in proyeccion.leads():
            cid = lead["conversation_id"]
            if cid is None:
                continue
            conv = convs[cid]
            self.assertEqual(conv["name"], lead["nombre"])
            self.assertEqual(conv["temperatura"], lead["temperatura"])
            self.assertEqual(conv["lead_score"], lead["lead_score"])
            self.assertEqual(conv["resumen"], lead["resumen"])
            self.assertEqual(conv["intencion"], lead["intencion"])

    def test_el_lead_y_su_chat_muestran_la_misma_ultima_interaccion(self):
        # Las dos pantallas se abren en la misma demo: si Leads dice que la
        # persona escribio hace cuatro dias y su chat dice que fue hoy, la
        # contradiccion se ve cruzando dos pestanas. Por eso las dos fechas
        # salen del mismo calculo (_iso_actividad), no de dos tablas escritas
        # a mano por separado.
        convs = {c["id"]: c for c in proyeccion.conversaciones()["items"]}
        for lead in proyeccion.leads():
            cid = lead["conversation_id"]
            if cid is None:
                self.assertIsNone(lead["ultima_interaccion"], lead["nombre"])
                continue
            self.assertEqual(lead["ultima_interaccion"], convs[cid]["updated_at"], lead["nombre"])

    def test_el_lead_se_creo_antes_de_su_ultima_interaccion(self):
        for lead in proyeccion.leads():
            if lead["ultima_interaccion"] is None:
                continue
            self.assertLess(lead["created_at"], lead["ultima_interaccion"], lead["nombre"])

    def test_la_actividad_proyectada_cae_dentro_de_la_semana_en_curso(self):
        # El filtro de fechas de la lista de chats arranca por defecto en el
        # lunes de esta semana: una conversacion mas vieja que ese lunes
        # queda escondida detras de un filtro que el expositor no puso.
        from datetime import date, timedelta
        hoy = date.today()
        lunes = hoy - timedelta(days=hoy.weekday())
        for conv in proyeccion.conversaciones()["items"]:
            dia = date.fromisoformat(conv["updated_at"][:10])
            self.assertGreaterEqual(dia, lunes, conv["name"])
            self.assertLessEqual(dia, hoy, conv["name"])

    def test_los_vehiculos_del_lead_aparecen_en_su_conversacion(self):
        # El vehiculo de interes del lead sale de lo que se hablo en el chat:
        # si el lead dice "Creta" y en la transcripcion nunca aparece, el
        # resumen de la ficha no se sostiene al abrir la conversacion.
        for lead in proyeccion.leads():
            cid = lead["conversation_id"]
            if cid is None:
                continue
            texto = " ".join(m["content"] for m in proyeccion.mensajes(cid)["items"])
            modelo = lead["vehiculo_interes"].split()[1]
            self.assertIn(modelo, texto, f"{lead['nombre']} / {modelo}")
            self.assertIn(lead["vehiculo_codigo"], texto, lead["nombre"])


@override_settings(DEBUG=True)
class EscrituraConIdProyectadoTest(TestCase):
    """La proyeccion es de solo lectura: ninguna vista de escritura puede
    crear ni modificar una fila real cuando el pk (o el conversation_id del
    body) apunta a un dato proyectado.

    api_conversation_archive y api_conversation_mode reciben el pk por la
    URL, que sigue registrada con <int:pk> (solo Task 5 cambio la lectura de
    mensajes a re_path): un id negativo ni siquiera matchea la ruta, asi que
    el 404 lo entrega el router de Django antes de llegar a la vista.
    api_send_message si recibe el id por el body, asi que ahi el 404 lo tira
    get_object_or_404. api_campana_enviar no usa pk en absoluto -- identifica
    la campana por campaign_type, y el mismo filtro que ya trae la vista deja
    afuera cualquier campaign_type que solo exista en la proyeccion.

    Estos tests fijan el resultado observable (404/400 y cero filas nuevas o
    modificadas), no el mecanismo interno: si alguien reescribe una de estas
    vistas y dejar de dar ese resultado es justo lo que tiene que hacer
    fallar la suite.
    """

    def setUp(self):
        User.objects.create_superuser("op5", "op5@x.cl", "x")
        self.client.login(username="op5", password="x")
        from bot.models import Conversation
        self.conv_real = Conversation.objects.create(wa_id="56900000000", name="Real Op5")

    def test_archivar_una_conversacion_proyectada_da_404(self):
        from bot.models import Conversation
        antes = list(Conversation.objects.values_list("pk", "archived"))
        r = self.client.post("/demo/api/conversations/-1/archive",
                             data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(list(Conversation.objects.values_list("pk", "archived")), antes)

    def test_archivar_con_modo_demo_en_la_query_tambien_da_404(self):
        from bot.models import Conversation
        antes = Conversation.objects.count()
        r = self.client.post("/demo/api/conversations/-1/archive?modo=demo",
                             data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Conversation.objects.count(), antes)

    def test_cambiar_a_modo_humano_una_conversacion_proyectada_da_404(self):
        from bot.models import Conversation
        antes = list(Conversation.objects.values_list("pk", "flow_data"))
        r = self.client.post("/demo/api/conversations/-1/mode",
                             data='{"human_mode": true}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(list(Conversation.objects.values_list("pk", "flow_data")), antes)

    def test_modo_humano_con_modo_demo_en_la_query_tambien_da_404(self):
        from bot.models import Conversation
        antes = Conversation.objects.count()
        r = self.client.post("/demo/api/conversations/-1/mode?modo=demo",
                             data='{"human_mode": true}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Conversation.objects.count(), antes)

    def test_enviar_mensaje_a_una_conversacion_proyectada_da_404(self):
        from bot.models import Message
        antes = Message.objects.count()
        r = self.client.post("/demo/api/admin/send-message",
                             data='{"conversation_id": -1, "text": "hola"}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Message.objects.count(), antes)

    def test_enviar_mensaje_con_modo_demo_en_la_query_tambien_da_404(self):
        from bot.models import Message
        antes = Message.objects.count()
        r = self.client.post("/demo/api/admin/send-message?modo=demo",
                             data='{"conversation_id": -1, "text": "hola"}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Message.objects.count(), antes)

    def test_enviar_campana_con_un_campaign_type_proyectado_no_envia_nada(self):
        from bot.models import Campana, CampaignSend
        campaign_type = proyeccion.campanas()[0]["campaign_type"]
        # Confirma la premisa: ese campaign_type no existe como Campana real
        # en la base de este test (si algun dia se creara, este test dejaria
        # de probar lo que dice probar).
        self.assertEqual(Campana.objects.filter(campaign_type=campaign_type).count(), 0)
        antes = CampaignSend.objects.count()
        r = self.client.post(
            "/demo/api/admin/campanas/enviar",
            data=json.dumps({"campaign_type": campaign_type, "csv_text": "wa_id\n56911111111"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(CampaignSend.objects.count(), antes)

    def test_enviar_campana_con_modo_demo_en_la_query_tambien_falla(self):
        from bot.models import CampaignSend
        campaign_type = proyeccion.campanas()[0]["campaign_type"]
        antes = CampaignSend.objects.count()
        r = self.client.post(
            "/demo/api/admin/campanas/enviar?modo=demo",
            data=json.dumps({"campaign_type": campaign_type, "csv_text": "wa_id\n56911111111"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(CampaignSend.objects.count(), antes)

    def test_la_conversacion_real_no_se_ve_afectada_por_ningun_intento(self):
        # Ningun request contra el id proyectado -1 puede filtrarse y tocar
        # por error la unica conversacion real que existe en la base.
        self.client.post("/demo/api/conversations/-1/archive",
                         data="{}", content_type="application/json")
        self.client.post("/demo/api/conversations/-1/mode",
                         data='{"human_mode": true}', content_type="application/json")
        self.client.post("/demo/api/admin/send-message",
                         data='{"conversation_id": -1, "text": "hola"}',
                         content_type="application/json")
        self.conv_real.refresh_from_db()
        self.assertFalse(self.conv_real.archived)
        self.assertEqual(self.conv_real.get_flow(), {})
