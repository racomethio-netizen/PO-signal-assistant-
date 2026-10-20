FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV API_HOST=0.0.0.0
EXPOSE 8000

# Port: defaults to 8000 for local `docker run`. On a PaaS (Render, Railway,
# Heroku, ...) that assigns a port via the PORT env var, run.py picks that
# up automatically — do NOT hardcode API_PORT here or it would always win.
#
# No secrets are baked into the image; provide them at runtime via --env-file .env
CMD ["python", "run.py"]
