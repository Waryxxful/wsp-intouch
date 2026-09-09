FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg2 unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# requirements-lock.txt (no requirements.txt) es lo que se instala de
# verdad -- version exacta de cada dependencia, generada con `pip freeze`
# contra un entorno ya verificado (ver comentario en ese archivo). Sin esto
# un rebuild sin cache podia resolver una combinacion nueva de un dia para
# otro y romper produccion sin que nadie tocara codigo (docs/PENDIENTES.md).
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY . .
EXPOSE 8000
# start-period generoso: migrate+collectstatic corren antes de que gunicorn
# levante (ver CMD abajo), y pueden tardar si hay migraciones pendientes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1
# El orden y el scoping por app de las dos llamadas a `migrate` de abajo son
# OBLIGATORIOS mientras "default" y "qaintouch" sigan siendo la MISMA base de
# datos fisica en QA real (mismo SQL Server, mismo NAME="QAIntouch" -- ver
# leads/tests.py:DockerfileMigrateOrderRegressionTest). Ambos alias comparten
# entonces una unica tabla django_migrations, y Django marca una migracion
# como "aplicada" ahi incluso cuando TODAS sus operaciones fueron saltadas
# por allow_migrate. Por eso:
#   1) "migrate leads --database=qaintouch" va PRIMERO y con el app_label
#      acotado a "leads": si fuera un `migrate --database=qaintouch` sin
#      acotar, recorreria el grafo completo y marcaria como "aplicadas" (sin
#      crear tablas, por allow_migrate) las migraciones de bot/admin/auth/etc,
#      rompiendo esas apps cuando el segundo comando (default) las encuentre
#      ya "aplicadas" en la tabla compartida y no cree sus tablas reales.
#   2) "migrate" (default, grafo completo) va SEGUNDO: si fuera al revez,
#      marcaria leads.0001_initial como aplicada (saltada por allow_migrate
#      en "default") ANTES de que la pasada de qaintouch pudiera crear la
#      tabla real -- es exactamente el bug critico detectado en la revision
#      final de este plan.
# Arreglo definitivo pendiente: darle a "qaintouch" su propio schema/base de
# datos real en SQL Server (requiere un GRANT que hoy no esta disponible),
# lo que elimina este riesgo por completo.
# "exec gunicorn" (no solo "gunicorn"): sin el exec, gunicorn corre como hijo
# de este `sh -c`, que sigue siendo el PID 1 del contenedor -- una señal
# (ej. SIGHUP para reload en caliente sin downtime, ver docker-compose.yml)
# mandada al contenedor le llega a `sh`, que no la reenvia a sus hijos. Con
# exec, gunicorn REEMPLAZA al shell y pasa a ser el PID 1 real, así que
# `docker exec <contenedor> kill -HUP 1` (o `docker kill --signal=HUP`) le
# llega directo y dispara el graceful reload nativo de gunicorn (arranca
# workers nuevos con el codigo actualizado, mata a los viejos recien cuando
# terminan sus requests en curso -- cero conexiones cortadas).
CMD ["sh", "-c", "python manage.py migrate leads --database=qaintouch --noinput && python manage.py migrate --noinput && python manage.py collectstatic --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile - --error-logfile - --log-level info --capture-output"]
