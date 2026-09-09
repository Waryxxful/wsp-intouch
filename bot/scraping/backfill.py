def agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting, cliente="renault") -> int:
    """Crea una ScrapingSource por cada url distinta entre los ScrapeRun
    existentes y les asigna el source correspondiente. Recibe las clases de
    modelo por parametro para poder llamarse tanto desde una migracion de
    datos (modelos historicos via apps.get_model) como desde un test
    (modelos reales). Devuelve la cantidad de fuentes creadas.

    `cliente` default "renault" a proposito, no settings.CLIENTE_ACTIVO: esto
    respalda la migracion 0008, anterior a que existiera multi-cliente -- los
    ScrapeRun que backfillea son inequivocamente de la era Renault-only, sin
    importar bajo que CLIENTE_ACTIVO corra el proceso que aplica la
    migracion. Los modelos historicos de una migracion tampoco tienen el
    manager filtrado (_ClienteActivoManager), asi que sin este parametro
    ScrapingSource.objects.create() cae en silencio al default del campo del
    modelo -- que hoy tambien es "renault", pero por coincidencia, no por
    diseno."""

    def _get_setting(key: str, default: str = "") -> str:
        try:
            return Setting.objects.get(key=key).value
        except Setting.DoesNotExist:
            return default

    active_url = _get_setting("scraping_target_url", "")
    try:
        active_frecuencia = float(_get_setting("scraping_frecuencia_horas", "0") or 0)
    except ValueError:
        active_frecuencia = 0

    # Use set() to deduplicate instead of distinct() which has issues in SQLite
    urls = list(set(ScrapeRun.objects.values_list("url", flat=True)))
    creadas = 0
    for url in urls:
        frecuencia = active_frecuencia if url == active_url else 0
        source, created = ScrapingSource.objects.get_or_create(
            url=url, defaults={"frecuencia_horas": frecuencia, "cliente": cliente},
        )
        ScrapeRun.objects.filter(url=url).update(source=source)
        if created:
            creadas += 1

    if active_url and active_url not in urls:
        _, created = ScrapingSource.objects.get_or_create(
            url=active_url, defaults={"frecuencia_horas": active_frecuencia, "cliente": cliente},
        )
        if created:
            creadas += 1

    return creadas
