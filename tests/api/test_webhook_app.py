"""
Testes HTTP do receptor do webhook (FastAPI TestClient + SQLite em memória).

StaticPool: o TestClient atende em outra thread; sem ele cada conexão nova
abriria um banco em memória vazio.
"""
import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.app import WebhookConfig, create_app
from src.domain.models import InboundMessage, OutboxStatus
from src.repositories import outbox as outbox_repo
from tests.webhook_payloads import (
    PHONE_NUMBER_ID, button_message, contacts, dumps, envelope, sign, status, text_message,
)

SECRET = "segredo-de-teste"
VERIFY = "token-de-verificacao"


@pytest.fixture()
def engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(engine):
    app = create_app(WebhookConfig(SECRET, VERIFY, PHONE_NUMBER_ID), engine)
    with TestClient(app) as c:  # o "with" roda o lifespan (create_all)
        yield c


def _post(client: TestClient, payload: dict, secret: str = SECRET):
    body = dumps(payload)
    return client.post("/webhook", content=body, headers={
        "Content-Type": "application/json", "X-Hub-Signature-256": sign(body, secret),
    })


# ── Handshake ─────────────────────────────────────────────────────────────────

def test_handshake_devolve_challenge(client):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                       "hub.challenge": "1158201444"})
    assert r.status_code == 200 and r.text == "1158201444"


def test_handshake_com_token_errado_e_recusado(client):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "errado",
                                       "hub.challenge": "1"})
    assert r.status_code == 403


def test_handshake_sem_token_configurado_e_recusado(engine):
    app = create_app(WebhookConfig(SECRET, ""), engine)
    with TestClient(app) as c:
        r = c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
    assert r.status_code == 403


# ── POST ──────────────────────────────────────────────────────────────────────

def test_post_sem_assinatura_valida_e_rejeitado(client, db):
    payload = envelope({"messages": [text_message("wamid.IN", "oi")]})
    assert _post(client, payload, secret="outro").status_code == 403
    r = client.post("/webhook", content=dumps(payload), headers={"Content-Type": "application/json"})
    assert r.status_code == 403
    assert db.query(InboundMessage).count() == 0


def test_post_assinado_com_json_invalido(client):
    body = b"nao e json"
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body, SECRET)})
    assert r.status_code == 400


def test_fluxo_completo_status_e_clique(client, db):
    now = datetime.datetime(2026, 11, 26, 9, 0)
    msg = outbox_repo.enqueue_template(db, recipient="5567999999999", template_name="t",
                                       ref_type="hearing_summary", now=now)
    msg.status, msg.wamid = OutboxStatus.SENT, "wamid.OUT"
    db.commit()

    r = _post(client, envelope({
        "contacts": contacts("Maria"),
        "statuses": [status("wamid.OUT", "delivered"), status("wamid.OUT", "read")],
        "messages": [button_message("wamid.IN", "Ver detalhes", context_wamid="wamid.OUT")],
    }))
    assert r.status_code == 200

    db.expire_all()
    db.refresh(msg)
    assert msg.status == OutboxStatus.READ
    inbound = db.query(InboundMessage).one()
    assert (inbound.sender_name, inbound.text, inbound.outbox_message_id) == ("Maria", "Ver detalhes", msg.id)

    # reentrega idêntica da Meta: 200 e nada duplicado
    assert _post(client, envelope({
        "messages": [button_message("wamid.IN", "Ver detalhes", context_wamid="wamid.OUT")],
    })).status_code == 200
    assert db.query(InboundMessage).count() == 1


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
