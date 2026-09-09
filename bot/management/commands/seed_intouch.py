"""Semilla del catálogo de InTouch: soluciones, modelos de operación y prompts.

El contenido sale del prompt de origen §2 (documento aprobado por el usuario);
no se inventa ninguna capacidad, y las que el prompt marca como sujetas a
evaluación técnica quedan con `requiere_evaluacion_tecnica=True`.

Idempotente por `update_or_create`: se puede correr en cada deploy.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from bot.models import ModeloOperacion, SolucionInTouch

MODELOS_OPERACION = [
    {
        "slug": "humano",
        "nombre": "Humano",
        "descripcion": "Agentes especializados que atienden la interacción completa.",
        "cuando_aplica": "Interacciones complejas, sensibles o de alto valor, "
                         "donde el criterio y la empatía de una persona son el servicio.",
        "orden": 1,
    },
    {
        "slug": "hibrido",
        "nombre": "Híbrido",
        "descripcion": "Agentes humanos e IA combinados según el proceso.",
        "cuando_aplica": "Operaciones donde una parte del flujo es estructurada y "
                         "otra necesita intervención humana; el reparto se define por proceso.",
        "orden": 2,
    },
    {
        "slug": "automatizado",
        "nombre": "Automatizado",
        "descripcion": "Agentes conversacionales que atienden sin intervención humana, "
                       "con mecanismos de escalamiento que se definen en el proyecto.",
        "cuando_aplica": "Procesos estructurados, consultas frecuentes y atención de alto volumen.",
        "orden": 3,
    },
]

SOLUCIONES = [
    {
        "slug": "operacion-a-medida",
        "nombre": "Diseño de una operación a medida",
        "categoria": "operacion",
        "descripcion": "Diseño de la operación de atención o contacto según el proceso, "
                       "el volumen y los canales de cada empresa, integrando personas, "
                       "IA, datos y automatización.",
        "canales": [],
        "modelos_operacion": ["humano", "hibrido", "automatizado"],
        "ejemplos_uso": "Una empresa que hoy atiende por varios canales sin un modelo "
                        "definido y necesita ordenar la operación antes de automatizar.",
        "orden": 1,
    },
    {
        "slug": "agentes-conversacionales",
        "nombre": "Agentes conversacionales con IA",
        "categoria": "agentes_ia",
        "descripcion": "Agentes que conversan con los clientes en WhatsApp, voz, chat y "
                       "correo electrónico, para procesos estructurados y atención de alto volumen.",
        "canales": ["whatsapp", "voz", "chat", "correo"],
        "modelos_operacion": ["automatizado", "hibrido"],
        "ejemplos_uso": "Atención de consultas frecuentes y toma de datos en WhatsApp, "
                        "con derivación a un agente humano cuando el caso lo requiere.",
        "orden": 2,
    },
    {
        "slug": "contact-center",
        "nombre": "Operación de Contact Center",
        "categoria": "operacion",
        "descripcion": "Operación de Contact Center con agentes especializados, "
                       "supervisión y control de calidad.",
        "canales": ["voz", "whatsapp", "chat", "correo"],
        "modelos_operacion": ["humano", "hibrido"],
        "ejemplos_uso": "Una empresa que necesita externalizar total o parcialmente "
                        "su atención, o complementar la operación que ya tiene.",
        "orden": 3,
    },
    {
        "slug": "paneles-y-dashboards",
        "nombre": "Paneles, supervisión y dashboards",
        "categoria": "analitica",
        "descripcion": "Paneles de supervisión en tiempo real, dashboards de gestión "
                       "y reportería en Power BI.",
        "canales": [],
        "modelos_operacion": ["humano", "hibrido", "automatizado"],
        "ejemplos_uso": "Un área que necesita ver la operación en vivo y medir su "
                        "gestión con indicadores propios.",
        "orden": 4,
    },
    {
        "slug": "analitica-conversacional",
        "nombre": "Analítica conversacional y control de calidad",
        "categoria": "analitica",
        "descripcion": "Análisis de las conversaciones de la operación y control de "
                       "calidad sobre lo que efectivamente se le dijo al cliente.",
        "canales": ["voz", "whatsapp", "chat", "correo"],
        "modelos_operacion": ["humano", "hibrido", "automatizado"],
        "ejemplos_uso": "Una operación que ya funciona y necesita saber qué está "
                        "pasando dentro de sus conversaciones.",
        "orden": 5,
    },
    {
        "slug": "integraciones",
        "nombre": "Integraciones con CRM y ERP",
        "categoria": "integracion",
        "descripcion": "Integración de la operación con el CRM o el ERP de la empresa, "
                       "para que los datos de la atención vivan donde el negocio los usa.",
        "canales": [],
        "modelos_operacion": ["humano", "hibrido", "automatizado"],
        # El prompt §2 es explícito: las integraciones van "sujetas a evaluación
        # técnica". El bot lo lee de acá, no se lo tiene que acordar.
        "requiere_evaluacion_tecnica": True,
        "ejemplos_uso": "Registrar automáticamente en el CRM del cliente las "
                        "oportunidades que se generan en la conversación.",
        "orden": 6,
    },
]


class Command(BaseCommand):
    help = "Siembra el catálogo de soluciones y los modelos de operación de InTouch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--republicar-prompt", action="store_true",
            help="Publica además los prompts desde los fixtures de git. "
                 "NO se corre sin visto bueno explícito del usuario: el prompt "
                 "activo es estado de producción.",
        )

    def handle(self, *args, **opciones):
        cliente = settings.CLIENTE_ACTIVO
        for datos in MODELOS_OPERACION:
            ModeloOperacion.todos_los_clientes.update_or_create(
                cliente=cliente, slug=datos["slug"],
                defaults={k: v for k, v in datos.items() if k != "slug"},
            )
        for datos in SOLUCIONES:
            SolucionInTouch.todos_los_clientes.update_or_create(
                cliente=cliente, slug=datos["slug"],
                defaults={k: v for k, v in datos.items() if k != "slug"},
            )
        self.stdout.write(self.style.SUCCESS(
            f"{len(MODELOS_OPERACION)} modelos de operación y {len(SOLUCIONES)} "
            f"soluciones sembradas para '{cliente}'."
        ))
        if opciones["republicar_prompt"]:
            self._republicar_prompts()

    def _republicar_prompts(self):
        """Publica los prompts desde git a PromptVersion.

        Existe porque el prompt activo vive en BD y se edita desde el panel: es
        estado de producción fuera de git (biblia §VI.2). Este comando es la
        única vía de reconciliación, y por eso está detrás de un flag.
        """
        from pathlib import Path

        from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT
        from bot.models import save_prompt_version

        save_prompt_version(GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT)
        fixture = Path(__file__).resolve().parents[2] / "fixtures" / "prompt_comercial.md"
        save_prompt_version("comercial", fixture.read_text(encoding="utf-8"))
        self.stdout.write(self.style.WARNING(
            "prompts republicados desde git: verificar con `doctor --seccion prompts`"
        ))
