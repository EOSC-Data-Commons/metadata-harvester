from typing import Literal
from pydantic_settings import BaseSettings
import os

Environment = Literal["production", "staging", "dev", "local"]


class HarvesterSettings(BaseSettings):
    ENVIRONMENT: Environment = "dev"
    WAREHOUSE_API_URL: str

    LOG_DIR: str = "./logs"
    LOG_LEVEL: str = "INFO"
    WAREHOUSE_API_TIMEOUT: int = 30

    class Config:
        env_file = ".env"
        case_sensitive = True
        # tolerate unrelated keys in a host project's .env when used as a library
        extra = "ignore"

class ProductionSettings(HarvesterSettings):
    """Production settings"""
    pass


class StagingSettings(HarvesterSettings):
    WAREHOUSE_API_URL: str = "http://192.168.10.6:8080"


class DevSettings(HarvesterSettings):
    WAREHOUSE_API_URL: str = "http://localhost:8080"


class LocalSettings(HarvesterSettings):
    pass # set in .env


def get_settings() -> HarvesterSettings:
    env: Environment = os.getenv("ENVIRONMENT", "dev") # type: ignore[assignment]

    if env == "production":
        return ProductionSettings() # type: ignore[call-arg]
    elif env == "staging":
        return StagingSettings()
    elif env == "local":
        return LocalSettings() # type: ignore[call-arg]
    else:
        return DevSettings()


# settings in use, read through current_settings() so it can be swapped per run
_current_settings: HarvesterSettings | None = None


def current_settings() -> HarvesterSettings:
    """
    Return the settings currently in use, falling back to the environment
    profile (ENVIRONMENT / .env / defaults) on first access.
    """
    global _current_settings
    if _current_settings is None:
        _current_settings = get_settings()
    return _current_settings


def set_settings(new_settings: HarvesterSettings | None) -> HarvesterSettings:
    """
    Install the settings used by all harvester modules.

    :param new_settings: settings to use, or None to fall back to the environment profile
    :return: the settings now in use
    """
    global _current_settings
    _current_settings = new_settings
    return current_settings()