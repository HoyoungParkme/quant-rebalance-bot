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

    # 운용 자금 (QBOT-PRD-001 7장 계획값). 매매 도메인이 생기기 전 판단 예산과 재현에 쓴다
    planned_capital: int = 3_000_000

    # 위험 한도 (QBOT-PRD-001 R9). 전략 설정이 아니라 운용 설정이라 환경 변수다
    max_drawdown: float = 0.30
    max_position_weight: float = 0.15
    daily_order_cap_multiple: float = 2.0

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"qbot-{self.mode}.sqlite3"

    @property
    def kis_base_url(self) -> str:
        if self.mode == "live":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    def query_credentials(self) -> tuple[str, str, str, float]:
        """조회용 접속. 실전 키가 있으면 그것(초당 20건), 없으면 모의(초당 1건). 조회는 돈이 움직이지 않는다.

        (QBOT-CODE-001 미결 결정: 수집은 실전 조회 키로, 주문은 모드에 따라)
        """
        if self.kis_live_app_key and self.kis_live_app_secret:
            return (
                "https://openapi.koreainvestment.com:9443",
                self.kis_live_app_key,
                self.kis_live_app_secret,
                15.0,
            )
        return (
            "https://openapivts.koreainvestment.com:29443",
            self.kis_paper_app_key,
            self.kis_paper_app_secret,
            0.9,
        )

    def order_credentials(self) -> tuple[str, str, str, str, str, float]:
        """주문용 접속. 조회와 달리 모드 그대로 쓴다. 모의 모드면 모의 계좌로만 주문이 나간다.

        (접속 주소, 앱 키, 앱 시크릿, 계좌 8자리, 상품 코드, 초당 호출 한도)
        """
        if self.mode == "live":
            if not self.live_ready():
                raise ValueError("실전 모드인데 실전 접속 정보(키·시크릿·계좌 8자리)가 없다")
            return (
                "https://openapi.koreainvestment.com:9443",
                self.kis_live_app_key,
                self.kis_live_app_secret,
                self.kis_live_account,
                self.kis_live_product,
                15.0,
            )
        return (
            "https://openapivts.koreainvestment.com:29443",
            self.kis_paper_app_key,
            self.kis_paper_app_secret,
            self.kis_paper_account,
            self.kis_paper_product,
            0.9,
        )

    def live_ready(self) -> bool:
        """실전 접속 정보가 모두 있는가. 관문(UC-H2)이 확인한다."""
        return all([self.kis_live_app_key, self.kis_live_app_secret, len(self.kis_live_account) == 8])


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
    """env 파일을 읽어 Settings를 만든다. 필수 값이 없으면 ValidationError로 실패한다."""
    if env_file is not None and env_file.exists():
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()
