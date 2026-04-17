"""
EvolutionAPIClient
------------------
Wrapper HTTP para a Evolution API v2 (auto-hospedada).

Endpoints utilizados:
  GET  /instance/connectionState/{instance}  → verifica conexão WhatsApp
  POST /message/sendText/{instance}          → envia mensagem de texto
  PUT  /chat/archiveChat/{instance}          → arquiva chat após envio
"""
import logging
import httpx
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    success: bool
    phone: str
    error: str | None = field(default=None)


class EvolutionAPIClient:
    """
    Cliente HTTP para a Evolution API v2.

    Usage:
        with EvolutionAPIClient(base_url, api_key, instance) as client:
            if client.check_connection():
                result = client.send_text("5567999999999", "Olá!")
                if result.success:
                    client.archive_chat("5567999999999", result._msg_key)
    """

    def __init__(self, base_url: str, api_key: str, instance: str):
        self.base_url = base_url.rstrip("/")
        self.instance = instance
        self.headers = {
            "Content-Type": "application/json",
            "apikey": api_key,
        }
        self._client = httpx.Client(timeout=15.0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._client.close()

    # ── Public methods ────────────────────────────────────────────────────────

    def check_connection(self) -> bool:
        """
        Verifica se a instância WhatsApp está conectada.

        Returns:
            True se state == 'open', False caso contrário.
        """
        url = f"{self.base_url}/instance/connectionState/{self.instance}"
        try:
            resp = self._client.get(url, headers=self.headers)
            resp.raise_for_status()
            state = resp.json().get("instance", {}).get("state", "")
            connected = state == "open"
            if not connected:
                logger.warning(
                    f"Instância '{self.instance}' não conectada. Estado: '{state}'"
                )
            return connected
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Erro HTTP ao verificar conexão ({e.response.status_code}): {e.response.text}"
            )
            return False
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar conexão: {e}")
            return False

    def send_text(self, phone: str, text: str, delay_ms: int = 1500) -> SendResult:
        """
        Envia uma mensagem de texto via WhatsApp.

        Args:
            phone:    Número no formato E.164 sem '+' (ex: '5567999999999').
            text:     Corpo da mensagem. Suporta formatação WhatsApp (*negrito*, _itálico_).
            delay_ms: Delay simulado de digitação em milissegundos.

        Returns:
            SendResult com success=True em caso de entrega aceita pela API.
            Em sucesso, result._msg_key contém a chave da mensagem (para arquivamento).
        """
        url = f"{self.base_url}/message/sendText/{self.instance}"
        payload = {
            "number": phone,
            "text": text,
            "delay": delay_ms,
        }
        try:
            resp = self._client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            msg_key = data.get("key", {})
            logger.info(f"[Evolution] Mensagem aceita pela API para {phone}")
            result = SendResult(success=True, phone=phone)
            result._msg_key = msg_key  # chave para arquivamento posterior
            return result
        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"[Evolution] Falha ao enviar para {phone}: {msg}")
            return SendResult(success=False, phone=phone, error=msg)
        except httpx.TimeoutException:
            msg = "Timeout na requisição"
            logger.error(f"[Evolution] Timeout ao enviar para {phone}")
            return SendResult(success=False, phone=phone, error=msg)
        except Exception as e:
            logger.error(f"[Evolution] Erro inesperado ao enviar para {phone}: {e}")
            return SendResult(success=False, phone=phone, error=str(e))

    def archive_chat(self, phone: str, msg_key: dict) -> bool:
        """
        Arquiva o chat de um contato após o envio da notificação.

        A conversa arquivada sai do chat principal e só reaparece
        quando o contato responder — mantendo a caixa de entrada limpa.

        Args:
            phone:   Número no formato E.164 sem '+' (ex: '5567999999999').
            msg_key: Chave da última mensagem retornada por send_text()._msg_key.
                     Formato esperado: {"remoteJid": "...", "fromMe": True, "id": "..."}

        Returns:
            True em caso de sucesso, False caso contrário (falha não-crítica).
        """
        url = f"{self.base_url}/chat/archiveChat/{self.instance}"
        payload = {
            "lastMessage": {"key": msg_key},
            "archive": True,
        }
        try:
            resp = self._client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            logger.info(f"[Evolution] Chat de {phone} arquivado.")
            return True
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"[Evolution] Falha ao arquivar chat de {phone} "
                f"({e.response.status_code}): {e.response.text}"
            )
            return False
        except Exception as e:
            logger.warning(f"[Evolution] Erro ao arquivar chat de {phone}: {e}")
            return False
