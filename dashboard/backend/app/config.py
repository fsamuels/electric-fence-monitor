from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root contract/fence-state.schema.json. Correct for local dev/tests,
# where this file lives at dashboard/backend/app/config.py; the Docker image
# instead copies the contract to /contract and overrides this via
# FENCE_CONTRACT_SCHEMA_PATH (see dashboard/backend/Dockerfile) -- the
# container's shallower directory tree doesn't have a parents[3] at all.
_here_parents = Path(__file__).resolve().parents
_DEFAULT_CONTRACT_SCHEMA_PATH = str(
    _here_parents[3] / "contract" / "fence-state.schema.json"
    if len(_here_parents) > 3
    else "/contract/fence-state.schema.json"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FENCE_")

    database_url: str = "postgresql+asyncpg://fence:fence@db:5432/fence"
    mqtt_host: str = "broker"
    mqtt_port: int = 1883

    # Ingest heartbeat is considered stale past this many seconds. D1 tunes
    # this against report_interval_s once nodes are reporting on a real
    # cadence; for D0's empty ingest container it just needs to be longer
    # than the ingest loop's own sleep interval.
    ingest_heartbeat_stale_s: int = 30

    contract_schema_path: str = _DEFAULT_CONTRACT_SCHEMA_PATH

    # Status thresholds -- see docs/dashboard-plan.md's "Status derivation"
    # table and software-plan.md Phase 6. Config, not hardcoded: the plan
    # calls out these may differ per node or season.
    status_low_kv: float = 5.0
    status_down_kv: float = 1.0
    status_low_consecutive: int = 3
    status_silent_multiplier: float = 2.5


settings = Settings()
