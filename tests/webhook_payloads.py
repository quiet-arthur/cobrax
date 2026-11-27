"""
Construtores de payloads no formato real do webhook da Cloud API, com dados
fictícios. Compartilhados pelos testes do parser, do handler e da API.
"""
import hashlib
import hmac
import json

PHONE_NUMBER_ID = "111111111111111"
CONTACT = "5567999999999"
TS = "1795942800"  # 2026-11-26 aprox.


def envelope(value: dict, *, phone_number_id: str = PHONE_NUMBER_ID) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "222222222222222",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "556799999999", "phone_number_id": phone_number_id},
                    **value,
                },
            }],
        }],
    }


def status(wamid: str, state: str, *, ts: str = TS, errors: list | None = None) -> dict:
    raw = {"id": wamid, "status": state, "timestamp": ts, "recipient_id": CONTACT}
    if errors:
        raw["errors"] = errors
    return raw


def text_message(wamid: str, body: str, *, ts: str = TS) -> dict:
    return {"from": CONTACT, "id": wamid, "timestamp": ts, "type": "text", "text": {"body": body}}


def button_message(wamid: str, label: str, *, context_wamid: str, ts: str = TS) -> dict:
    return {
        "context": {"from": "556799999999", "id": context_wamid},
        "from": CONTACT, "id": wamid, "timestamp": ts, "type": "button",
        "button": {"payload": label, "text": label},
    }


def contacts(name: str = "Síndico Teste") -> list[dict]:
    return [{"profile": {"name": name}, "wa_id": CONTACT}]


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def dumps(payload: dict) -> bytes:
    return json.dumps(payload).encode()
