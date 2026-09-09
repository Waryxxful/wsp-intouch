from unittest.mock import MagicMock, patch

import httpx
from django.test import TestCase


class GetMediaUrlTest(TestCase):
    @patch("bot.whatsapp.media.httpx.get")
    def test_get_media_url_ok(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://lookaside.fbsbx.com/whatsapp_media/abc"}
        mock_get.return_value = mock_resp
        from bot.whatsapp.media import get_media_url
        self.assertEqual(get_media_url("media123"), "https://lookaside.fbsbx.com/whatsapp_media/abc")

    @patch("bot.whatsapp.media.httpx.get")
    def test_get_media_url_error_devuelve_none(self, mock_get):
        mock_get.side_effect = httpx.HTTPError("boom")
        from bot.whatsapp.media import get_media_url
        self.assertIsNone(get_media_url("media123"))

    @patch("bot.whatsapp.media.httpx.get")
    def test_get_media_url_respuesta_sin_campo_url_devuelve_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"algo_distinto": "x"}
        mock_get.return_value = mock_resp
        from bot.whatsapp.media import get_media_url
        self.assertIsNone(get_media_url("media123"))


class DownloadMediaTest(TestCase):
    @patch("bot.whatsapp.media.get_media_url")
    @patch("bot.whatsapp.media.httpx.get")
    def test_download_media_ok(self, mock_get, mock_get_url):
        mock_get_url.return_value = "https://lookaside.fbsbx.com/whatsapp_media/abc"
        mock_resp = MagicMock()
        mock_resp.content = b"contenido-binario"
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_get.return_value = mock_resp
        from bot.whatsapp.media import download_media
        contenido, mime_type = download_media("media123")
        self.assertEqual(contenido, b"contenido-binario")
        self.assertEqual(mime_type, "image/jpeg")

    @patch("bot.whatsapp.media.get_media_url")
    def test_download_media_sin_url_devuelve_none_none(self, mock_get_url):
        mock_get_url.return_value = None
        from bot.whatsapp.media import download_media
        self.assertEqual(download_media("media123"), (None, None))

    @patch("bot.whatsapp.media.get_media_url")
    @patch("bot.whatsapp.media.httpx.get")
    def test_download_media_error_en_descarga_devuelve_none_none(self, mock_get, mock_get_url):
        mock_get_url.return_value = "https://lookaside.fbsbx.com/whatsapp_media/abc"
        mock_get.side_effect = httpx.HTTPError("boom")
        from bot.whatsapp.media import download_media
        self.assertEqual(download_media("media123"), (None, None))
