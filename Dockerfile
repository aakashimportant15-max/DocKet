FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY fixtures.json .

# Seed on every start (not at build time) so `docker compose up` always boots
# a freshly seeded portal, then serve on 0.0.0.0:8000.
CMD ["sh", "-c", "python scripts/seed_fixtures.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
