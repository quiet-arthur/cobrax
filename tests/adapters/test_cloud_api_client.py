"""
test_cloud_api_client.py — Testes unitários para CloudAPIClient.

O HTTP é interceptado com httpx.MockTransport (a lib `responses` só intercepta
`requests`, não `httpx`) — nenhuma chamada sai para a Graph API real.

Valida:
  - check_connection (sucesso e token inválido)
  - send_template (payload correto, message_id, erro da Graph API)
  - send_text fora da janela de 24h (erro 131047)
  - mark_as_read
  - timeout
"""
import json
from collections.abc import Callable

import httpx

from src.adapters.cloud_api_client import CloudAPIClient


PHONE_ID = "1234567890"
BASE = f"https://graph.facebook.com/v23.0/{PHONE_ID}"
TO = "5567999999999"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[CloudAPIClient, list[httpx.Request]]:
    """Cria um cliente cujo transporte grava as requisições e responde via handler."""
    calls: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = CloudAPIClient("fake-token", PHONE_ID, transport=httpx.MockTransport(_record))
    return client, calls


def graph_error(status: int, message: str, code: int, details: str | None = None) -> httpx.Response:
    err: dict = {"message": message, "code": code}
    if details:
        err["error_data"] = {"details": details}
    return httpx.Response(status, json={"error": err})


def test_check_connection_ok():
    client, calls = make_client(
        lambda r: httpx.Response(200, json={"display_phone_number": "+55 67 9999-9999", "id": PHONE_ID})
    )
    assert client.check_connection() is True
    assert str(calls[0].url).startswith(BASE)
    assert calls[0].headers["Authorization"] == "Bearer fake-token"


def test_check_connection_token_invalido():
    client, _ = make_client(lambda r: graph_error(401, "Invalid OAuth access token", 190))
    assert client.check_connection() is False


def test_send_template_payload_e_message_id():
    client, calls = make_client(lambda r: httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]}))

    result = client.send_template(TO, "lembrete_audiencia_v1", body_params=["Maria", "12/12/2026"])

    assert result.success is True
    assert result.message_id == "wamid.ABC"
    assert str(calls[0].url) == f"{BASE}/messages"
    body = json.loads(calls[0].content)
    assert body["to"] == TO
    assert body["type"] == "template"
    assert body["template"]["name"] == "lembrete_audiencia_v1"
    assert body["template"]["language"] == {"code": "pt_BR"}
    params = body["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["Maria", "12/12/2026"]


def test_send_template_sem_parametros_omite_components():
    client, calls = make_client(lambda r: httpx.Response(200, json={"messages": [{"id": "wamid.X"}]}))
    client.send_template(TO, "hello_world", language="en_US")
    body = json.loads(calls[0].content)
    assert "components" not in body["template"]
    assert body["template"]["language"] == {"code": "en_US"}


def test_send_template_erro_graph():
    client, _ = make_client(lambda r: graph_error(400, "Template name does not exist", 132001))
    result = client.send_template(TO, "inexistente")
    assert result.success is False
    assert result.error_code == 132001
    assert "Template name does not exist" in result.error


def test_send_text_fora_da_janela_24h():
    client, _ = make_client(
        lambda r: graph_error(400, "Re-engagement message", 131047, "More than 24 hours have passed")
    )
    result = client.send_text(TO, "Olá")
    assert result.success is False
    assert result.error_code == 131047
    assert "24 hours" in result.error


def test_send_timeout():
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client, _ = make_client(_timeout)
    result = client.send_text(TO, "Olá")
    assert result.success is False
    assert result.error == "Timeout na requisição"


def test_mark_as_read():
    client, calls = make_client(lambda r: httpx.Response(200, json={"success": True}))
    assert client.mark_as_read("wamid.IN") is True
    body = json.loads(calls[0].content)
    assert body == {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.IN"}
