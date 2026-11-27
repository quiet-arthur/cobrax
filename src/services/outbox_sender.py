"""
OutboxSender
------------
Único componente que envia mensagens do outbox para a WhatsApp Cloud API, e
único que interpreta os status de entrega devolvidos pelo webhook.

Fluxo de um envio:
  1. recupera mensagens presas em 'sending' (processo caiu no meio do envio);
  2. busca as mensagens 'pending' já vencidas;
  3. para cada uma: grava 'sending' + COMMIT, só então chama a API;
  4. grava o resultado (sent / retry agendado / failed) + COMMIT.

Classificação de erros da Graph API:
  - autenticação (token inválido/expirado) → interrompe o lote inteiro sem
    gastar tentativas: todas as mensagens falhariam pelo mesmo motivo;
  - transitório (timeout, 5xx, rate limit) → nova tentativa com backoff
    exponencial, até MAX_ATTEMPTS;
  - permanente (número inválido, template inexistente, fora da janela 24h...)
    → failed imediatamente; tentar de novo não mudaria o resultado.
"""
import datetime
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.adapters.cloud_api_client import CloudAPIClient, CloudSendResult
from src.domain.models import OutboxMessage, OutboxStatus
from src.repositories import outbox as outbox_repo


logger = logging.getLogger(__name__)

# ── Regras de envio ───────────────────────────────────────────────────────────
MAX_ATTEMPTS: int = 5
BACKOFF_BASE: datetime.timedelta = datetime.timedelta(minutes=1)   # 1, 2, 4, 8 min...
STALE_SENDING_AFTER: datetime.timedelta = datetime.timedelta(minutes=10)

# Códigos da Graph API
AUTH_ERROR_CODES: frozenset[int] = frozenset({190})                # token inválido/expirado
TRANSIENT_ERROR_CODES: frozenset[int] = frozenset({
    1,        # erro desconhecido da API
    2,        # serviço temporariamente indisponível
    4,        # limite de chamadas do app
    80007,    # limite de chamadas da conta
    130429,   # limite de throughput
    131000,   # "something went wrong"
    131056,   # muitas mensagens para o mesmo destinatário em pouco tempo
    133004,   # servidor temporariamente indisponível
})

# Ordem do ciclo de vida: status de webhook só avançam, nunca regridem
# (a Meta pode entregar 'read' antes de 'delivered').
_STATUS_RANK: dict[str, int] = {
    OutboxStatus.PENDING: 0,
    OutboxStatus.SENDING: 1,
    OutboxStatus.SENT: 2,
    OutboxStatus.DELIVERED: 3,
    OutboxStatus.READ: 4,
}


@dataclass
class SendSummary:
    sent: int = 0
    retried: int = 0
    failed: int = 0
    recovered: int = 0
    halted: bool = False
    halt_reason: str | None = field(default=None)


class OutboxSender:
    def __init__(self, client: CloudAPIClient):
        self.client = client

    def process_due(
        self,
        db: Session,
        *,
        limit: int = 50,
        now: datetime.datetime | None = None,
    ) -> SendSummary:
        """Envia um lote de mensagens vencidas do outbox."""
        now = now or datetime.datetime.now()
        summary = SendSummary()

        summary.recovered = outbox_repo.recover_stale_sending(
            db, now=now, older_than=STALE_SENDING_AFTER
        )
        db.commit()

        for msg in outbox_repo.claim_due(db, now=now, limit=limit):
            # 1) Reserva: gravada ANTES de chamar a API
            msg.status = OutboxStatus.SENDING
            msg.sending_at = now
            msg.attempts += 1
            db.commit()

            # 2) Envio
            result = self._dispatch(msg)

            # 3) Resultado
            if result.success:
                msg.status = OutboxStatus.SENT
                msg.wamid = result.message_id
                msg.sent_at = now
                msg.last_error = None
                msg.last_error_code = None
                summary.sent += 1
            elif _is_auth_error(result):
                # Não é culpa desta mensagem: devolve à fila sem consumir tentativa
                msg.status = OutboxStatus.PENDING
                msg.attempts -= 1
                msg.last_error = result.error
                msg.last_error_code = result.error_code
                db.commit()
                summary.halted = True
                summary.halt_reason = result.error
                logger.error(
                    f"[Outbox] Falha de autenticação na Cloud API — lote interrompido. "
                    f"Verifique WHATSAPP_ACCESS_TOKEN. Detalhe: {result.error}"
                )
                break
            elif _is_transient(result) and msg.attempts < MAX_ATTEMPTS:
                msg.status = OutboxStatus.PENDING
                msg.next_attempt_at = now + BACKOFF_BASE * (2 ** (msg.attempts - 1))
                msg.last_error = result.error
                msg.last_error_code = result.error_code
                summary.retried += 1
                logger.warning(
                    f"[Outbox] Mensagem {msg.id}: erro transitório "
                    f"(tentativa {msg.attempts}/{MAX_ATTEMPTS}), nova tentativa às "
                    f"{msg.next_attempt_at:%H:%M}. {result.error}"
                )
            else:
                msg.status = OutboxStatus.FAILED
                msg.failed_at = now
                msg.last_error = result.error
                msg.last_error_code = result.error_code
                summary.failed += 1
                logger.error(f"[Outbox] Mensagem {msg.id} falhou: {result.error}")
            db.commit()

        logger.info(
            f"[Outbox] Lote: {summary.sent} enviadas, {summary.retried} reagendadas, "
            f"{summary.failed} falharam, {summary.recovered} recuperadas"
            + (" — INTERROMPIDO" if summary.halted else "")
        )
        return summary

    def _dispatch(self, msg: OutboxMessage) -> CloudSendResult:
        if msg.kind == "template":
            return self.client.send_template(
                msg.recipient,
                msg.template_name,
                body_params=msg.params or None,
                language=msg.language or "pt_BR",
            )
        if msg.kind == "text":
            return self.client.send_text(msg.recipient, msg.text)
        return CloudSendResult(
            success=False, phone=msg.recipient, error=f"Tipo de mensagem desconhecido: {msg.kind}"
        )


def apply_status_update(
    db: Session,
    *,
    wamid: str,
    status: str,
    timestamp: datetime.datetime,
    error_code: int | None = None,
    error_message: str | None = None,
) -> bool:
    """
    Aplica um status de entrega vindo do webhook da Meta (sent / delivered /
    read / failed). Não faz commit.

    Regras:
      - status só avançam (read nunca volta para delivered);
      - 'failed' só é aceito se a mensagem ainda não foi lida;
      - cada timestamp é gravado uma única vez (o primeiro recebido vale).

    Returns:
        False se o wamid não pertence a nenhuma mensagem do outbox.
    """
    msg = outbox_repo.get_by_wamid(db, wamid)
    if msg is None:
        logger.warning(f"[Outbox] Status '{status}' para wamid desconhecido: {wamid}")
        return False

    if status == OutboxStatus.FAILED:
        if msg.status != OutboxStatus.READ:
            msg.status = OutboxStatus.FAILED
            msg.failed_at = msg.failed_at or timestamp
            msg.last_error = error_message
            msg.last_error_code = error_code
        return True

    if status not in _STATUS_RANK:
        logger.warning(f"[Outbox] Status de webhook ignorado: '{status}' ({wamid})")
        return True

    timestamp_field = f"{status}_at"
    if getattr(msg, timestamp_field) is None:
        setattr(msg, timestamp_field, timestamp)

    current_rank = _STATUS_RANK.get(msg.status, -1)
    if msg.status != OutboxStatus.FAILED and _STATUS_RANK[status] > current_rank:
        msg.status = status
    return True


# ── Private ───────────────────────────────────────────────────────────────────

def _is_auth_error(result: CloudSendResult) -> bool:
    return result.status_code == 401 or result.error_code in AUTH_ERROR_CODES


def _is_transient(result: CloudSendResult) -> bool:
    if result.status_code is None:            # timeout / erro de rede: sem resposta
        return True
    if result.status_code == 429 or result.status_code >= 500:
        return True
    return result.error_code in TRANSIENT_ERROR_CODES
