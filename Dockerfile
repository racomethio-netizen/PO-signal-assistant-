FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV API_HOST=0.0.0.0
ENV API_PORT=8000
EXPOSE 8000

# No secrets are baked into the image; provide them at runtime via --env-file .env
CMD ["python", "run.py"]
