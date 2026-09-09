"""Un escape inválido no debe tirar abajo toda la respuesta del LLM.

Caso real (conversación de Quintin, 2026-09-03, docs/PENDIENTES.md #24): el
contacto dijo su comuna y el bot le respondió *"Disculpa, tuve un problema para
responderte"*. El LLM había generado un JSON **perfecto salvo por una barra
invertida suelta**:

    {"mensaje": "...abierta este sabado de 09:00 a 13:00\\)...", "stage": "agenda"}

`\\)` no es un escape válido en JSON, así que `json.loads` rechazaba el objeto
entero. Los 3 intentos produjeron el mismo carácter y la respuesta —que era
correcta y útil— se descartó completa.
"""
from django.test import SimpleTestCase

from bot.flow.graph import _parse_json_response


class EscapeInvalidoTest(SimpleTestCase):
    CASO_REAL = (
        '{"mensaje": "¡Anotado! Le queda mas cerca Cavem La Reina (Av. Bilbao 1234), '
        'abierta este sabado de 09:00 a 13:00\\).", "handoff": true, "stage": "agenda"}'
    )

    def test_rescata_el_caso_real(self):
        parseado = _parse_json_response(self.CASO_REAL)
        self.assertTrue(parseado)
        self.assertIn("Cavem La Reina", parseado["mensaje"])
        # Los demás campos del contrato tienen que sobrevivir: son los que el
        # grafo lee para el handoff y el stage.
        self.assertTrue(parseado["handoff"])
        self.assertEqual(parseado["stage"], "agenda")

    def test_quita_la_barra_pero_deja_el_caracter(self):
        # "13:00\)" -> "13:00)": se va la barra, no el paréntesis.
        parseado = _parse_json_response(self.CASO_REAL)
        self.assertIn("13:00).", parseado["mensaje"])
        self.assertNotIn("\\)", parseado["mensaje"])

    def test_no_toca_los_escapes_validos(self):
        # \n, \t, \" y \\ son válidos y tienen que seguir funcionando.
        parseado = _parse_json_response('{"mensaje": "linea1\\nlinea2\\ttab \\"cita\\""}')
        self.assertEqual(parseado["mensaje"], 'linea1\nlinea2\ttab "cita"')

    def test_un_json_normal_no_cambia(self):
        self.assertEqual(_parse_json_response('{"mensaje": "hola"}'), {"mensaje": "hola"})

    def test_texto_que_no_es_json_sigue_devolviendo_vacio(self):
        # El rescate no puede convertir cualquier cosa en un dict.
        self.assertEqual(_parse_json_response("esto no es json"), {})
        self.assertEqual(_parse_json_response(""), {})

    def test_json_valido_que_no_es_objeto_sigue_rechazado(self):
        # Un string o una lista no sirven: los llamadores hacen .get().
        self.assertEqual(_parse_json_response('"solo un string"'), {})
        self.assertEqual(_parse_json_response("[1, 2]"), {})

    def test_rescata_tambien_con_texto_suelto_alrededor(self):
        # Combina los dos rescates: texto antes del "{" y escape inválido.
        crudo = 'Dejame ver... {"mensaje": "abierto hasta las 13:00\\)"}'
        self.assertIn("13:00)", _parse_json_response(crudo)["mensaje"])
