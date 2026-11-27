"""
Testes do outbox de mensagens (repositório + OutboxSender + status de webhook).

O CloudAPIClient é substituído por um FakeClient com respostas roteirizadas:
o sender só depende da interface (send_template/send_text → CloudSendResult),
então não é preciso simular HTTP aqui — isso já é coberto em
tests/adapters/test_cloud_api_client.py.
"""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.adapters.cloud_api_client import CloudSendResult
from src.domain.models import Base, OutboxMessage, OutboxStatus
from src.repositories import outbox as outbox_repo
from src.services.outbox_sender import (
    BACKOFF_BASE,
    MAX_ATTEMPTS,
    STALE_SENDING_AFTER,
    OutboxSender,
    apply_status_update,
)


NOW = datetime.datetime(2026, 11, 24, 9, 0, 0)
TO = "5567999999999"


# ── Fixtures / helpers ────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def ok(wamid: str = "wamid.OK") -> CloudSendResult:
    return CloudSendResult(success=True, phone=TO, message_id=wamid)


def err(status_code: int | None, error_code: int | None = None) -> CloudSendResult:
    return CloudSendResult(
        success=False, phone=TO, error=f"erro {status_code}/{error_code}",
        error_code=error_code, status_code=status_code,
    )


class FakeClient:
    """Imita o CloudAPIClient devolvendo resultados na ordem roteirizada."""

    def __init__(self, *results: CloudSendResult, on_send=None):
        self.results = list(results)
        self.calls: list[tuple] = []
        self.on_send = on_send   # hook para inspecionar o banco DURANTE o envio

    def send_template(self, phone, template_name, body_params=None, language="pt_BR"):
        self.calls.append(("template", phone, template_name, body_params, language))
        return self._next()

    def send_text(self, phone, text, preview_url=False):
        self.calls.append(("text", phone, text))
        return self._next()

    def _next(self) -> CloudSendResult:
        if self.on_send:
            self.on_send()
        return self.results.pop(0)


def enqueue(db: Session, **kw) -> OutboxMessage:
    defaults = dict(recipient=TO, template_name="aviso_audiencias_resumo_v1",
                    params=["Cond. X", "12/11/2026", "5"], now=NOW)
    msg = outbox_repo.enqueue_template(db, **{**defaults, **kw})
    db.commit()
    return msg


# ── Repositório ───────────────────────────────────────────────────────────────

class TestEnqueue:
    def test_dedupe_key_prevents_duplicate(self, db):
        first = enqueue(db, dedupe_key="hearing:42:2026-11-12")
        second = enqueue(db, dedupe_key="hearing:42:2026-11-12")
        assert first is not None
        assert second is None
        assert db.query(OutboxMessage).count() == 1

    def test_without_dedupe_key_allows_repeats(self, db):
        enqueue(db)
        enqueue(db)
        assert db.query(OutboxMessage).count() == 2

    def test_new_message_is_pending(self, db):
        msg = enqueue(db, ref_type="hearing_summary", ref_id="42")
        assert msg.status == OutboxStatus.PENDING
        assert msg.attempts == 0
        assert msg.ref_type == "hearing_summary"


class TestClaimDue:
    def test_skips_messages_waiting_for_retry(self, db):
        ready = enqueue(db)
        waiting = enqueue(db)
        waiting.next_attempt_at = NOW + datetime.timedelta(minutes=5)
        db.commit()
        assert outbox_repo.claim_due(db, now=NOW) == [ready]

    def test_skips_non_pending(self, db):
        sent = enqueue(db)
        sent.status = OutboxStatus.SENT
        db.commit()
        assert outbox_repo.claim_due(db, now=NOW) == []

    def test_respects_limit_and_order(self, db):
        msgs = [enqueue(db, now=NOW + datetime.timedelta(seconds=i)) for i in range(3)]
        assert outbox_repo.claim_due(db, now=NOW + datetime.timedelta(hours=1), limit=2) == msgs[:2]


# ── Sender ────────────────────────────────────────────────────────────────────

class TestSender:
    def test_success_marks_sent_with_wamid(self, db):
        msg = enqueue(db)
        summary = OutboxSender(FakeClient(ok("wamid.ABC"))).process_due(db, now=NOW)

        assert summary.sent == 1
        assert msg.status == OutboxStatus.SENT
        assert msg.wamid == "wamid.ABC"
        assert msg.sent_at == NOW
        assert msg.attempts == 1

    def test_template_payload_passed_to_client(self, db):
        enqueue(db)
        client = FakeClient(ok())
        OutboxSender(client).process_due(db, now=NOW)
        assert client.calls == [
            ("template", TO, "aviso_audiencias_resumo_v1", ["Cond. X", "12/11/2026", "5"], "pt_BR")
        ]

    def test_text_message_uses_send_text(self, db):
        outbox_repo.enqueue_text(db, recipient=TO, text="Lista completa...", now=NOW)
        db.commit()
        client = FakeClient(ok())
        OutboxSender(client).process_due(db, now=NOW)
        assert client.calls == [("text", TO, "Lista completa...")]

    def test_status_is_sending_while_api_call_runs(self, db):
        """A reserva 'sending' precisa estar gravada ANTES da chamada à API."""
        msg = enqueue(db)
        seen: list[str] = []

        def inspect():
            db.expire(msg)               # força reler do banco, não do cache da sessão
            seen.append(msg.status)

        OutboxSender(FakeClient(ok(), on_send=inspect)).process_due(db, now=NOW)
        assert seen == [OutboxStatus.SENDING]

    @pytest.mark.parametrize("result", [
        err(None),                 # timeout / sem resposta
        err(500),                  # erro do servidor da Meta
        err(429, 130429),          # rate limit
        err(400, 131056),          # muitas mensagens ao mesmo destinatário
    ])
    def test_transient_error_schedules_retry(self, db, result):
        msg = enqueue(db)
        summary = OutboxSender(FakeClient(result)).process_due(db, now=NOW)

        assert summary.retried == 1
        assert msg.status == OutboxStatus.PENDING
        assert msg.attempts == 1
        assert msg.next_attempt_at == NOW + BACKOFF_BASE
        assert msg.last_error is not None

    def test_backoff_doubles_each_attempt(self, db):
        msg = enqueue(db)
        msg.attempts = 2                  # já falhou 2 vezes
        db.commit()
        OutboxSender(FakeClient(err(500))).process_due(db, now=NOW)
        assert msg.next_attempt_at == NOW + BACKOFF_BASE * 4   # 3ª tentativa → 4 min

    def test_transient_error_on_last_attempt_fails(self, db):
        msg = enqueue(db)
        msg.attempts = MAX_ATTEMPTS - 1
        db.commit()
        summary = OutboxSender(FakeClient(err(500))).process_due(db, now=NOW)
        assert summary.failed == 1
        assert msg.status == OutboxStatus.FAILED
        assert msg.failed_at == NOW

    @pytest.mark.parametrize("error_code", [131026, 131047, 132001, 100])
    def test_permanent_error_fails_immediately(self, db, error_code):
        msg = enqueue(db)
        summary = OutboxSender(FakeClient(err(400, error_code))).process_due(db, now=NOW)
        assert summary.failed == 1
        assert msg.status == OutboxStatus.FAILED
        assert msg.last_error_code == error_code
        assert msg.attempts == 1

    def test_auth_error_halts_batch_without_consuming_attempts(self, db):
        first = enqueue(db)
        second = enqueue(db)
        client = FakeClient(err(401, 190), ok())
        summary = OutboxSender(client).process_due(db, now=NOW)

        assert summary.halted is True
        assert len(client.calls) == 1               # a segunda nem foi tentada
        assert first.status == OutboxStatus.PENDING
        assert first.attempts == 0
        assert first.last_error_code == 190
        assert second.status == OutboxStatus.PENDING

    def test_recovers_stale_sending(self, db):
        msg = enqueue(db)
        msg.status = OutboxStatus.SENDING
        msg.sending_at = NOW - STALE_SENDING_AFTER - datetime.timedelta(minutes=1)
        msg.attempts = 1
        db.commit()

        summary = OutboxSender(FakeClient(ok())).process_due(db, now=NOW)
        assert summary.recovered == 1
        assert summary.sent == 1
        assert msg.status == OutboxStatus.SENT
        assert msg.attempts == 2

    def test_recent_sending_is_left_alone(self, db):
        msg = enqueue(db)
        msg.status = OutboxStatus.SENDING
        msg.sending_at = NOW - datetime.timedelta(minutes=1)
        db.commit()
        client = FakeClient()
        summary = OutboxSender(client).process_due(db, now=NOW)
        assert summary.recovered == 0
        assert client.calls == []
        assert msg.status == OutboxStatus.SENDING


# ── Status vindos do webhook ──────────────────────────────────────────────────

def _sent_message(db: Session, wamid: str = "wamid.X") -> OutboxMessage:
    msg = enqueue(db)
    msg.status = OutboxStatus.SENT
    msg.wamid = wamid
    msg.sent_at = NOW
    db.commit()
    return msg


T1 = NOW + datetime.timedelta(minutes=1)
T2 = NOW + datetime.timedelta(minutes=2)


class TestApplyStatusUpdate:
    def test_delivered_then_read(self, db):
        msg = _sent_message(db)
        apply_status_update(db, wamid="wamid.X", status="delivered", timestamp=T1)
        apply_status_update(db, wamid="wamid.X", status="read", timestamp=T2)
        assert msg.status == OutboxStatus.READ
        assert msg.delivered_at == T1
        assert msg.read_at == T2

    def test_out_of_order_never_regresses(self, db):
        msg = _sent_message(db)
        apply_status_update(db, wamid="wamid.X", status="read", timestamp=T2)
        apply_status_update(db, wamid="wamid.X", status="delivered", timestamp=T1)
        assert msg.status == OutboxStatus.READ        # não volta para delivered
        assert msg.delivered_at == T1                 # mas o horário é registrado

    def test_failed_after_sent(self, db):
        msg = _sent_message(db)
        apply_status_update(db, wamid="wamid.X", status="failed", timestamp=T1,
                            error_code=131026, error_message="Mensagem não entregável")
        assert msg.status == OutboxStatus.FAILED
        assert msg.failed_at == T1
        assert msg.last_error_code == 131026

    def test_failed_after_read_is_ignored(self, db):
        msg = _sent_message(db)
        apply_status_update(db, wamid="wamid.X", status="read", timestamp=T1)
        apply_status_update(db, wamid="wamid.X", status="failed", timestamp=T2)
        assert msg.status == OutboxStatus.READ
        assert msg.failed_at is None

    def test_first_timestamp_wins(self, db):
        msg = _sent_message(db)
        apply_status_update(db, wamid="wamid.X", status="delivered", timestamp=T1)
        apply_status_update(db, wamid="wamid.X", status="delivered", timestamp=T2)
        assert msg.delivered_at == T1

    def test_unknown_wamid_returns_false(self, db):
        assert apply_status_update(db, wamid="wamid.NOPE", status="delivered", timestamp=T1) is False
