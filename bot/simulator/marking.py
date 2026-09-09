from bot.models import Conversation

TEST_WA_ID_PREFIJO = "TEST"
LEAD_RAZON_PREFIJO = "[TEST] "


def generar_wa_id_test() -> str:
    """wa_id correlativo con prefijo TEST, nunca mas de 20 caracteres (limite
    de Conversation.wa_id). Herramienta manual de un solo operador -- no
    protege contra corridas concurrentes del mismo comando."""
    maximo = 0
    for wa_id in Conversation.objects.filter(
        wa_id__startswith=TEST_WA_ID_PREFIJO
    ).values_list("wa_id", flat=True):
        sufijo = wa_id[len(TEST_WA_ID_PREFIJO):]
        if sufijo.isdigit():
            maximo = max(maximo, int(sufijo))
    return f"{TEST_WA_ID_PREFIJO}{maximo + 1:06d}"


def nombre_contacto_test(persona: str) -> str:
    return f"[PRUEBA] {persona[:40]}"


def marcar_lead_de_test(lead_id: int) -> None:
    from leads.models import Lead

    lead = Lead.objects.filter(id=lead_id).first()
    if lead and not lead.razon_interes.startswith(LEAD_RAZON_PREFIJO):
        lead.razon_interes = f"{LEAD_RAZON_PREFIJO}{lead.razon_interes}"
        lead.save(update_fields=["razon_interes"])
