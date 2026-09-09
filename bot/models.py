import dataclasses
import json
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# Import a nivel de modulo y no diferido a proposito: bot/flow/flow_data.py no
# importa nada del proyecto (solo `re`) justamente para poder usarse desde acá
# sin ciclo. Si alguna vez necesita modelos, pasarlo a import diferido.
from bot.flow.flow_data import normalizar_flow_data

# "intouch" es el cliente de ESTE bot (CLIENTE_ACTIVO en .env.docker). Las tres
# marcas anteriores se conservan como valores válidos aunque este bot no las
# use: la suite heredada las estampa en prácticamente todos sus fixtures, y
# sacarlas de la lista deja cientos de tests en rojo de golpe -- perdiendo la
# red de seguridad justo cuando más se necesita. Mantenerlas no cuesta nada (es
# una lista de choices) y no afecta a producción, donde CLIENTE_ACTIVO=intouch
# hace que los managers filtren sólo filas de InTouch.
CLIENTE_CHOICES = [
    ("renault", "Renault"), ("astara", "Astara Retail"), ("cavem", "Cavem"),
    ("intouch", "InTouch"),
]


class _ClienteActivoManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(cliente=settings.CLIENTE_ACTIVO)


class Conversation(models.Model):
    LEAD_CLASS_CHOICES = [("HOT", "HOT"), ("WARM", "WARM"), ("COLD", "COLD")]
    STAGE_CHOICES = [
        ("nuevo", "nuevo"), ("descubrimiento", "descubrimiento"), ("calificacion", "calificacion"),
        ("cotizacion", "cotizacion"), ("simulacion", "simulacion"), ("agenda", "agenda"),
        ("handoff", "handoff"), ("seguimiento", "seguimiento"), ("reclamo", "reclamo"), ("cerrado", "cerrado"),
    ]

    wa_id = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=200, blank=True, default="")
    flow_state = models.CharField(max_length=50, default="IDLE")
    flow_data = models.JSONField(default=dict, blank=True)
    active_agent = models.CharField(max_length=50, blank=True, default="")
    rut = models.CharField(max_length=12, blank=True, default="")
    lead_class = models.CharField(max_length=10, choices=LEAD_CLASS_CHOICES, blank=True, default="")
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, blank=True, default="")
    archived = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def get_flow(self) -> dict:
        # Se normaliza tambien en la LECTURA, no solo al guardar: las
        # conversaciones que ya venian con basura acumulada (ver el ejemplo de
        # la conversacion 29 en bot/flow/flow_data.py) le siguen inyectando esa
        # basura al system prompt del especialista hasta que alguien escriba de
        # nuevo. Normalizando aca quedan limpias desde el turno siguiente, sin
        # migracion de datos.
        crudo = json.loads(self.flow_data) if isinstance(self.flow_data, str) else (self.flow_data or {})
        return normalizar_flow_data(crudo)

    def set_flow(self, data: dict):
        # Ultima red antes de persistir. Los dos merges de flow_data
        # (bot/flow/graph.py y bot/whatsapp/cola_envio.py) desembocan aca, asi
        # que este es el unico punto que ve el diccionario COMPLETO: es el que
        # puede colapsar un alias que quedo conviviendo con su nombre canonico.
        self.flow_data = normalizar_flow_data(data)

    def __str__(self):
        return self.wa_id


class Message(models.Model):
    ROLE_CHOICES = [("user", "User"), ("assistant", "Assistant"), ("system", "System"), ("human", "Human")]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    wa_msg_id = models.CharField(max_length=100, blank=True, default="")
    # Nombre de archivo relativo bajo settings.WHATSAPP_MEDIA_ROOT (imagen o
    # audio que mando el cliente), o "" si el mensaje no trae adjunto o el
    # guardado fallo -- ver bot.whatsapp.media_storage.guardar_media_privado.
    media_url = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # Bug real confirmado (revision manual/reportes de FB 2026-08-24
            # a 26): ventana de carrera check-then-act -- process_incoming_message,
            # handle_message y handle_unsupported_media (bot/whatsapp/*.py)
            # todos hacen Message.objects.filter(wa_msg_id=...).exists() y
            # solo bastante despues guardan el Message real. Si Meta
            # reintrega el mismo webhook mientras el primer turno todavia
            # esta corriendo el grafo (llamadas LLM + tools pueden tardar
            # varios segundos), ambas ejecuciones pasan el check y cada una
            # termina mandando su propia respuesta -- el sintoma real
            # reportado: mensaje de cierre repetido dos veces seguidas sin
            # mensaje del cliente entremedio. Mismo patron que Reserva:
            # UniqueConstraint condicional (wa_msg_id nunca vacio para
            # mensajes salientes del bot, que no tienen id de WhatsApp) +
            # capturar IntegrityError en el punto de guardado real (ver
            # bot.whatsapp.handlers._save_message_reservando).
            # condition=Q(wa_msg_id__gt="") en vez de ~Q(wa_msg_id="") a
            # proposito: el backend mssql-django traduce la negacion a
            # "WHERE NOT (...)", sintaxis que este SQL Server rechaza para
            # un indice filtrado (ProgrammingError 156, confirmado al
            # migrar) -- "> ''" es equivalente para un CharField (ningun
            # string no vacio es lexicograficamente menor a "") y genera
            # "WHERE ... > ''", que si acepta.
            models.UniqueConstraint(
                fields=["wa_msg_id"],
                condition=models.Q(wa_msg_id__gt=""),
                name="unico_wa_msg_id_no_vacio",
            ),
        ]


class Setting(models.Model):
    key = models.CharField(max_length=100, unique=True, db_index=True)
    value = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key


def get_setting(key: str, default: str = "") -> str:
    try:
        return Setting.objects.get(key=key).value
    except Setting.DoesNotExist:
        return default


class Incident(models.Model):
    STATUS_CHOICES = [("abierto", "Abierto"), ("revisado", "Revisado"), ("cerrado", "Cerrado")]
    TIPO_CASO_CHOICES = [
        ("mantencion", "mantencion"), ("garantia", "garantia"), ("diagnostico", "diagnostico"),
        ("reclamo", "reclamo"), ("repuesto", "repuesto"), ("campana_tecnica", "campana_tecnica"),
        ("dyp", "dyp"), ("seguro", "seguro"), ("rent_a_car", "rent_a_car"), ("otro", "otro"),
    ]
    kind = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="abierto")
    context = models.JSONField(default=dict, blank=True)
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CampaignSend(models.Model):
    contacto = models.CharField(max_length=32, db_index=True)
    campaign_type = models.CharField(max_length=50)
    template = models.CharField(max_length=100)
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="campaign_sends")
    enviado_at = models.DateTimeField(auto_now_add=True)
    respondido = models.BooleanField(default=False)

    class Meta:
        ordering = ["-enviado_at"]
        constraints = [
            # Gunicorn corre con varios workers y cada uno arranca su propio
            # thread de seguimiento (la bandera _started de bot/seguimiento.py
            # es por-proceso). Dos ticks casi simultaneos pasaban ambos el
            # chequeo "ya se le mando?" antes de que ninguno escribiera la
            # fila, y el cliente recibia el mismo mensaje de seguimiento dos
            # veces. Con esta restriccion el get_or_create se vuelve un claim
            # atomico: el segundo worker choca y se sale.
            # Solo aplica al seguimiento -- las campanas y encuestas SI pueden
            # reenviarse al mismo contacto en otra ocasion.
            models.UniqueConstraint(
                fields=["contacto", "campaign_type"],
                condition=models.Q(campaign_type="seguimiento_vehiculo"),
                name="un_solo_seguimiento_por_contacto",
            ),
        ]


class EncuestaServicioTecnico(models.Model):
    campaign_send = models.OneToOneField(
        CampaignSend, on_delete=models.CASCADE, related_name="encuesta_servicio_tecnico",
    )
    p1_satisfaccion_ejecutivo = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-10
    p2_satisfaccion_visita = models.PositiveSmallIntegerField(null=True, blank=True)     # 1-10
    p3_trabajos_correctos = models.CharField(
        max_length=10, blank=True, default="",
        choices=[("si", "Si"), ("no", "No"), ("no_se", "No se")],
    )
    p4_recomendaria = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-10 (NPS)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class EncuestaVentaAutoNuevo(models.Model):
    campaign_send = models.OneToOneField(
        CampaignSend, on_delete=models.CASCADE, related_name="encuesta_venta_auto_nuevo",
    )
    p1_nota_general = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-10
    p1_motivo_nota_baja = models.TextField(blank=True, default="")  # solo si p1 <= 6

    p2_dejo_parte_pago = models.CharField(max_length=10, blank=True, default="",
        choices=[("si", "Si"), ("no", "No")])
    p2_motivo_no = models.CharField(max_length=30, blank=True, default="", choices=[
        ("no_ofrecieron", "No le ofrecieron la opcion"),
        ("precio_tasacion", "Precio tasacion no fue el esperado"),
        ("vendio_particular", "Lo vendio particular o a otra empresa"),
        ("otro", "Otro motivo"),
    ])
    p2_motivo_otro_texto = models.TextField(blank=True, default="")  # solo si p2_motivo_no == "otro"

    p3_firmo_checklist = models.CharField(max_length=10, blank=True, default="",
        choices=[("si", "Si"), ("no", "No")])
    p3_motivo_no = models.CharField(max_length=30, blank=True, default="", choices=[
        ("no_presentaron", "No le presentaron el checklist al momento de la entrega"),
        ("no_quiso_o_sin_tiempo", "No quiso firmarlo o no tuvo tiempo"),
    ])
    p3_observaciones = models.TextField(blank=True, default="")  # libre, siempre disponible

    p4_informaron_garantia = models.CharField(max_length=10, blank=True, default="",
        choices=[("si", "Si"), ("no", "No")])
    p5_informaron_mantenciones = models.CharField(max_length=10, blank=True, default="",
        choices=[("si", "Si"), ("no", "No")])

    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class Servicio(models.Model):
    nombre = models.CharField(max_length=200)
    duracion_min = models.PositiveIntegerField(default=30)
    precio = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True)
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="renault", db_index=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    def __str__(self):
        return self.nombre


class Sucursal(models.Model):
    nombre = models.CharField(max_length=200)
    direccion = models.CharField(max_length=300, blank=True, default="")
    horario_texto = models.CharField(max_length=200, blank=True, default="Lun-Vie 9:00-18:00")
    latitud = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="renault", db_index=True)
    categorias = models.JSONField(default=list, blank=True)
    categorias_actualizado_en = models.DateTimeField(null=True, blank=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    def __str__(self):
        return self.nombre


class Reserva(models.Model):
    ESTADO_CHOICES = [("activa", "Activa"), ("cancelada", "Cancelada")]
    codigo = models.CharField(max_length=20, unique=True, db_index=True)
    contacto = models.CharField(max_length=32, db_index=True)
    cliente_nombre = models.CharField(max_length=200, blank=True, default="")
    cliente_email = models.CharField(max_length=200, blank=True, default="")
    # Datos del vehiculo que entra al taller. El docx S11 los pide visibles en
    # la plataforma ("Vehiculo. Patente.") y el S10 los pide al agendar una
    # mantencion -- sin ellos el taller no sabe que auto va a recibir ni con
    # que kilometraje, que es justo lo que determina la mantencion que
    # corresponde. Opcionales porque el mismo modelo agenda tambien servicios
    # que no son de taller (ej. una prueba de manejo).
    vehiculo = models.CharField(max_length=200, blank=True, default="")
    patente = models.CharField(max_length=12, blank=True, default="")
    vehiculo_anio = models.IntegerField(null=True, blank=True)
    vehiculo_km = models.IntegerField(null=True, blank=True)
    servicio = models.ForeignKey(Servicio, on_delete=models.PROTECT)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT)
    fecha = models.DateField()
    hora = models.TimeField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="activa")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "fecha", "hora"],
                condition=models.Q(estado="activa"),
                name="unico_cupo_activo_por_sucursal_fecha_hora",
            ),
        ]

    def __str__(self):
        return self.codigo


class ScrapingSource(models.Model):
    url = models.URLField()
    nombre = models.CharField(max_length=100, blank=True, default="")
    frecuencia_horas = models.FloatField(default=0)
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="renault", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "url"], name="unico_url_por_cliente"),
        ]

    def __str__(self):
        return self.nombre or self.url


class ScrapeRun(models.Model):
    ESTADO_CHOICES = [("corriendo", "Corriendo"), ("ok", "Ok"), ("error", "Error")]
    source = models.ForeignKey(ScrapingSource, null=True, blank=True, on_delete=models.CASCADE, related_name="runs")
    url = models.URLField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="corriendo")
    paginas_procesadas = models.IntegerField(default=0)
    error_detalle = models.TextField(blank=True, default="")
    catalogo_extraido = models.JSONField(default=dict, blank=True)
    paginas_con_error = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.url} ({self.estado})"


class ScrapedPage(models.Model):
    run = models.ForeignKey(ScrapeRun, on_delete=models.CASCADE, related_name="pages")
    # max_length generoso (default de URLField es 200): bug real 2026-08-31
    # (docs/PENDIENTES.md) -- al subir max_pages, el BFS de astararetail.cl
    # llego a un slug de blog largo (~250+ caracteres) que superaba 200 y
    # abortaba la corrida ENTERA con un error de SQL Server a mitad del loop
    # de ScrapedPage, antes de llegar siquiera a actualizar VehiculoCatalogo.
    url = models.URLField(max_length=500)
    texto = models.TextField()
    imagenes = models.JSONField(default=list, blank=True)  # [{"url": "...", "alt": "..."}]
    secciones = models.JSONField(default=list, blank=True)  # [{"titulo": str|None, "texto": str}]
    es_documento = models.BooleanField(default=False)  # True si vino de PDF/Word/Excel, no de
    # HTML -- distingue este caso del fallback de HTML con baja cobertura de secciones
    # (_COBERTURA_MINIMA_SECCIONES en crawler.py), que tambien deja secciones=[] pero NO debe
    # pasar por _hechos_de_documento (bot/rag/indexador.py).

    class Meta:
        # Ascendente por id (= orden de creacion): al reindexar, la ultima
        # ScrapedPage de una misma fuente_url procesada es la que "gana" en
        # Supabase (delete-por-fuente_url + insert en el indexador) -- este
        # orden hace que sea siempre la mas reciente, no la que la base
        # devuelva sin garantia. Ver docs/PENDIENTES.md.
        ordering = ["id"]

    def __str__(self):
        return self.url


class ImagenConvertida(models.Model):
    url_original = models.URLField(unique=True)
    archivo = models.ImageField(upload_to="model_images/")
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.url_original


class VehiculoCatalogo(models.Model):
    CONDICION_CHOICES = [("0km", "0km"), ("usado", "Usado")]

    modelo = models.CharField(max_length=100)
    version = models.CharField(max_length=150, blank=True, default="")
    # precio = precio de LISTA (el mas alto de los 3 tiers que suelen
    # publicar los sitios de concesionaria). precio_contado/precio_financiado
    # son los otros 2 tiers reales del mismo vehiculo, no versiones
    # distintas -- separados en campos propios porque _simular_financiamiento
    # calcula su propia amortizacion sobre "precio" (de lista), no sobre un
    # precio que ya trae un bono de financiamiento de la marca aplicado. Bug
    # real encontrado en la auditoria previa a la prueba de Astara del
    # 2026-09-01 (docs/PENDIENTES.md): el extractor a veces guardaba el
    # precio financiado (mas bajo) en "precio", devolviendole al cliente un
    # precio de lista incorrecto.
    precio = models.IntegerField(null=True, blank=True)
    precio_contado = models.IntegerField(null=True, blank=True)
    precio_financiado = models.IntegerField(null=True, blank=True)
    specs = models.JSONField(default=dict, blank=True)
    url_fuente = models.URLField(blank=True, default="")
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="renault", db_index=True)
    # Distingue vehiculos 0km de vehiculos usados/seminuevos scrapeados desde
    # la seccion de seminuevos del sitio (ej. astararetail.cl/seminuevos/) --
    # sin esto, un seminuevo y un 0km con el mismo nombre generico ("Compass",
    # "L200") se fusionaban en el matching difuso de bot/business/ventas.py y
    # _resolver_precio_catalogo podia devolver el precio del usado (mas bajo)
    # para una pregunta sobre el 0km. Bug real encontrado en la auditoria
    # previa a la prueba de Astara del 2026-09-01 (docs/PENDIENTES.md).
    condicion = models.CharField(max_length=10, choices=CONDICION_CHOICES, default="0km", blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["modelo", "version"]

    def __str__(self):
        return f"{self.modelo} {self.version}".strip()


class CustomSpecialist(models.Model):
    slug = models.SlugField(max_length=50)
    label = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=300)
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="renault", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "slug"], name="unico_slug_por_cliente"),
        ]

    def __str__(self):
        return self.label


class PromptVersion(models.Model):
    agente = models.CharField(max_length=100, db_index=True)
    prompt = models.TextField()
    activa = models.BooleanField(default=False)
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="renault", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.agente} ({'activa' if self.activa else 'inactiva'})"


def get_active_prompt(agente: str) -> str:
    version = PromptVersion.objects.filter(agente=agente, activa=True).first()
    return version.prompt if version else ""


def save_prompt_version(agente: str, prompt: str) -> PromptVersion:
    PromptVersion.objects.filter(agente=agente, activa=True).update(activa=False)
    return PromptVersion.objects.create(
        agente=agente, prompt=prompt, activa=True, cliente=settings.CLIENTE_ACTIVO
    )


def deactivate_prompt(agente: str) -> None:
    PromptVersion.objects.filter(agente=agente, activa=True).update(activa=False)


class OptOut(models.Model):
    """Registro de compliance (Ley 21.719): un wa_id aca no debe volver a
    recibir mensajes salientes/comerciales (ver api_enviar_recordatorio en
    admin_panel/views.py). Tabla propia y no un campo en Conversation
    porque flow_data se sobreescribe con un merge en cada turno del bot
    (ver graph.py) -- un registro de compliance necesita ser un dato
    aparte, estable y auditable."""
    wa_id = models.CharField(max_length=20, unique=True, db_index=True)
    motivo = models.TextField(blank=True, default="")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="optouts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.wa_id


def esta_optout(wa_id: str) -> bool:
    return OptOut.objects.filter(wa_id=wa_id).exists()


def registrar_optout(wa_id: str, motivo: str = "") -> OptOut:
    conversation = Conversation.objects.filter(wa_id=wa_id).first()
    optout, _ = OptOut.objects.get_or_create(
        wa_id=wa_id, defaults={"motivo": motivo or "", "conversation": conversation},
    )
    return optout


def registrar_incidente(conversation, kind: str, context: dict | None = None) -> "Incident":
    """Dedupea por conversacion + kind mientras exista un Incident
    status="abierto" -- ya no por una ventana de tiempo fija. La ventana de
    5 minutos (fix anterior) resolvia el caso extremo de reintentos casi
    inmediatos del LLM (12 incidentes en 17 minutos), pero no cubre un lead
    real evolucionando mas lento durante una conversacion larga: bug
    confirmado en produccion 2026-08-31 (docs/PENDIENTES.md, auditoria
    conversacion 14) -- 6 Incident de kind="handoff" en 52 minutos (motivos
    distintos: financiamiento, parte de pago, coordinar llamada, test
    drive x2, confirmacion final), generando 6 notificaciones separadas
    para el equipo comercial por UN solo cliente en curso. Mientras el
    Incident anterior siga abierto, cualquier registro nuevo para la misma
    conversacion+kind actualiza su contexto al motivo mas reciente en vez
    de crear una fila nueva -- solo se abre una genuinamente nueva despues
    de que el equipo lo marque revisado/cerrado (ver admin_panel, panel de
    Incidentes)."""
    if conversation is not None:
        existente = Incident.objects.filter(
            conversation=conversation, kind=kind, status="abierto",
        ).order_by("-created_at").first()
        if existente is not None:
            if context:
                existente.context = context
                existente.save(update_fields=["context"])
            return existente
    return Incident.objects.create(conversation=conversation, kind=kind, context=context or {})


def crear_caso(conversation, tipo: str, resumen: str) -> "Incident":
    """Caso/ticket real de postventa (garantia, reclamo, repuesto, DyP, etc.)
    para que un humano lo pueda listar y revisar despues -- no solo leer el
    chat de WhatsApp. Reusa Incident (kind=tipo validado) en vez de una tabla
    nueva, mismo criterio que registrar_incidente."""
    validos = {c[0] for c in Incident.TIPO_CASO_CHOICES}
    if tipo in validos:
        return Incident.objects.create(conversation=conversation, kind=tipo, context={"resumen": resumen})
    return Incident.objects.create(
        conversation=conversation, kind="otro", context={"resumen": resumen, "tipo_original": tipo},
    )


class Consentimiento(models.Model):
    """Registro de compliance (Ley 21.719): si el contacto dio o revoco
    consentimiento para uso de sus datos con fines comerciales/seguimiento.
    Tabla propia y append-only, mismo motivo que OptOut (ver comentario
    ahi arriba): necesitamos poder responder "¿tenia consentimiento el
    3 de marzo?", no solo el valor actual -- una columna que se pisa no
    puede responder eso."""
    wa_id = models.CharField(max_length=20, db_index=True)
    otorgado = models.BooleanField()
    motivo = models.TextField(blank=True, default="")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="consentimientos")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.wa_id} ({'otorgado' if self.otorgado else 'revocado'})"


def registrar_consentimiento(wa_id: str, otorgado: bool, motivo: str = "", conversation=None) -> Consentimiento:
    if conversation is None:
        conversation = Conversation.objects.filter(wa_id=wa_id).first()
    return Consentimiento.objects.create(wa_id=wa_id, otorgado=otorgado, motivo=motivo or "", conversation=conversation)


def tiene_consentimiento(wa_id: str) -> bool | None:
    ultimo = Consentimiento.objects.filter(wa_id=wa_id).order_by("-created_at").first()
    return ultimo.otorgado if ultimo else None


class VehiculoUsado(models.Model):
    """Stock de usados de Cavem, cargado desde la planilla comercial
    (bot/fixtures/stock_cavem.csv via `manage.py importar_stock_cavem`).

    Tabla propia y NO campos nuevos en VehiculoCatalogo a proposito: aquel
    modelo existe para recibir lo que escupe el scraper (por eso tiene tres
    tiers de precio y `specs` como JSON blando -- el extractor nunca sabe que
    campos va a encontrar). Esto es lo contrario: planilla controlada, 34
    campos conocidos, y sobre todo hay que FILTRAR por ellos en SQL
    (presupuesto, carroceria, anio, km), cosa que un JSON no permite. Mezclar
    los dos origenes en una tabla es justo la mezcla que produjo los bugs de
    precio de Astara del 2026-09-01 (precio financiado guardado como precio
    de lista; Outlander vs Outlander PHEV).
    """

    codigo = models.CharField(max_length=20, unique=True, db_index=True)  # US001

    # --- identificacion ---
    marca = models.CharField(max_length=60, db_index=True)
    modelo = models.CharField(max_length=80, db_index=True)
    version = models.CharField(max_length=120, blank=True, default="")

    # --- filtros numericos (columnas reales, no JSON: ver docstring) ---
    anio = models.IntegerField(db_index=True)
    km = models.IntegerField(db_index=True)
    precio_lista = models.IntegerField(db_index=True)
    precio_oferta = models.IntegerField(null=True, blank=True, db_index=True)

    # --- filtros categoricos ---
    tipo_vehiculo = models.CharField(max_length=30, db_index=True)   # SUV, Pick-up, Sedan...
    combustible = models.CharField(max_length=30, db_index=True)     # Gasolina, Diesel
    transmision = models.CharField(max_length=40)                    # "Automatica CVT", "Automatica 6AT"
    # Derivado de `transmision` en el importador. La planilla escribe la caja
    # con 10 redacciones distintas ("Automatica CVT", "Automatica 6AT",
    # "Automatica S tronic"...), asi que un filtro por igualdad nunca
    # respondería "busco un auto automatico" (docx S24). Booleano normalizado
    # para que ese filtro sea una comparacion y no un LIKE fragil.
    es_automatico = models.BooleanField(default=True, db_index=True)
    traccion = models.CharField(max_length=20, blank=True, default="")  # 4x2, 4x4, AWD

    # --- condicion ---
    color = models.CharField(max_length=60, blank=True, default="")
    n_duenos = models.IntegerField(null=True, blank=True)
    estado = models.CharField(max_length=40, blank=True, default="")
    airbags = models.IntegerField(null=True, blank=True)

    # --- operacional ---
    garantia = models.CharField(max_length=120, blank=True, default="")
    ubicacion = models.CharField(max_length=120, blank=True, default="")
    disponibilidad = models.CharField(max_length=40, blank=True, default="Disponible", db_index=True)
    fecha_ingreso = models.DateField(null=True, blank=True)
    acepta_financiamiento = models.BooleanField(default=True)
    acepta_parte_pago = models.BooleanField(default=True)

    # --- ficha tecnica (descriptivos, no se filtra por ellos) ---
    motor = models.CharField(max_length=120, blank=True, default="")
    cilindrada = models.CharField(max_length=40, blank=True, default="")
    potencia = models.CharField(max_length=60, blank=True, default="")
    torque = models.CharField(max_length=60, blank=True, default="")
    rendimiento = models.CharField(max_length=60, blank=True, default="")
    seguridad = models.TextField(blank=True, default="")
    conectividad = models.TextField(blank=True, default="")
    confort = models.TextField(blank=True, default="")
    equipamiento = models.TextField(blank=True, default="")
    observaciones = models.TextField(blank=True, default="")

    # --- enlaces ---
    link_ficha = models.URLField(max_length=300, blank=True, default="")
    link_fotos = models.URLField(max_length=300, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["precio_oferta", "marca", "modelo"]

    def __str__(self):
        return f"{self.codigo} {self.marca} {self.modelo} {self.version}".strip()

    @property
    def precio_vigente(self) -> int:
        """El precio que se le cotiza al cliente. El diccionario de campos de
        la planilla dice "priorizar precio oferta cuando corresponda" -- este
        es el unico lugar donde se decide cual de los dos manda, para que
        ninguna tool ni prompt tenga que elegir por su cuenta."""
        return self.precio_oferta or self.precio_lista


class LeadComercial(models.Model):
    """Lead del docx S8, con los 14 antecedentes que pide capturar.

    Modelo propio y NO el `Lead` de la app `leads`: aquel tiene 4 campos y
    vive en la BD `qaintouch` (el CRM real de Intouch, ver leads/db_router.py).
    Escribir datos ficticios de una demo en el CRM productivo no corresponde,
    y ademas necesitamos 14 campos, no 4. Uno por conversacion (OneToOne): el
    lead se va completando a medida que avanza el chat, no se crea uno nuevo
    por turno -- mismo criterio de dedup que registrar_incidente."""

    TEMPERATURA_CHOICES = [("HOT", "HOT"), ("WARM", "WARM"), ("COLD", "COLD")]

    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="lead_comercial")

    nombre = models.CharField(max_length=200, blank=True, default="")
    telefono = models.CharField(max_length=32, blank=True, default="")
    email = models.CharField(max_length=200, blank=True, default="")
    comuna = models.CharField(max_length=120, blank=True, default="")

    vehiculo_interes = models.CharField(max_length=200, blank=True, default="")
    vehiculo_codigo = models.CharField(
        max_length=20, blank=True, default="",
        # Lo deriva el sistema desde vehiculo_interes contra VehiculoUsado
        # (bot/business/prospeccion.py), no lo escribe el LLM: quedaba vacio y
        # el vendedor tenia que buscar el auto a mano en la planilla.
        help_text="Codigo del stock (ej. US011). Lo resuelve el sistema desde vehiculo_interes.")
    presupuesto = models.IntegerField(null=True, blank=True)
    pie_disponible = models.IntegerField(null=True, blank=True)
    cuota_objetivo = models.IntegerField(null=True, blank=True)
    plazo_compra = models.CharField(
        max_length=60, blank=True, default="",
        # El nombre de la columna es ambiguo y ya causo un dato falso: en la
        # conversacion 29 el LLM guardo aca "24 cuotas", que es el plazo del
        # CREDITO. La tool expone este campo como `cuando_compra` y valida el
        # valor antes de escribirlo (ver _validar_cuando_compra).
        help_text="CUANDO piensa comprar el cliente (ej. 'este viernes', 'en 3 semanas'). "
                  "NO es el plazo del financiamiento.")

    tiene_parte_pago = models.BooleanField(null=True, blank=True)
    vehiculo_actual = models.CharField(max_length=200, blank=True, default="")

    intencion = models.CharField(max_length=60, blank=True, default="")
    sentimiento = models.CharField(max_length=30, blank=True, default="")
    urgencia = models.CharField(max_length=30, blank=True, default="")
    temperatura = models.CharField(max_length=10, choices=TEMPERATURA_CHOICES, blank=True, default="")
    lead_score = models.IntegerField(default=0)
    proxima_accion = models.CharField(max_length=300, blank=True, default="")
    resumen = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-lead_score", "-updated_at"]

    def __str__(self):
        return f"{self.nombre or self.telefono} ({self.temperatura or 'sin clasificar'})"


# Peso de cada senal comercial en el score 0-100. Se calcula en CODIGO y no lo
# pone el LLM a ojo: en la demo alguien va a preguntar "y de donde sale ese 92",
# y un numero inventado por el modelo no se puede explicar ni reproducir. Los
# pesos reflejan que tan cerca esta el lead de una venta real: saber QUE auto
# quiere y CUANDO compra pesan mas que un dato de contacto.
_PESOS_LEAD_SCORE = {
    "vehiculo_interes": 20,   # sabe que auto quiere
    "plazo_compra": 18,       # tiene fecha de compra
    "presupuesto": 15,        # tiene presupuesto definido
    "pie_disponible": 12,     # tiene el pie listo
    "telefono": 10,           # es contactable
    "nombre": 8,
    "cuota_objetivo": 7,      # ya penso en la cuota
    "tiene_parte_pago": 5,    # trae un auto a cuenta
    "email": 3,
    "comuna": 2,
}


def calcular_lead_score(lead: "LeadComercial") -> int:
    total = 0
    for campo, peso in _PESOS_LEAD_SCORE.items():
        valor = getattr(lead, campo, None)
        # tiene_parte_pago es booleano de 3 estados: None = no se pregunto
        # todavia (no suma), False = dijo que no (igual suma: es una respuesta
        # concreta que acota la negociacion, no un dato faltante).
        if campo == "tiene_parte_pago":
            total += peso if valor is not None else 0
        elif valor not in (None, "", 0):
            total += peso
    return min(total, 100)


def clasificar_temperatura(score: int) -> str:
    """Umbrales del docx S8: HOT es "vehiculo definido + presupuesto + compra
    en 30 dias + financiamiento + acepta contacto", que con los pesos de
    arriba cae sobre 70."""
    if score >= 70:
        return "HOT"
    if score >= 40:
        return "WARM"
    return "COLD"


class VehiculoPartePago(models.Model):
    """Vehiculo que el cliente ofrece en parte de pago (docx S9). Guarda los
    antecedentes para pedir una tasacion; NUNCA un monto tasado -- el
    guardrail del docx S15 prohibe comprometer tasaciones, asi que este
    modelo deliberadamente no tiene campo de valor."""

    ESTADO_CHOICES = [
        ("solicitada", "Tasacion solicitada"),
        ("contactado", "Cliente contactado"),
        ("descartada", "Descartada"),
    ]

    conversation = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vehiculos_parte_pago")

    marca_modelo = models.CharField(max_length=200)
    anio = models.IntegerField(null=True, blank=True)
    version = models.CharField(max_length=150, blank=True, default="")
    km = models.IntegerField(null=True, blank=True)
    patente = models.CharField(max_length=12, blank=True, default="")
    estado_general = models.CharField(max_length=120, blank=True, default="")
    tiene_deuda = models.BooleanField(null=True, blank=True)
    ubicacion = models.CharField(max_length=150, blank=True, default="")
    observaciones = models.TextField(blank=True, default="")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="solicitada")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.marca_modelo} {self.anio or ''}".strip()


class Campana(models.Model):
    """Campana saliente de WhatsApp (docx S12). El envio real usa
    CampaignSend (una fila por contacto); esta tabla es la definicion.

    `campaign_type` es la llave que conecta los tres lados del sistema: la
    plantilla de Meta que se manda, las filas de CampaignSend que registran el
    envio, y la regla de PRE_ROUTING_RULES (bot/flow/campaign_rules.py) que
    decide a que especialista entra quien responde. Si no coincide en los tres
    lados, el cliente responde "QUIERO" y cae en el supervisor generico."""

    nombre = models.CharField(max_length=150)
    campaign_type = models.CharField(max_length=50, unique=True, db_index=True)
    template = models.CharField(max_length=100, blank=True, default="")
    segmento = models.CharField(max_length=200, blank=True, default="")
    objetivo = models.CharField(max_length=300, blank=True, default="")
    mensaje = models.TextField(blank=True, default="")
    palabra_clave = models.CharField(max_length=30, blank=True, default="")
    activa = models.BooleanField(default=True)
    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="cavem", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def metricas(self) -> dict:
        """Embudo de la campana a partir de datos REALES del bot.

        Entregados y leidos no salen de aca: los sabe Meta, no nosotros (ver
        admin_panel.views.api_template_stats, que los trae de la Graph API).
        Se devuelven en None en vez de inventar un numero, y el panel oculta
        la columna cuando no hay dato -- mismo criterio que las tarjetas del
        catalogo de wsp_platform."""
        envios = CampaignSend.objects.filter(campaign_type=self.campaign_type)
        contactos = envios.values("contacto").distinct().count()
        respuestas = envios.filter(respondido=True).count()
        conversaciones = Conversation.objects.filter(
            campaign_sends__campaign_type=self.campaign_type).distinct()
        leads_qs = LeadComercial.objects.filter(conversation__in=conversaciones)
        leads = leads_qs.count()
        # Se cuenta sobre LeadComercial.temperatura y no sobre
        # Conversation.lead_class: esa columna la pisa el JSON del LLM al final
        # de cada turno (ver el comentario en bot/business/prospeccion.py), asi
        # que contar HOT ahi devolvia menos conversiones de las reales.
        conversiones = leads_qs.filter(temperatura="HOT").count()
        return {
            "campaign_type": self.campaign_type,
            "nombre": self.nombre,
            "contactos": contactos,
            "enviados": envios.count(),
            "entregados": None,
            "leidos": None,
            "respuestas": respuestas,
            "leads": leads,
            "conversiones": conversiones,
        }


class SolucionInTouch(models.Model):
    """Catálogo de soluciones que el bot puede ofrecer.

    Va a tabla y no al prompt a propósito (spec §5.1): el guardrail "no
    inventes integraciones, capacidades ni certificaciones" sólo es cumplible
    si la lista sale de una fila. Es el mismo argumento por el que un precio
    no puede vivir en un chunk vectorial (biblia §III.5).

    Editable desde el panel, así que agregar una solución no necesita deploy.
    """

    CATEGORIA_CHOICES = [
        ("operacion", "Diseño de operación"),
        ("agentes_ia", "Agentes conversacionales con IA"),
        ("analitica", "Analítica y control de calidad"),
        ("integracion", "Integraciones"),
    ]

    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="intouch")
    slug = models.SlugField(max_length=60)
    nombre = models.CharField(max_length=200)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES)
    descripcion = models.TextField()
    canales = models.JSONField(default=list, blank=True)
    modelos_operacion = models.JSONField(default=list, blank=True)
    requiere_evaluacion_tecnica = models.BooleanField(
        default=False,
        help_text="El prompt exige presentarla como sujeta a evaluación técnica.")
    ejemplos_uso = models.TextField(blank=True, default="")
    activa = models.BooleanField(default=True)
    orden = models.IntegerField(default=0)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["orden", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "slug"], name="unica_solucion_por_cliente"),
        ]

    def __str__(self):
        return self.nombre


class ModeloOperacion(models.Model):
    """Los tres modelos de operación del prompt §2: humano, híbrido y
    automatizado. Conjunto cerrado, y por eso el bot los lee de una tabla en
    vez de recordarlos."""

    cliente = models.CharField(max_length=20, choices=CLIENTE_CHOICES, default="intouch")
    slug = models.SlugField(max_length=30)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField()
    cuando_aplica = models.TextField()
    orden = models.IntegerField(default=0)

    objects = _ClienteActivoManager()
    todos_los_clientes = models.Manager()

    class Meta:
        ordering = ["orden"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "slug"], name="unico_modelo_operacion_por_cliente"),
        ]

    def __str__(self):
        return self.nombre


class LeadInTouch(models.Model):
    """El lead comercial B2B, con los 21 campos del contrato del prompt §8.

    Uno por conversación (OneToOne) a propósito: el lead se va completando a
    medida que avanza el chat, no se crea uno por turno. Es la primera de las
    tres capas de idempotencia del spec §7.5, y la única que es estructural --
    la garantiza la tabla, no el código.

    El teléfono NO es un campo: llega de los metadatos de WhatsApp y vive en
    Conversation.wa_id. El prompt prohíbe pedírselo al contacto.

    `lead_score` lo escribe `calcular_lead_score` (código), nunca el LLM.
    """

    SCORE_CHOICES = [("HOT", "HOT"), ("WARM", "WARM"), ("COLD", "COLD"),
                     ("NO_CALIFICADO", "No calificado")]
    SITUACION_CC_CHOICES = [("tiene", "Tiene"), ("no_tiene", "No tiene")]
    TIPO_CC_CHOICES = [("propio", "Propio"), ("externalizado", "Externalizado"),
                       ("mixto", "Mixto"), ("no_tiene", "No tiene")]
    # Los siete valores del prompt §4. "otro" significa que el contacto indicó
    # una categoría distinta -- NO se usa para reemplazar un subtipo desconocido,
    # que se representa con la cadena vacía.
    SUBTIPO_AUTOMOTRIZ_CHOICES = [
        ("importador", "Importador"), ("concesionario", "Concesionario"),
        ("automotora", "Automotora"), ("servicio_tecnico", "Servicio Técnico"),
        ("rent_a_car", "Rent a Car"), ("financiera", "Financiera Automotriz"),
        ("otro", "Otro"),
    ]

    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="lead_intouch")

    # Identificación
    nombre_completo = models.CharField(max_length=200, blank=True, default="")
    correo = models.CharField(max_length=200, blank=True, default="")
    empresa = models.CharField(max_length=200, blank=True, default="")
    industria = models.CharField(max_length=120, blank=True, default="")
    subtipo_automotriz = models.CharField(
        max_length=20, choices=SUBTIPO_AUTOMOTRIZ_CHOICES, blank=True, default="")
    cargo = models.CharField(max_length=120, blank=True, default="")
    pais_ciudad = models.CharField(max_length=120, blank=True, default="")

    # Diagnóstico
    situacion_contact_center = models.CharField(
        max_length=10, choices=SITUACION_CC_CHOICES, blank=True, default="")
    tipo_contact_center = models.CharField(
        max_length=15, choices=TIPO_CC_CHOICES, blank=True, default="")
    usa_ia_actualmente = models.BooleanField(null=True, blank=True)
    canales_actuales = models.JSONField(default=list, blank=True)
    volumen_interacciones = models.CharField(
        max_length=120, blank=True, default="",
        help_text="Como lo dijo el contacto, conservando período y unidad.")
    necesidad_principal = models.TextField(blank=True, default="")
    soluciones_interes = models.JSONField(default=list, blank=True)
    intencion = models.CharField(max_length=200, blank=True, default="")
    plazo_proyecto = models.CharField(max_length=120, blank=True, default="")

    # Calificación
    lead_score = models.CharField(
        max_length=15, choices=SCORE_CHOICES, blank=True, default="",
        help_text="Lo escribe calcular_lead_score (código), nunca el LLM.")
    solicita_consultoria = models.BooleanField(default=False)
    solicita_contacto_humano = models.BooleanField(default=False)

    # Cierre
    resumen_conversacion = models.TextField(blank=True, default="")
    siguiente_accion_recomendada = models.TextField(blank=True, default="")

    # Trazabilidad
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    notificado_en = models.DateTimeField(
        null=True, blank=True,
        help_text="Cuándo se notificó como HOT. Sella la notificación para que "
                  "no se repita en cada turno.")
    despachado_en = models.DateTimeField(
        null=True, blank=True,
        help_text="Cuándo se despachó al destino externo. Nulo con LEAD_SINK=none, "
                  "y nulo tras un fallo: un lead sin despachar tiene que ser visible.")

    class Meta:
        ordering = ["-actualizado"]

    def __str__(self):
        quien = self.empresa or self.nombre_completo or self.conversation.wa_id
        return f"{quien} ({self.lead_score or 'sin calificar'})"

    def save(self, *args, **kwargs):
        # Las reglas de consistencia del prompt §8 se aplican en código y no se
        # le confían al prompt: un lead que dice "no tiene Contact Center" y a
        # la vez "propio" es una contradicción que el equipo comercial no puede
        # resolver mirando la fila.
        if self.situacion_contact_center == "no_tiene":
            self.tipo_contact_center = "no_tiene"
        elif not self.situacion_contact_center:
            self.tipo_contact_center = ""
        super().save(*args, **kwargs)


@dataclasses.dataclass(frozen=True)
class SenalesLead:
    """Lo que el extractor observa en la conversación.

    Son señales verificables, no un veredicto: el extractor dice qué pasó y
    `calcular_lead_score` decide qué significa. Ver spec §7.3.
    """
    encaje_con_oferta: bool = False
    necesidad_concreta: bool = False
    interes_evaluar: bool = False
    solicita_siguiente_paso: bool = False
    intencion_avanzar_declarada: bool = False
    plazo_cercano_declarado: bool = False
    interes_exploratorio: bool = False


def calcular_lead_score(senales: SenalesLead) -> str:
    """La precedencia del prompt §6: HOT, luego WARM, luego COLD, si no
    NO_CALIFICADO.

    Se calcula acá y no en el prompt porque el resultado tiene que ser
    reproducible: el mismo lead no puede salir HOT o WARM según el humor del
    modelo en ese turno. El prompt §6 sigue existiendo como criterio de qué
    evidencia recoger; lo que sale del prompt es el veredicto.
    """
    if not senales.encaje_con_oferta:
        return "NO_CALIFICADO"
    if (senales.necesidad_concreta and senales.solicita_siguiente_paso
            and (senales.intencion_avanzar_declarada or senales.plazo_cercano_declarado)):
        return "HOT"
    if senales.necesidad_concreta and senales.interes_evaluar:
        return "WARM"
    if senales.interes_exploratorio:
        return "COLD"
    return "NO_CALIFICADO"
