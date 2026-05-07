from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://trading:dev_password@localhost:5432/betfair_trading"

    # Betfair API
    betfair_username: str = ""
    betfair_password: str = ""
    betfair_app_key: str = ""
    betfair_cert_path: str = "./certs"

    # Application
    log_level: str = "INFO"
    results_data_path: str = "./data/results.csv"
    trading_config_path: Path = Path("config/trading.yaml")

    # Health check
    health_port: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
