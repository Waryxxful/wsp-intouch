"""El conocimiento que se indexa.

Se testea el CONTENIDO de los .md y no sólo su existencia, porque el modo de
falla de esta parte es que el bot arranque perfecto y conteste cualquier cosa
(biblia §I.2): un .md que afirma un precio o un caso de éxito se convierte en
un chunk que el RAG le va a entregar al modelo como verdad.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

RAG = Path(__file__).resolve().parents[1] / "fixtures" / "rag"


class ExistenciaTest(SimpleTestCase):
    def test_estan_los_siete_documentos(self):
        esperados = {
            "soluciones.md", "modelos-de-operacion.md", "canales.md",
            "analitica-y-calidad.md", "integraciones.md", "datos-y-seguridad.md",
            "sobre-intouch.md",
        }
        self.assertEqual({p.name for p in RAG.glob("*.md")}, esperados)


class ContenidoProhibidoTest(SimpleTestCase):
    """Lo que el prompt §5 prohíbe afirmar no puede estar en un chunk."""

    def _todos(self):
        return [(p.name, p.read_text(encoding="utf-8")) for p in RAG.glob("*.md")]

    def test_no_hay_montos_en_pesos(self):
        for nombre, texto in self._todos():
            self.assertIsNone(re.search(r"\$\s?\d", texto), nombre)

    def test_no_hay_plazos_de_implementacion(self):
        for nombre, texto in self._todos():
            bajo = texto.lower()
            for frase in ("en 30 días", "en dos semanas", "implementación en",
                          "listo en", "puesta en marcha en"):
                self.assertNotIn(frase, bajo, f"{nombre}: {frase}")

    def test_no_hay_nombres_de_clientes_ni_casos_de_exito(self):
        for nombre, texto in self._todos():
            bajo = texto.lower()
            for frase in ("caso de éxito", "logramos un", "aumentamos un",
                          "redujimos un", "nuestros clientes incluyen"):
                self.assertNotIn(frase, bajo, f"{nombre}: {frase}")

    def test_no_hay_certificaciones(self):
        for nombre, texto in self._todos():
            bajo = texto.lower()
            for frase in ("iso 9001", "iso 27001", "certificados en", "acreditados por"):
                self.assertNotIn(frase, bajo, f"{nombre}: {frase}")


class CalidadTest(SimpleTestCase):
    def test_todos_tienen_tildes(self):
        # Los chunks son corpus que el modelo lee y cita casi literal.
        for md in RAG.glob("*.md"):
            texto = md.read_text(encoding="utf-8")
            self.assertGreater(sum(texto.count(c) for c in "áéíóúñ"), 10, md.name)

    def test_ninguno_esta_casi_vacio(self):
        for md in RAG.glob("*.md"):
            self.assertGreater(len(md.read_text(encoding="utf-8")), 600, md.name)

    def test_las_integraciones_dicen_que_van_sujetas_a_evaluacion(self):
        texto = (RAG / "integraciones.md").read_text(encoding="utf-8").lower()
        self.assertIn("evaluación técnica", texto)
