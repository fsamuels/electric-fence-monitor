from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
