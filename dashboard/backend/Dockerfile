FROM python:3.12-slim

WORKDIR /srv

COPY pyproject.toml .
COPY app app

RUN pip install --no-cache-dir .

# entrypoint.sh picks api vs ingest based on $1 so one image serves both
# dashboard/docker-compose.yml services -- no separate Dockerfile to drift.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
