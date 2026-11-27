"""Testes do WebhookHandler: eventos do webhook aplicados ao banco (SQLite em memória)."""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.adapters.whatsapp_webhook import MessageEvent, StatusEvent, WebhookBatch
from src.domain.models import Base, InboundMessage, OutboxMessage, OutboxStatus
from src.repositories import inbound as inbound_repo
from src.repositories import outbox as outbox_repo
from src.services.webhook_handler import WebhookHandler


NOW = datetime.datetime(2026, 11, 26, 10, 0, 0)
TO = "5567999999999"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _sent_message(db: Session, wamid: str = "wamid.OUT", ref_type: str = "hearing_summary") -> OutboxMessage:
    msg = outbox_repo.enqueue_template(
        db, recipient=TO, template_name="aviso_audiencias_resumo_v1",
        params=["Cond. Teste", "01/12/2026", "3"], ref_type=ref_type, ref_id="42", now=NOW,
    )
    msg.status, msg.wamid, msg.sent_at = OutboxStatus.SENT, wamid, NOW
    db.commit()
    return msg


def _button(wamid: str = "wamid.IN", context: str | None = "wamid.OUT") -> MessageEvent:
    return MessageEvent(wamid=wamid, sender=TO, kind="button", timestamp=NOW,
                        text="Ver detalhes", button_payload="Ver detalhes", context_wamid=context)


# ── Status ────────────────────────────────────────────────────────────────────

def test_status_avanca_mensagem_do_outbox(db):
    msg = _sent_message(db)
    batch = WebhookBatch(statuses=[StatusEvent("wamid.OUT", "delivered", NOW)])
    summary = WebhookHandler().handle(db, batch, now=NOW)

    db.refresh(msg)
    assert msg.status == OutboxStatus.DELIVERED and msg.delivered_at == NOW
    assert summary.statuses_applied == 1


def test_status_de_wamid_desconhecido_e_contado_sem_erro(db):
    summary = WebhookHandler().handle(db, WebhookBatch(statuses=[StatusEvent("wamid.X", "read", NOW)]), now=NOW)
    assert summary.statuses_unknown == 1


def test_reentrega_do_mesmo_status_e_idempotente(db):
    msg = _sent_message(db)
    later = NOW + datetime.timedelta(minutes=5)
    handler = WebhookHandler()
    handler.handle(db, WebhookBatch(statuses=[StatusEvent("wamid.OUT", "read", NOW)]), now=NOW)
    handler.handle(db, WebhookBatch(statuses=[StatusEvent("wamid.OUT", "read", later)]), now=later)
    db.refresh(msg)
    assert msg.status == OutboxStatus.READ and msg.read_at == NOW  # o primeiro timestamp vale


# ── Mensagens recebidas ───────────────────────────────────────────────────────

def test_texto_recebido_e_gravado(db):
    event = MessageEvent(wamid="wamid.IN", sender=TO, kind="text", timestamp=NOW,
                         sender_name="Maria", text="Bom dia")
    summary = WebhookHandler().handle(db, WebhookBatch(messages=[event]), now=NOW)

    row = db.query(InboundMessage).one()
    assert (row.sender, row.sender_name, row.text, row.outbox_message_id) == (TO, "Maria", "Bom dia", None)
    assert row.received_at == NOW
    assert summary.messages_stored == 1
    assert inbound_repo.last_inbound_at(db, TO) == NOW


def test_mensagem_reentregue_nao_duplica(db):
    event = MessageEvent(wamid="wamid.IN", sender=TO, kind="text", timestamp=NOW, text="oi")
    handler = WebhookHandler()
    handler.handle(db, WebhookBatch(messages=[event]), now=NOW)
    summary = handler.handle(db, WebhookBatch(messages=[event]), now=NOW)
    assert db.query(InboundMessage).count() == 1
    assert summary.messages_duplicated == 1


def test_clique_e_ligado_a_mensagem_do_outbox(db):
    msg = _sent_message(db)
    WebhookHandler().handle(db, WebhookBatch(messages=[_button()]), now=NOW)
    row = db.query(InboundMessage).one()
    assert row.outbox_message_id == msg.id
    assert row.handled_at is None  # nenhum handler registrado


def test_clique_chama_handler_do_ref_type(db):
    _sent_message(db)
    calls = []

    def on_hearing(db_, inbound, original):
        calls.append((inbound.text, original.ref_id))
        outbox_repo.enqueue_text(db_, recipient=inbound.sender, text="Lista completa...",
                                 dedupe_key=f"detalhes:{inbound.wamid}", now=NOW)

    summary = WebhookHandler({"hearing_summary": on_hearing}).handle(
        db, WebhookBatch(messages=[_button()]), now=NOW
    )

    assert calls == [("Ver detalhes", "42")]
    assert summary.buttons_handled == 1
    assert db.query(InboundMessage).one().handled_at == NOW
    reply = db.query(OutboxMessage).filter(OutboxMessage.kind == "text").one()
    assert reply.status == OutboxStatus.PENDING and reply.recipient == TO


def test_handler_com_erro_nao_perde_mensagem_nem_status(db):
    msg = _sent_message(db)

    def broken(db_, inbound, original):
        outbox_repo.enqueue_text(db_, recipient=TO, text="parcial", now=NOW)
        raise RuntimeError("bug no produtor")

    batch = WebhookBatch(statuses=[StatusEvent("wamid.OUT", "read", NOW)], messages=[_button()])
    summary = WebhookHandler({"hearing_summary": broken}).handle(db, batch, now=NOW)

    db.refresh(msg)
    assert msg.status == OutboxStatus.READ
    row = db.query(InboundMessage).one()
    assert row.handled_at is None
    assert summary.buttons_handled == 0
    # o que o handler escreveu antes de falhar foi desfeito (savepoint)
    assert db.query(OutboxMessage).filter(OutboxMessage.kind == "text").count() == 0


def test_clique_sem_contexto_conhecido_so_grava(db):
    calls = []
    handler = WebhookHandler({"hearing_summary": lambda *a: calls.append(a)})
    handler.handle(db, WebhookBatch(messages=[_button(context="wamid.DESCONHECIDO")]), now=NOW)
    assert calls == []
    assert db.query(InboundMessage).one().outbox_message_id is None
