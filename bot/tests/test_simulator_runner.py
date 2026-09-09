from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from bot.flow.graph import RESPUESTA_GENERICA_JSON_INVALIDO


class CorrerEscenarioTest(SimpleTestCase):
    def _turno_base(self, **overrides):
        turno = {
            "cliente_dice": "hola", "bot_responde": "hola, en que le ayudo",
            "intent": None, "active_agent": "faq", "acciones": [],
            "modelo_imagen": None, "imagen_enviada": False, "flow_data": {},
        }
        turno.update(overrides)
        return turno

    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_no_llama_al_juez_si_hay_un_fallo_de_codigo(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        turnos = [self._turno_base(bot_responde=RESPUESTA_GENERICA_JSON_INVALIDO)]
        mock_create_app.return_value = (MagicMock(), {"hilo-1": turnos})

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": ["z"], "max_turns": 5})

        mock_evaluar.assert_not_called()
        self.assertFalse(resultado["paso"])
        self.assertEqual(len(resultado["fallos_de_codigo"]), 1)
        self.assertEqual(resultado["fallos_de_juez"], [])

    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_pasa_si_no_hay_fallos_de_codigo_y_el_juez_aprueba_todo(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        turnos = [self._turno_base()]
        mock_create_app.return_value = (MagicMock(), {"hilo-1": turnos})
        mock_evaluar.return_value = (
            {
                "no_repregunta_dato_conocido": {"valor": True, "razonamiento": "ok"},
                "avanza_hacia_objetivo": {"valor": 4, "razonamiento": "ok"},
                "tono_apropiado_whatsapp": {"valor": 5, "razonamiento": "ok"},
            },
            ["no_repregunta_dato_conocido", "avanza_hacia_objetivo", "tono_apropiado_whatsapp"],
        )

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": [], "max_turns": 5})

        mock_evaluar.assert_called_once()
        self.assertTrue(resultado["paso"])
        self.assertEqual(resultado["fallos_de_juez"], [])

    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_no_pasa_si_el_juez_marca_algun_criterio_estricto_en_false(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        turnos = [self._turno_base()]
        mock_create_app.return_value = (MagicMock(), {"hilo-1": turnos})
        mock_evaluar.return_value = (
            {"no_repregunta_dato_conocido": {"valor": False, "razonamiento": "volvio a preguntar el rut"}},
            ["no_repregunta_dato_conocido"],
        )

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": [], "max_turns": 5})

        self.assertFalse(resultado["paso"])
        self.assertEqual(len(resultado["fallos_de_juez"]), 1)
        self.assertIn("volvio a preguntar el rut", resultado["fallos_de_juez"][0])

    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_no_pasa_si_el_juez_no_devuelve_nada_valido(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        turnos = [self._turno_base()]
        mock_create_app.return_value = (MagicMock(), {"hilo-1": turnos})
        mock_evaluar.return_value = ({}, ["no_repregunta_dato_conocido"])

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": [], "max_turns": 5})

        self.assertFalse(resultado["paso"])
        self.assertEqual(len(resultado["fallos_de_juez"]), 1)

    # -- Hallazgo 2: una conversacion sin turnos capturados nunca puede
    # reportarse como "paso". --
    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_no_pasa_si_no_se_captura_ningun_turno(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        mock_create_app.return_value = (MagicMock(), {})  # capturas vacio: ningun thread_id

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": [], "max_turns": 5})

        self.assertFalse(resultado["paso"])
        self.assertEqual(resultado["num_turnos"], 0)
        self.assertEqual(resultado["fallos_de_codigo"], ["conversacion_vacia"])
        self.assertEqual(resultado["fallos_de_juez"], [])
        self.assertEqual(resultado["turnos"], [])
        mock_evaluar.assert_not_called()

    # -- Hallazgo 4: claves faltantes en la respuesta del juez son un fallo,
    # no un pase vacuo. --
    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_no_pasa_si_el_juez_omite_un_criterio_esperado(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        turnos = [self._turno_base()]
        mock_create_app.return_value = (MagicMock(), {"hilo-1": turnos})
        mock_evaluar.return_value = (
            {"no_repregunta_dato_conocido": {"valor": True, "razonamiento": "ok"}},
            ["no_repregunta_dato_conocido", "avanza_hacia_objetivo"],
        )

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": [], "max_turns": 5})

        self.assertFalse(resultado["paso"])
        self.assertEqual(len(resultado["fallos_de_juez"]), 1)

    # -- Hallazgo 4: una forma de respuesta inesperada del juez (ej. un bool
    # pelado en vez de {"valor":..., "razonamiento":...}) no debe reventar
    # correr_escenario -- debe tratarse como un fallo. --
    @patch("bot.simulator.runner.evaluar_conversacion")
    @patch("bot.simulator.runner.crear_cliente_simulado")
    @patch("bot.simulator.runner.run_multiturn_simulation")
    @patch("bot.simulator.runner.create_app")
    def test_no_revienta_si_el_juez_devuelve_un_valor_con_forma_invalida(
        self, mock_create_app, mock_run_sim, mock_crear_cliente, mock_evaluar,
    ):
        turnos = [self._turno_base()]
        mock_create_app.return_value = (MagicMock(), {"hilo-1": turnos})
        mock_evaluar.return_value = (
            {"tono_apropiado_whatsapp": True},  # bool pelado, no un dict
            ["tono_apropiado_whatsapp"],
        )

        from bot.simulator.runner import correr_escenario
        resultado = correr_escenario({"persona": "x", "objetivo": "y", "criterios": [], "max_turns": 5})

        self.assertFalse(resultado["paso"])
        self.assertEqual(len(resultado["fallos_de_juez"]), 1)


class ConstruirTranscriptTest(SimpleTestCase):
    """El transcript debe incluir no solo el dialogo Cliente/Bot sino
    tambien las señales internas que los propios criterios del juez
    referencian: si se envio una imagen, que acciones de negocio se
    ejecutaron y si tuvieron exito, y el flow_data acumulado al cierre.
    Sin esto, criterios sobre imagenes/crear_lead son inverificables para
    el juez porque nunca ve esos datos (Hallazgo 3 de la revision final del
    plan original del simulador -- este task migra esa misma cobertura a la
    forma nueva de `turno["acciones"]`)."""

    def _turno(self, **overrides):
        turno = {
            "cliente_dice": "quiero un test drive", "bot_responde": "listo, ya quedo registrado",
            "acciones": [], "modelo_imagen": None, "imagen_enviada": False, "flow_data": {},
        }
        turno.update(overrides)
        return turno

    def test_anota_cada_accion_ejecutada_en_el_turno(self):
        from bot.simulator.runner import _construir_transcript

        turnos = [self._turno(acciones=[
            {"nombre": "crear_lead", "params": {}, "resultado": {"ok": True, "lead_id": 42}},
        ])]

        transcript = _construir_transcript(turnos)

        self.assertIn("accion: crear_lead -> ok=True", transcript)

    def test_anota_varias_acciones_del_mismo_turno_si_se_encadenaron(self):
        from bot.simulator.runner import _construir_transcript

        turnos = [self._turno(acciones=[
            {"nombre": "buscar_reserva", "params": {"codigo": "ABC123"}, "resultado": {"ok": True}},
            {"nombre": "reagendar_hora", "params": {"codigo": "ABC123"}, "resultado": {"ok": True}},
        ])]

        transcript = _construir_transcript(turnos)

        self.assertIn("accion: buscar_reserva -> ok=True", transcript)
        self.assertIn("accion: reagendar_hora -> ok=True", transcript)

    def test_no_anota_nada_si_no_hubo_ninguna_accion(self):
        from bot.simulator.runner import _construir_transcript

        turnos = [self._turno()]

        transcript = _construir_transcript(turnos)

        self.assertNotIn("accion:", transcript)


class ClientePareceSatisfechoTest(SimpleTestCase):
    def test_no_corta_antes_del_minimo_de_turnos(self):
        from bot.simulator.runner import _cliente_parece_satisfecho

        trayectoria = [{"role": "user", "content": "eso es todo, gracias"}]
        self.assertFalse(_cliente_parece_satisfecho(trayectoria, turn_counter=1))

    def test_no_corta_si_el_cliente_no_dijo_nada_de_cierre(self):
        from bot.simulator.runner import _cliente_parece_satisfecho

        trayectoria = [
            {"role": "user", "content": "cuanto cuesta el arkana"},
            {"role": "assistant", "content": "cuesta 24 millones"},
        ]
        self.assertFalse(_cliente_parece_satisfecho(trayectoria, turn_counter=3))

    def test_no_corta_por_nos_vemos_ni_chao_dichos_a_mitad_de_un_agendamiento(self):
        # Re-revision final, Item 2: "nos vemos"/"chao" se sacaron
        # deliberadamente de la lista de frases de cierre -- un cliente
        # agendando un test drive puede decir "perfecto, nos vemos el
        # jueves" ANTES de llegar al intercambio de RUT/telefono que el
        # escenario agendar-test-drive-y-crear-lead necesita verificar, y
        # cortar ahi arruinaria ese chequeo.
        from bot.simulator.runner import _cliente_parece_satisfecho

        for frase in ("perfecto, nos vemos el jueves entonces", "dale, chao"):
            with self.subTest(frase=frase):
                trayectoria = [
                    {"role": "user", "content": "quiero agendar un test drive"},
                    {"role": "assistant", "content": "listo, para cuando le acomoda"},
                    {"role": "user", "content": frase},
                ]
                self.assertFalse(_cliente_parece_satisfecho(trayectoria, turn_counter=3))

    def test_corta_si_el_ultimo_mensaje_del_cliente_senala_cierre(self):
        from bot.simulator.runner import _cliente_parece_satisfecho

        trayectoria = [
            {"role": "user", "content": "cuanto cuesta el arkana"},
            {"role": "assistant", "content": "cuesta 24 millones"},
            {"role": "user", "content": "perfecto, muchas gracias, eso era todo"},
            {"role": "assistant", "content": "de nada, que este bien"},
        ]
        self.assertTrue(_cliente_parece_satisfecho(trayectoria, turn_counter=2))

    def test_ignora_frases_de_cierre_que_vienen_del_bot_no_del_cliente(self):
        from bot.simulator.runner import _cliente_parece_satisfecho

        trayectoria = [
            {"role": "user", "content": "cuanto cuesta el arkana"},
            {"role": "assistant", "content": "hasta luego, que tenga buen dia"},
        ]
        self.assertFalse(_cliente_parece_satisfecho(trayectoria, turn_counter=3))


import importlib

from django.apps import apps as django_apps
from django.test import TestCase, TransactionTestCase

from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, ResultadoDeEscenario

# Modulo de migracion (nombre invalido como identificador Python por el
# prefijo numerico -- se carga con importlib, igual que Django lo hace
# internamente) -- reusa la MISMA lista ESCENARIOS_SEMILLA que usa la
# migracion real, en vez de duplicarla a mano en este archivo de test (ver
# EjecutarCorridaTest._fixture_teardown mas abajo).
_seed_escenarios = importlib.import_module(
    "bot.simulator.migrations.0002_seed_escenarios",
).seed_escenarios


class IniciarCorridaTest(TestCase):
    def test_escenario_inexistente_lanza_value_error_sin_crear_corrida(self):
        from bot.simulator.runner import iniciar_corrida

        with self.assertRaises(ValueError):
            iniciar_corrida(nombre_escenario="no-existe-este-escenario")
        self.assertEqual(CorridaDePrueba.objects.count(), 0)

    def test_sin_escenarios_activos_lanza_value_error_sin_crear_corrida(self):
        from bot.simulator.runner import iniciar_corrida

        # La migracion 0002_seed_escenarios (ver
        # bot/tests/test_simulator_seed_migration.py) siembra 8 escenarios
        # activos en TODA BD de test -- se desactivan aca para que este test
        # pruebe genuinamente el caso "cero escenarios activos", no un
        # artefacto de la semilla.
        EscenarioDePrueba.objects.update(activo=False)
        EscenarioDePrueba.objects.create(
            nombre="e1", persona="x", objetivo="y", criterios=["z"], activo=False,
        )
        with self.assertRaises(ValueError):
            iniciar_corrida()
        self.assertEqual(CorridaDePrueba.objects.count(), 0)

    def test_crea_la_corrida_con_disparada_por_y_filtro(self):
        from bot.simulator.runner import iniciar_corrida

        EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])
        corrida = iniciar_corrida(nombre_escenario="e1", disparada_por="admin@test.com")

        self.assertEqual(corrida.estado, "corriendo")
        self.assertEqual(corrida.disparada_por, "admin@test.com")
        self.assertEqual(corrida.nombre_escenario_filtro, "e1")


class EjecutarCorridaTest(TransactionTestCase):
    """TransactionTestCase, no TestCase: ejecutar_corrida despacha trabajo
    real (incluida la escritura de ResultadoDeEscenario) a un thread de
    verdad via asyncio.to_thread (ver runner.py::_ejecutar_corrida_async).
    TestCase envuelve cada test en una transaccion abierta sobre la
    conexion del thread principal -- en SQLite eso deja un lock de
    escritura tomado por iniciar_corrida() que nunca se libera durante el
    test, y cualquier escritura desde el connection aparte del thread de
    asyncio.to_thread revienta con "database table is locked" (limitacion
    documentada de Django: codigo que abre threads propios y toca la BD
    necesita TransactionTestCase).

    serialized_rollback=True (la forma "estandar" de sobrevivir el
    flush() automatico de TransactionTestCase entre tests) se probo
    primero pero choca con django_content_type al correr la suite
    bot.tests completa: la snapshot global que usa para restaurar datos
    ya no coincide exactamente con el estado real de la BD para ese punto
    de la corrida (otros tests ya alteraron content types), y
    deserialize_db_from_string revienta con IntegrityError. En su lugar,
    _fixture_teardown() de mas abajo deja que Django haga su flush()
    normal (que SI recrea content types/permisos via la señal
    post_migrate, sin pisar nada) y solo reinserta la semilla propia de
    este dominio (los 8 EscenarioDePrueba de la migracion
    0002_seed_escenarios) despues -- sin la cual test_simulator_seed_migration.py
    encontraria la tabla vacia para el resto de la sesion de test."""

    def _fixture_teardown(self):
        super()._fixture_teardown()
        _seed_escenarios(django_apps, None)

    def setUp(self):
        # Mismo motivo que en IniciarCorridaTest: neutraliza los 8
        # escenarios semilla para que "cada escenario activo" signifique
        # solo e1/e2 en estos tests.
        EscenarioDePrueba.objects.update(activo=False)
        self.e1 = EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])
        self.e2 = EscenarioDePrueba.objects.create(nombre="e2", persona="a", objetivo="b", criterios=["c"])

    @patch("bot.simulator.runner.call_command")
    @patch("bot.simulator.runner.correr_escenario")
    def test_corre_cada_escenario_activo_y_guarda_su_resultado(self, mock_correr, mock_cleanup):
        mock_correr.return_value = {
            "paso": True, "num_turnos": 1, "fallos_de_codigo": [], "fallos_de_juez": [], "juez": {}, "turnos": [],
        }
        from bot.simulator.runner import ejecutar_corrida, iniciar_corrida

        corrida = iniciar_corrida()
        ejecutar_corrida(corrida)
        corrida.refresh_from_db()

        self.assertEqual(corrida.estado, "completa")
        self.assertIsNotNone(corrida.fecha_fin)
        self.assertEqual(ResultadoDeEscenario.objects.filter(corrida=corrida).count(), 2)
        self.assertEqual(mock_correr.call_count, 2)
        mock_cleanup.assert_called_once_with("cleanup_test_conversations")

    @patch("bot.simulator.runner.call_command")
    @patch("bot.simulator.runner.correr_escenario")
    def test_respeta_el_filtro_de_un_solo_escenario(self, mock_correr, mock_cleanup):
        mock_correr.return_value = {
            "paso": True, "num_turnos": 1, "fallos_de_codigo": [], "fallos_de_juez": [], "juez": {}, "turnos": [],
        }
        from bot.simulator.runner import ejecutar_corrida, iniciar_corrida

        corrida = iniciar_corrida(nombre_escenario="e2")
        ejecutar_corrida(corrida)

        self.assertEqual(mock_correr.call_count, 1)
        resultado = ResultadoDeEscenario.objects.get(corrida=corrida)
        self.assertEqual(resultado.escenario, self.e2)

    @patch("bot.simulator.runner.call_command")
    @patch("bot.simulator.runner.correr_escenario")
    def test_persiste_paso_y_fallos_combinados(self, mock_correr, mock_cleanup):
        mock_correr.return_value = {
            "paso": False, "num_turnos": 3, "fallos_de_codigo": ["algo_fallo: detalle"],
            "fallos_de_juez": ["criterio_x: no cumplio"], "juez": {}, "turnos": [],
        }
        from bot.simulator.runner import ejecutar_corrida, iniciar_corrida

        corrida = iniciar_corrida(nombre_escenario="e1")
        ejecutar_corrida(corrida)

        resultado = ResultadoDeEscenario.objects.get(corrida=corrida)
        self.assertFalse(resultado.paso)
        self.assertEqual(resultado.fallos, ["algo_fallo: detalle", "criterio_x: no cumplio"])

    @patch("bot.simulator.runner.call_command")
    @patch("bot.simulator.runner.correr_escenario")
    def test_un_escenario_con_excepcion_no_controlada_no_interrumpe_el_resto(self, mock_correr, mock_cleanup):
        mock_correr.side_effect = [RuntimeError("boom"), {
            "paso": True, "num_turnos": 1, "fallos_de_codigo": [], "fallos_de_juez": [], "juez": {}, "turnos": [],
        }]
        from bot.simulator.runner import ejecutar_corrida, iniciar_corrida

        corrida = iniciar_corrida()
        ejecutar_corrida(corrida)
        corrida.refresh_from_db()

        self.assertEqual(corrida.estado, "completa")
        self.assertEqual(ResultadoDeEscenario.objects.filter(corrida=corrida).count(), 2)
        con_error = ResultadoDeEscenario.objects.get(escenario__nombre="e1")
        self.assertFalse(con_error.paso)
        self.assertIn("boom", con_error.error)

    @patch("bot.simulator.runner.call_command")
    def test_siempre_corre_la_limpieza_de_datos_de_test_al_final(self, mock_cleanup):
        from bot.simulator.runner import ejecutar_corrida, iniciar_corrida

        corrida = iniciar_corrida(nombre_escenario="e1")
        with patch("bot.simulator.runner.correr_escenario") as mock_correr:
            mock_correr.return_value = {
                "paso": True, "num_turnos": 1, "fallos_de_codigo": [], "fallos_de_juez": [], "juez": {}, "turnos": [],
            }
            ejecutar_corrida(corrida)

        mock_cleanup.assert_called_once_with("cleanup_test_conversations")
