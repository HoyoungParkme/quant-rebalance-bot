"""도메인 오류 타입. 메시지는 사람이 읽는 한국어."""


class QbotError(Exception):
    """모든 봇 오류의 부모."""


class DataNotReady(QbotError):
    """오늘 수집이 끝나지 않아 판단할 수 없다."""


class BrokerUnavailable(QbotError):
    """증권사에 접속할 수 없다."""


class BrokerSendFailed(QbotError):
    """주문 전송 호출이 실패했다. 증권사에 닿았는지 알 수 없다. 주문 상태는 unknown이다."""


class BrokerRejected(QbotError):
    """증권사가 주문을 거부했다(잔고 부족, 장 시간 아님 등). 확실히 체결되지 않았다."""


class OrdersBlocked(QbotError):
    """계좌 불일치 등으로 주문이 멈춰 있다."""


class GateFailed(QbotError):
    """실전 전환 관문 조건 미달."""


class Precondition(QbotError):
    """상태가 맞지 않는다."""


class InvalidState(QbotError):
    """판단·주문의 상태 전이가 허용되지 않는다."""
