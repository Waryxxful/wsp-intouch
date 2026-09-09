import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from bot.whatsapp.media_storage import guardar_media_privado


class GuardarMediaPrivadoTest(TestCase):
    def test_guarda_imagen_jpeg_y_devuelve_nombre_relativo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                nombre = guardar_media_privado(b"fake jpeg bytes", "image/jpeg", "image")
            self.assertIsNotNone(nombre)
            self.assertTrue(nombre.endswith(".jpg"))
            self.assertEqual((Path(tmp) / nombre).read_bytes(), b"fake jpeg bytes")

    def test_guarda_audio_ogg_y_devuelve_nombre_relativo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                nombre = guardar_media_privado(b"fake ogg bytes", "audio/ogg", "audio")
            self.assertIsNotNone(nombre)
            self.assertTrue(nombre.endswith(".ogg"))
            self.assertEqual((Path(tmp) / nombre).read_bytes(), b"fake ogg bytes")

    def test_mime_no_soportado_devuelve_none_sin_escribir_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                nombre = guardar_media_privado(b"x", "application/octet-stream", "image")
            self.assertIsNone(nombre)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_kind_audio_no_acepta_mime_de_imagen(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                nombre = guardar_media_privado(b"x", "image/jpeg", "audio")
            self.assertIsNone(nombre)

    def test_directorio_destino_se_crea_si_no_existe(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "no-existe-todavia" / "whatsapp"
            with override_settings(WHATSAPP_MEDIA_ROOT=str(destino)):
                nombre = guardar_media_privado(b"fake png bytes", "image/png", "image")
            self.assertIsNotNone(nombre)
            self.assertTrue((destino / nombre).is_file())

    def test_error_al_escribir_devuelve_none_en_vez_de_lanzar(self):
        # WHATSAPP_MEDIA_ROOT invalido (un archivo, no un directorio) hace que
        # mkdir/write_bytes fallen -- debe degradar a None, nunca lanzar, ya
        # que un fallo de guardado no debe impedir que la percepcion de medios
        # ya generada se siga usando.
        with tempfile.TemporaryDirectory() as tmp:
            archivo_como_root = Path(tmp) / "esto-es-un-archivo"
            archivo_como_root.write_text("no soy un directorio")
            with override_settings(WHATSAPP_MEDIA_ROOT=str(archivo_como_root)):
                nombre = guardar_media_privado(b"x", "image/jpeg", "image")
            self.assertIsNone(nombre)

    def test_nombres_generados_son_unicos_entre_llamadas(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                n1 = guardar_media_privado(b"a", "image/jpeg", "image")
                n2 = guardar_media_privado(b"b", "image/jpeg", "image")
            self.assertNotEqual(n1, n2)
