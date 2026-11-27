"""
CloudAPIClient
--------------
Wrapper HTTP para a WhatsApp Cloud API oficial da Meta (Graph API).

Endpoints utilizados:
  GET  /{phone_number_id}           → verifica se o número está ativo/registrado
  POST /{phone_number_id}/messages  → envia template, texto livre ou marca como lida

Regras da plataforma que afetam o uso deste cliente:
  - A PRIMEIRA mensagem para um contato (ou qualquer mensagem após 24h sem
    resposta dele) precisa ser um TEMPLATE aprovado pela Meta → send_template().
  - Texto livre (send_text) só é aceito dentro da janela de 24h aberta quando
    o contato envia uma mensagem para o número da empresa.
  - "Aceito pela API" NÃO significa entregue: a confirmação de entrega/leitura
    chega depois, via webhook (statuses), identificada pelo message_id.
"""
import logging
from dataclasses import dataclass, field

import httpx


logger = logging.getLogger(__name__)

DEFAULT_API_VERSION: str = "v23.0"
DEFAULT_BASE_URL: str = "https://graph.facebook.com"
DEFAULT_LANGUAGE: str = "pt_BR"


@dataclass
class CloudSendResult:
    success: bool
    phone: str
    message_id: str | None = field(default=None)   # wamid.* — chave para casar com webhooks
    error: str | None = field(default=None)
    error_code: int | None = field(default=None)   # código de erro da Graph API (ex: 131047)
    status_code: int | None = field(default=None)  # HTTP status da resposta (None = sem resposta, ex: timeout)


class CloudAPIClient:
    """
    Cliente HTTP para a WhatsApp Cloud API.

    Usage:
        with CloudAPIClient(access_token, phone_number_id) as client:
            if client.check_connection():
                result = client.send_template(
                    "5567999999999", "lembrete_audiencia_v1",
                    body_params=["Maria", "12/12/2026", "14:00"],
                )
                if result.success:
                    print(result.message_id)
    """

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = DEFAULT_API_VERSION,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,  # injetável nos testes (httpx.MockTransport)
    ):
        self.phone_number_id = phone_number_id
        self.base_url = f"{base_url.rstrip('/')}/{api_version}"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._client.close()

    # ── Public methods ────────────────────────────────────────────────────────

    def check_connection(self) -> bool:
        """
        Verifica se o token é válido e o número existe/está acessível.

        Returns:
            True se a Graph API responder 200 para o phone_number_id.
        """
        url = f"{self.base_url}/{self.phone_number_id}"
        params = {"fields": "display_phone_number,verified_name,quality_rating"}
        try:
            resp = self._client.get(url, headers=self.headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                f"[CloudAPI] Número ativo: {data.get('display_phone_number')} "
                f"({data.get('verified_name')}) | qualidade: {data.get('quality_rating')}"
            )
            return True
        except httpx.HTTPStatusError as e:
            message, code = _parse_graph_error(e.response)
            logger.error(f"[CloudAPI] Falha ao verificar número (código {code}): {message}")
            return False
        except Exception as e:
            logger.error(f"[CloudAPI] Erro inesperado ao verificar número: {e}")
            return False

    def send_template(
        self,
        phone: str,
        template_name: str,
        body_params: list[str] | None = None,
        language: str = DEFAULT_LANGUAGE,
    ) -> CloudSendResult:
        """
        Envia uma mensagem de template aprovado (obrigatório para iniciar conversa).

        Args:
            phone:         Número E.164 sem '+' (ex: '5567999999999').
            template_name: Nome do template aprovado no WhatsApp Manager.
            body_params:   Valores das variáveis {{1}}, {{2}}... do corpo, em ordem.
            language:      Código do idioma do template (padrão 'pt_BR').
        """
        template: dict = {"name": template_name, "language": {"code": language}}
        if body_params:
            template["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in body_params],
                }
            ]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "template",
            "template": template,
        }
        return self._send(phone, payload)

    def send_text(self, phone: str, text: str, preview_url: bool = False) -> CloudSendResult:
        """
        Envia texto livre. Só funciona dentro da janela de 24h após o contato
        ter enviado uma mensagem — fora dela a API retorna erro 131047.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"body": text, "preview_url": preview_url},
        }
        return self._send(phone, payload)

    def mark_as_read(self, message_id: str) -> bool:
        """
        Marca uma mensagem RECEBIDA (wamid vindo do webhook) como lida —
        o contato vê os dois tiques azuis. Falha não-crítica.
        """
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        try:
            resp = self._client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            message, code = _parse_graph_error(e.response)
            logger.warning(f"[CloudAPI] Falha ao marcar {message_id} como lida ({code}): {message}")
            return False
        except Exception as e:
            logger.warning(f"[CloudAPI] Erro ao marcar {message_id} como lida: {e}")
            return False

    # ── Private ───────────────────────────────────────────────────────────────

    def _send(self, phone: str, payload: dict) -> CloudSendResult:
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        try:
            resp = self._client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            messages = resp.json().get("messages") or [{}]
            message_id = messages[0].get("id")
            logger.info(f"[CloudAPI] Mensagem aceita pela API para {phone} ({message_id})")
            return CloudSendResult(success=True, phone=phone, message_id=message_id)
        except httpx.HTTPStatusError as e:
            message, code = _parse_graph_error(e.response)
            msg = f"HTTP {e.response.status_code} (código {code}): {message}"
            logger.error(f"[CloudAPI] Falha ao enviar para {phone}: {msg}")
            return CloudSendResult(
                success=False, phone=phone, error=msg, error_code=code,
                status_code=e.response.status_code,
            )
        except httpx.TimeoutException:
            logger.error(f"[CloudAPI] Timeout ao enviar para {phone}")
            return CloudSendResult(success=False, phone=phone, error="Timeout na requisição")
        except Exception as e:
            logger.error(f"[CloudAPI] Erro inesperado ao enviar para {phone}: {e}")
            return CloudSendResult(success=False, phone=phone, error=str(e))


def _parse_graph_error(response: httpx.Response) -> tuple[str, int | None]:
    """Extrai (mensagem, código) do corpo de erro padrão da Graph API."""
    try:
        err = response.json().get("error", {})
        details = (err.get("error_data") or {}).get("details")
        message = err.get("message", response.text)
        if details:
            message = f"{message} — {details}"
        return message, err.get("code")
    except Exception:
        return response.text, None
