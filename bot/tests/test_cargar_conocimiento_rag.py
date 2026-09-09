"""Cobertura del comando que mete los documentos de conocimiento al pipeline.

Existe porque el hueco que este comando cierra era invisible: los .md de
`bot/fixtures/rag/` estaban escritos, `reindexar_conocimiento_rag` corria sin
error, y el RAG quedaba vacio igual -- ese comando indexa ScrapedPage, y nada
creaba las ScrapedPage de esos archivos (hallazgo 2026-09-02, docs/PENDIENTES.md).
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from bot.management.commands.cargar_conocimiento_rag import DIRECTORIO_DEFAULT, URL_FUENTE
from bot.models import ScrapedPage, ScrapeRun, ScrapingSource


class CargarConocimientoRagTest(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.directorio = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.directorio / "02_financiamiento.md").write_text(
            "# Financiamiento\nPie minimo referencial 20%.", encoding="utf-8")
        (self.directorio / "06_faq.md").write_text(
            "# FAQ\nAceptamos parte de pago.", encoding="utf-8")

    def cargar(self, **kwargs):
        # cliente=CLIENTE_ACTIVO y no "cavem" fijo: los modelos con manager
        # filtrado quedan invisibles si el fixture estampa otro cliente, y la
        # suite corre como renault (ver CLAUDE.md).
        opciones = {"cliente": settings.CLIENTE_ACTIVO, "directorio": str(self.directorio)}
        opciones.update(kwargs)
        call_command("cargar_conocimiento_rag", **opciones)

    def test_crea_una_pagina_de_documento_por_archivo(self):
        self.cargar()

        paginas = list(ScrapedPage.objects.order_by("url"))
        self.assertEqual([p.url for p in paginas], [
            f"{URL_FUENTE}/02_financiamiento.md", f"{URL_FUENTE}/06_faq.md",
        ])
        self.assertIn("Pie minimo referencial 20%.", paginas[0].texto)
        for pagina in paginas:
            # es_documento=True + secciones=[] es lo que enruta el indexado por
            # _hechos_de_documento en vez de partir por largo (bot/rag/indexador.py:150).
            self.assertTrue(pagina.es_documento)
            self.assertEqual(pagina.secciones, [])

    def test_las_paginas_quedan_visibles_para_el_filtro_del_reindexado(self):
        self.cargar()

        # Exactamente la query de reindexar_conocimiento_rag: si la source no
        # queda con el cliente correcto, el reindexado no ve nada y no falla.
        encontradas = ScrapedPage.objects.filter(run__source__cliente=settings.CLIENTE_ACTIVO)
        self.assertEqual(encontradas.count(), 2)

    def test_correrlo_dos_veces_no_duplica_y_refresca_el_texto(self):
        self.cargar()
        (self.directorio / "06_faq.md").write_text("# FAQ\nTexto nuevo.", encoding="utf-8")

        self.cargar()

        self.assertEqual(ScrapedPage.objects.count(), 2)
        self.assertEqual(ScrapingSource.todos_los_clientes.count(), 1)
        self.assertEqual(ScrapeRun.objects.count(), 1)
        refrescada = ScrapedPage.objects.get(url=f"{URL_FUENTE}/06_faq.md")
        self.assertEqual(refrescada.texto, "# FAQ\nTexto nuevo.")

    def test_borra_la_pagina_de_un_archivo_que_ya_no_existe(self):
        self.cargar()
        (self.directorio / "06_faq.md").unlink()

        self.cargar()

        # Sin este borrado, el conocimiento de un documento eliminado del repo
        # seguiria vigente en Supabase (la pagina viva lo mantiene indexado).
        self.assertEqual(
            [p.url for p in ScrapedPage.objects.all()], [f"{URL_FUENTE}/02_financiamiento.md"])

    def test_registra_el_run_como_ok_con_las_paginas_procesadas(self):
        self.cargar()

        run = ScrapeRun.objects.get()
        self.assertEqual(run.estado, "ok")
        self.assertEqual(run.paginas_procesadas, 2)

    def test_falla_si_el_directorio_no_tiene_documentos(self):
        with TemporaryDirectory() as vacio:
            with self.assertRaises(CommandError):
                self.cargar(directorio=vacio)

    def test_falla_si_el_directorio_no_existe(self):
        with self.assertRaises(CommandError):
            self.cargar(directorio=str(self.directorio / "no_existe"))

    def test_los_documentos_reales_del_repo_se_cargan(self):
        # Guard contra el caso que motivo el comando: que los .md existan pero
        # no lleguen nunca al pipeline. Si alguien renombra el directorio o
        # borra las bases, esto se cae.
        #
        # Cuenta contra los archivos .md reales del directorio, no un numero
        # fijo: un numero fijo se vuelve a romper la proxima vez que alguien
        # agregue o saque un documento (paso exactamente por eso de 5 a 7
        # documentos en 2026-09-09). Y exige ademas que el resultado sea mayor
        # que cero -- sin ese segundo assert, el test pasaria igual con el
        # directorio vacio, que es precisamente el modo de falla mas caro de
        # esta parte: el RAG queda sin nada y el bot arranca perfecto
        # contestando cualquier cosa (hallazgo 2026-09-02).
        esperadas = len(list(DIRECTORIO_DEFAULT.glob("*.md")))
        self.assertGreater(esperadas, 0, f"no hay documentos .md en {DIRECTORIO_DEFAULT}")

        call_command("cargar_conocimiento_rag", cliente=settings.CLIENTE_ACTIVO)

        paginas = ScrapedPage.objects.filter(run__source__cliente=settings.CLIENTE_ACTIVO)
        self.assertGreater(paginas.count(), 0)
        self.assertEqual(paginas.count(), esperadas)
        self.assertTrue(all(p.es_documento and p.texto.strip() for p in paginas))
