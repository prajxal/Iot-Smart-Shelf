"""Application configuration module for Smart Shelf backend.

All tunable SRI weights, hysteresis thresholds, and alert parameters are defined
here with default values and can be overridden via environment variables.
Per-commodity biological values are NEVER defined here — they reside strictly in
commodity-profiles.json and the MongoDB commodity_profiles collection.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Database settings
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "smart_shelf"

    # API server settings
    api_title: str = "Smart Shelf IoT Backend"
    api_version: str = "0.3.0"
    api_description: str = (
        "FastAPI backend for ESP32-based spoilage-monitoring shelf in MSME kirana stores."
    )

    # -------------------------------------------------------------------------
    # SRI Algorithm Tunable Constants (PRD §5.2 / §7 Open Question 3)
    # These are engineering weights and thresholds, NOT biological commodity data.
    # -------------------------------------------------------------------------

    # Tunable weights for Spoilage Risk Index: w1 (temp), w2 (rh), w3 (gas)
    # Sum of weights should equal 1.0
    w1: float = 0.50  # TODO(confirm): SRI temperature excess weight
    w2: float = 0.30  # TODO(confirm): SRI relative humidity deviation weight
    w3: float = 0.20  # TODO(confirm): SRI gas signal weight

    # Gas normalization parameters (PRD §5.2 / §7 Open Question 2)
    # gas_signal = gas_raw / mq135_baseline
    gas_signal_baseline: float = 1.0  # Normalized baseline ratio (1.0 = baseline)
    gas_signal_span: float = 2.0  # TODO(confirm): MQ-135 signal span above baseline for full scale (1.0)

    # Hysteresis fan control thresholds (PRD §5.3)
    sri_on: float = 0.60  # TODO(confirm): SRI threshold to turn fan ON
    sri_off: float = 0.40  # TODO(confirm): SRI threshold to turn fan OFF

    # Alert lifecycle thresholds (PRD §5.4)
    alert_threshold: float = 0.70  # TODO(confirm): SRI threshold to trigger high risk alert
    alert_resolve_threshold: float = 0.40  # TODO(confirm): SRI threshold to resolve high risk alert

    # Allow configuration via .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SMART_SHELF_",
        extra="ignore",
    )


settings = Settings()
