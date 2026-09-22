"""환경 변수 → Settings. 빠진 값이 있으면 시작을 거부한다. main.py와 cli가 부른다.

비밀값 파일은 저장소 밖 ~/.qbot/.env 이다 (QBOT-INFRA-001 C5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = Path.home() / ".qbot" / ".env"


class Settings(BaseSettings):
    """봇이 읽는 설정 전부. 전략 설정은 여기가 아니라 DB의 strategy_config에 있다."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # 운영
    mode: Literal["paper", "live"] = "paper"
    data_dir: Path = Path.home() / ".qbot"
    timezone: str = "Asia/Seoul"

    # 증권사 (한국투자증권). 모의·실전은 접속 주소와 계좌만 다르다 (QBOT-INFRA-001 C6)
    kis_paper_app_key: str = Field(min_length=1)
    kis_paper_app_secret: str = Field(min_length=1)
    kis_paper_account: str = Field(min_length=8, max_length=8)
    kis_paper_product: str = "01"
    kis_live_app_key: str = ""
    kis_live_app_secret: str = ""
    kis_live_account: str = ""
    kis_live_product: str = "01"

    # 전자공시
    dart_api_key: str = Field(min_length=1)

    # 메신저
    telegram_bot_token: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)

    # 위험 한도 (QBOT-PRD-001 R9). 전략 설정이 아니라 운용 설정이라 환경 변수다
    max_drawdown: float = 0.30
    max_position_weight: float = 0.15
    daily_order_cap_multiple: float = 2.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"qbot-{self.mode}.sqlite3"

    @property
    def kis_base_url(self) -> str:
        if self.mode == "live":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    def live_ready(self) -> bool:
        """실전 접속 정보가 모두 있는가. 관문(UC-H2)이 확인한다."""
        return all([self.kis_live_app_key, self.kis_live_app_secret, len(self.kis_live_account) == 8])


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
    """env 파일을 읽어 Settings를 만든다. 필수 값이 없으면 ValidationError로 실패한다."""
    if env_file is not None and env_file.exists():
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()
