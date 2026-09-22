"""한국투자증권 REST 클라이언트. 토큰 캐시, 초당 호출 한도, 조회 재시도 (QBOT-UC-001 UC-S7).

조회(get)는 재시도하고 주문(post)은 재시도하지 않는다 (QBOT-INFRA-001 C8).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app.core.clock import KST
from app.core.errors import BrokerRejected, BrokerSendFailed, BrokerUnavailable

TRANSIENT_MSG_CODES = {"EGW00201", "EGW00123"}  # 초당 거래건수 초과, 일시 오류

_BUDGETS: dict[str, list] = {}  # 앱 키 → [잠금, 마지막 호출 시각]
_BUDGETS_LOCK = threading.Lock()


def _budget(app_key: str) -> list:
    """초당 한도는 앱 키마다 걸린다. 조회용·주문용 클라이언트가 같은 키면 시각을 같이 본다."""
    with _BUDGETS_LOCK:
        b = _BUDGETS.get(app_key)
        if b is None:
            b = _BUDGETS[app_key] = [threading.Lock(), 0.0]
        return b


@dataclass
class KisClient:
    base_url: str
    app_key: str
    app_secret: str
    rate_per_sec: float = 1.0
    token_cache: Path | None = None
    timeout: float = 10.0
    _token: str | None = field(default=None, init=False, repr=False)
    _token_expires: datetime | None = field(default=None, init=False, repr=False)
    _http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10.0), init=False, repr=False)

    # ----- 토큰 -----
    def token(self) -> str:
        now = datetime.now(KST)
        if self._token and self._token_expires and now < self._token_expires - timedelta(minutes=10):
            return self._token
        if self.token_cache and self.token_cache.exists():
            try:
                c = json.loads(self.token_cache.read_text())
                exp = datetime.fromisoformat(c["expires_at"])
                if now < exp - timedelta(minutes=10):
                    self._token, self._token_expires = c["token"], exp
                    return self._token
            except (KeyError, ValueError, json.JSONDecodeError):
                pass
        r = self._http.post(
            f"{self.base_url}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
        )
        if r.status_code != 200 or "access_token" not in r.json():
            raise BrokerUnavailable(f"토큰 발급 실패: {r.status_code} {r.text[:120]}")
        body = r.json()
        self._token = body["access_token"]
        # 만료 시각은 KST 문자열 'YYYY-MM-DD HH:MM:SS'
        self._token_expires = datetime.strptime(body["access_token_token_expired"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=KST
        )
        if self.token_cache:
            self.token_cache.parent.mkdir(parents=True, exist_ok=True)
            self.token_cache.write_text(
                json.dumps({"token": self._token, "expires_at": self._token_expires.isoformat()})
            )
            self.token_cache.chmod(0o600)
        return self._token

    def _headers(self, tr_id: str, tr_cont: str = "") -> dict:
        return {
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",
            "content-type": "application/json; charset=utf-8",
        }

    def _throttle(self) -> None:
        gap = 1.0 / self.rate_per_sec
        b = _budget(self.app_key)
        with b[0]:  # 여러 스레드·여러 클라이언트가 같이 써도 앱 키 기준으로 한도를 지킨다
            wait = b[1] + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            b[1] = time.monotonic()

    # ----- 주문 (재시도 없음) -----
    def post(self, path: str, tr_id: str, body: dict) -> dict:
        """주문·취소. 한 번만 보낸다.

        200이고 rt_cd != 0 이면 증권사에 닿아서 거부된 것이라 BrokerRejected(확실히 미체결),
        그 밖(접속 실패, 5xx, 타임아웃)은 닿았는지 알 수 없으므로 BrokerSendFailed다.
        """
        headers = self._headers(tr_id)
        h = self._hashkey(body)  # 여기서 한 번, 아래 전송에서 한 번 간격을 지킨다
        if h:
            headers["hashkey"] = h
        self._throttle()
        try:
            r = self._http.post(f"{self.base_url}{path}", headers=headers, json=body)
        except httpx.HTTPError as e:
            raise BrokerSendFailed(f"{tr_id} 전송 실패: {e}") from e
        if r.status_code != 200:
            if "token" in r.text.lower():  # 토큰 문제면 다음 호출을 위해 버린다
                self._forget_token()
            raise BrokerSendFailed(f"{tr_id} {r.status_code} {r.text[:120]}")
        body_out = r.json()
        if body_out.get("rt_cd") != "0":
            msg = f"{body_out.get('msg_cd')} {(body_out.get('msg1') or '').strip()}"
            if body_out.get("msg_cd") in TRANSIENT_MSG_CODES:
                raise BrokerUnavailable(msg)  # 잠깐 못 받은 것. 주문 자체를 접지 않는다
            raise BrokerRejected(msg)
        return body_out

    def _hashkey(self, body: dict) -> str | None:
        """주문 본문 해시. 없어도 주문은 되므로 실패하면 헤더를 빼고 보낸다."""
        self._throttle()  # 이것도 같은 앱 키의 호출이다. 빼먹으면 주문이 한도에 걸린다
        try:
            r = self._http.post(
                f"{self.base_url}/uapi/hashkey",
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                json=body,
            )
            return r.json().get("HASH") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    def _forget_token(self) -> None:
        self._token, self._token_expires = None, None
        if self.token_cache and self.token_cache.exists():
            self.token_cache.unlink()

    # ----- 조회 (재시도 있음) -----
    def get(self, path: str, tr_id: str, params: dict, tr_cont: str = "", retries: int = 3) -> dict:
        last = None
        for attempt in range(retries + 1):
            self._throttle()
            try:
                r = self._http.get(f"{self.base_url}{path}", headers=self._headers(tr_id, tr_cont), params=params)
            except httpx.HTTPError as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (401, 500) and "token" in r.text.lower():
                self._forget_token()  # 토큰이 무효. 캐시도 버리고 새로 받는다
                last = BrokerUnavailable(f"토큰 무효: {r.text[:80]}")
                continue
            if r.status_code >= 500:
                last = BrokerUnavailable(f"{r.status_code} {r.text[:120]}")
                time.sleep(1.5 * (attempt + 1))
                continue
            body = r.json()
            if body.get("rt_cd") != "0":
                if body.get("msg_cd") in TRANSIENT_MSG_CODES:
                    last = BrokerUnavailable(body.get("msg1", ""))
                    time.sleep(1.0 * (attempt + 1))
                    continue
                # 그 밖의 오류(지원 안 되는 TR, 잘못된 종목 등)는 "자료 없음"이 아니라 오류다
                raise BrokerUnavailable(f"{tr_id} {body.get('msg_cd')} {body.get('msg1')}")
            body["_tr_cont"] = r.headers.get("tr_cont", "")
            return body
        raise BrokerUnavailable(f"조회 실패 {path}: {last}")
