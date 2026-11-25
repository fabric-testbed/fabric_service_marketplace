# FABRIC Service Marketplace: the portal (Flask app behind Gunicorn).
# Built and run by docker-compose.yml in this folder; see docs/SETUP.md.
FROM python:3.13-slim

WORKDIR /usr/src/app

# gosu: start as root to fix the ownership of the mounted data folder, then drop to "portal"
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Database, secret key and the gateway's certificate live in instance/, mounted from the host
RUN groupadd -r portal && useradd -r -g portal -d /usr/src/app portal \
    && mkdir -p /usr/src/app/instance \
    && chown -R portal:portal /usr/src/app \
    && mv docker-entrypoint.sh /usr/local/bin/ && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV FLASK_APP=app.py \
    PYTHONUNBUFFERED=1 \
    PORTAL_SECRET_KEY_FILE=/usr/src/app/instance/.secret_key

EXPOSE 5000

ENTRYPOINT ["docker-entrypoint.sh"]

# --limit-request-field_size: the FABRIC login cookie can be over 8 KB
CMD ["gunicorn", "-w", "4", "--threads", "8", "-b", "0.0.0.0:5000", \
     "--limit-request-field_size", "32768", "--access-logfile", "-", "app:app"]
