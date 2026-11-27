"""Testes do parser do webhook e da verificação de assinatura (sem banco)."""
import datetime

from src.adapters.whatsapp_webhook import parse_webhook, verify_signature
from tests.webhook_payloads import (
    CONTACT, PHONE_NUMBER_ID, TS, button_message, contacts, envelope, sign, status, text_message,
)

SECRET = "segredo-de-teste"


# ── Assinatura ────────────────────────────────────────────────────────────────

def test_assinatura_valida():
    body = b'{"a": 1}'
    assert verify_signature(body, sign(body, SECRET), SECRET)


def test_assinatura_de_corpo_alterado_e_rejeitada():
    body = b'{"a": 1}'
    assert not verify_signature(b'{"a": 2}', sign(body, SECRET), SECRET)


def test_assinatura_com_outro_segredo_e_rejeitada():
    body = b'{"a": 1}'
    assert not verify_signature(body, sign(body, "outro"), SECRET)


def test_sem_cabecalho_ou_sem_segredo_falha_fechado():
    body = b'{"a": 1}'
    assert not verify_signature(body, None, SECRET)
    assert not verify_signature(body, "abc123", SECRET)          # sem prefixo sha256=
    assert not verify_signature(body, sign(body, ""), "")        # segredo não configurado


# ── Status ────────────────────────────────────────────────────────────────────

def test_status_entregue():
    batch = parse_webhook(envelope({"statuses": [status("wamid.A", "delivered")]}))
    assert len(batch.statuses) == 1 and batch.messages == []
    ev = batch.statuses[0]
    assert ev.wamid == "wamid.A"
    assert ev.status == "delivered"
    assert ev.recipient == CONTACT
    assert ev.timestamp == datetime.datetime.fromtimestamp(int(TS))


def test_status_falha_traz_codigo_e_detalhe():
    errors = [{"code": 131026, "title": "Message undeliverable",
               "error_data": {"details": "Número sem WhatsApp"}}]
    ev = parse_webhook(envelope({"statuses": [status("wamid.A", "failed", errors=errors)]})).statuses[0]
    assert ev.error_code == 131026
    assert ev.error_message == "Número sem WhatsApp"


def test_status_incompleto_e_ignorado():
    batch = parse_webhook(envelope({"statuses": [{"id": "wamid.A", "status": "sent"}]}))  # sem timestamp
    assert batch.statuses == []


# ── Mensagens ─────────────────────────────────────────────────────────────────

def test_mensagem_de_texto_com_nome_do_contato():
    payload = envelope({"contacts": contacts("Maria"), "messages": [text_message("wamid.IN", "Bom dia")]})
    ev = parse_webhook(payload).messages[0]
    assert (ev.kind, ev.text, ev.sender, ev.sender_name) == ("text", "Bom dia", CONTACT, "Maria")
    assert ev.context_wamid is None


def test_clique_em_botao_de_template_traz_contexto():
    payload = envelope({"messages": [button_message("wamid.IN", "Ver detalhes", context_wamid="wamid.OUT")]})
    ev = parse_webhook(payload).messages[0]
    assert ev.kind == "button"
    assert ev.text == "Ver detalhes"
    assert ev.button_payload == "Ver detalhes"
    assert ev.context_wamid == "wamid.OUT"


def test_botao_de_mensagem_interativa():
    raw = {"from": CONTACT, "id": "wamid.IN", "timestamp": TS, "type": "interactive",
           "context": {"id": "wamid.OUT"},
           "interactive": {"type": "button_reply", "button_reply": {"id": "detalhes", "title": "Ver detalhes"}}}
    ev = parse_webhook(envelope({"messages": [raw]})).messages[0]
    assert (ev.kind, ev.text, ev.button_payload) == ("interactive", "Ver detalhes", "detalhes")


def test_tipo_desconhecido_e_gravado_sem_texto():
    raw = {"from": CONTACT, "id": "wamid.IN", "timestamp": TS, "type": "image", "image": {"id": "1"}}
    ev = parse_webhook(envelope({"messages": [raw]})).messages[0]
    assert ev.kind == "image" and ev.text is None


# ── Filtros e tolerância ──────────────────────────────────────────────────────

def test_evento_de_outro_numero_e_ignorado():
    payload = envelope({"statuses": [status("wamid.A", "sent")]}, phone_number_id="999")
    assert parse_webhook(payload, phone_number_id=PHONE_NUMBER_ID).statuses == []
    assert len(parse_webhook(payload).statuses) == 1  # sem filtro configurado, aceita


def test_objeto_ou_campo_inesperado_nao_quebra():
    assert parse_webhook({"object": "page"}).statuses == []
    assert parse_webhook([]).messages == []  # type: ignore[arg-type]
    payload = envelope({})
    payload["entry"][0]["changes"][0]["field"] = "account_update"
    assert parse_webhook(payload).statuses == []


def test_status_e_mensagens_no_mesmo_post():
    payload = envelope({
        "statuses": [status("wamid.A", "sent"), status("wamid.B", "read")],
        "messages": [text_message("wamid.IN", "oi")],
    })
    batch = parse_webhook(payload)
    assert [s.wamid for s in batch.statuses] == ["wamid.A", "wamid.B"]
    assert len(batch.messages) == 1
