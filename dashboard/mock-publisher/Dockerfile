FROM python:3.12-slim

WORKDIR /srv

# Build context is the repo root (see dashboard/docker-compose.yml) so this
# image can share dashboard/backend/app/models.py for backfill's direct-to-
# Postgres writes -- one schema definition, not a second copy that could
# drift from the API/ingest's. models.py has no dependency on the rest of
# the backend package (no FastAPI/alembic/jsonschema), so only its own
# requirements.txt (sqlalchemy + asyncpg + paho-mqtt) is needed here.
COPY dashboard/backend/app/__init__.py app/__init__.py
COPY dashboard/backend/app/models.py app/models.py

COPY dashboard/mock-publisher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard/mock-publisher/*.py .

CMD ["python", "publisher.py"]
