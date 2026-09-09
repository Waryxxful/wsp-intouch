"""Command one-shot para probar la Graph API de Meta.

Sirve para verificar:
  - WHATSAPP_BUSINESS_ACCOUNT_ID esta seteado.
  - El token tiene los scopes correctos (whatsapp_business_management).
  - La estructura del JSON que Meta devuelve, para armar el mapping al front.

Uso:
    python manage.py test_meta_stats --start=2026-08-01 --end=2026-08-19
    python manage.py test_meta_stats --start=2026-08-01 --end=2026-08-19 --templates
"""
import json
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Prueba la Graph API de Meta (conversation + pricing + template analytics)."

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True, help="Fecha inicio YYYY-MM-DD.")
        parser.add_argument("--end", required=True, help="Fecha fin YYYY-MM-DD.")
        parser.add_argument(
            "--templates", action="store_true",
            help="Trae tambien template_analytics (por template).",
        )
        parser.add_argument(
            "--no-cache", action="store_true",
            help="Limpia el cache antes de consultar.",
        )

    def handle(self, *args, **options):
        from bot.whatsapp.meta_stats import (
            get_message_stats, get_template_stats, date_to_ts, clear_cache,
        )
        from django.conf import settings

        waba = settings.WHATSAPP_BUSINESS_ACCOUNT_ID
        if not waba:
            self.stderr.write("[test-meta] WHATSAPP_BUSINESS_ACCOUNT_ID vacio — abortando.")
            return

        self.stdout.write(f"[test-meta] WABA: {waba}")
        self.stdout.write(f"[test-meta] Token: {settings.WHATSAPP_TOKEN[:20]}...")

        if options["no_cache"]:
            clear_cache()

        start_ts = date_to_ts(options["start"])
        end_ts = date_to_ts(options["end"])
        self.stdout.write(
            f"[test-meta] Rango: {options['start']} ({start_ts}) - "
            f"{options['end']} ({end_ts})"
        )
        self.stdout.write("")

        self.stdout.write("=== MESSAGE STATS ===")
        stats = get_message_stats(start_ts, end_ts)
        if stats is None:
            self.stderr.write("  fallo (ver log warning arriba)")
        else:
            # Volcar JSON compacto pero legible (indent=2 para debug).
            self.stdout.write(json.dumps(stats, indent=2, ensure_ascii=False)[:5000])

        if options["templates"]:
            self.stdout.write("")
            self.stdout.write("=== TEMPLATE STATS ===")
            tstats = get_template_stats(start_ts, end_ts)
            if tstats is None:
                self.stderr.write("  fallo (ver log warning arriba)")
            else:
                self.stdout.write(json.dumps(tstats, indent=2, ensure_ascii=False)[:5000])
