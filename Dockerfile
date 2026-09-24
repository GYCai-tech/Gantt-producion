FROM python:3.12-slim-bookworm

# Logs sin buffer y sin .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Driver ODBC de SQL Server: la app lee el ERP en vivo
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg unixodbc-dev tzdata \
    && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# Dependencias primero (mejor cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la app
COPY app ./app
COPY templates ./templates
COPY static ./static

# La ficha de máquinas que trabajan solas. Es un dato de producción, no
# código, y por eso docker-compose la monta encima para poder cambiarla sin
# reconstruir. Va también en la imagen para que un despliegue sin el montaje
# siga funcionando: sin ficha no hay máquinas automáticas y la cola trata
# todo como atendido, que es como se comportaba antes de que existiera.
COPY maquinas-auto.txt .

# Usuario no-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Healthcheck: la home responde 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
