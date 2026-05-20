from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: str = "dev-key-change-me"
    fmcsa_web_key: str = ""
    database_url: str = "sqlite:///./app.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
