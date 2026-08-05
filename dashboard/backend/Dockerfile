FROM python:3.12-slim

WORKDIR /srv

# Build context is the repo root (see dashboard/docker-compose.yml) so this
# image can also pull in contract/, which lives outside dashboard/ since the
# firmware is the schema's other party.
COPY dashboard/backend/pyproject.toml .
COPY dashboard/backend/app app
COPY dashboard/backend/alembic alembic
COPY dashboard/backend/alembic.ini .
COPY contract /contract

RUN pip install --no-cache-dir .

ENV FENCE_CONTRACT_SCHEMA_PATH=/contract/fence-state.schema.json

# entrypoint.sh picks api vs ingest vs migrate based on $1 so one image
# serves all three dashboard/docker-compose.yml services -- no separate
# Dockerfile to drift.
COPY dashboard/backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
