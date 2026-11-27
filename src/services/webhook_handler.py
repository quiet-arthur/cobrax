"""
WebhookHandler
--------------
Aplica os eventos do webhook da Cloud API ao banco:

  - StatusEvent  → apply_status_update (outbox_sender), que já garante que
                   status nunca regridem;
  - MessageEvent → grava uma InboundMessage (idempotente por wamid) e, se for
                   resposta a uma mensagem nossa, liga ao registro do outbox.

Cliques em botões (ex: "Ver detalhes" do resumo de audiências) são
despachados para um handler registrado pelo `ref_type` da mensagem do
outbox que originou o clique. Cada produtor (audiências, cobrança...)
registra o seu; sem handler, o clique fica gravado sem resposta
(`handled_at` vazio) e pode ser tratado depois.
"""
import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.adapters.whatsapp_webhook import MessageEvent, WebhookBatch
from src.domain.models import InboundMessage, OutboxMessage
from src.repositories import inbound as inbound_repo
from src.repositories import outbox as outbox_repo
from src.services.outbox_sender import apply_status_update


logger = logging.getLogger(__name__)

# (db, mensagem recebida, mensagem do outbox que foi respondida) → None.
# O handler NÃO envia nada direto: enfileira a resposta no outbox.
ButtonHandler = Callable[[Session, InboundMessage, OutboxMessage], None]

BUTTON_KINDS: frozenset[str] = frozenset({"button", "interactive"})


@dataclass
class WebhookSummary:
    statuses_applied: int = 0
    statuses_unknown: int = 0
    messages_stored: int = 0
    messages_duplicated: int = 0
    buttons_handled: int = 0


class WebhookHandler:
    def __init__(self, button_handlers: dict[str, ButtonHandler] | None = None):
        self.button_handlers: dict[str, ButtonHandler] = dict(button_handlers or {})

    def handle(
        self,
        db: Session,
        batch: WebhookBatch,
        *,
        now: datetime.datetime | None = None,
    ) -> WebhookSummary:
        """Aplica todos os eventos do lote numa única transação (faz commit)."""
        now = now or datetime.datetime.now()
        summary = WebhookSummary()

        for status in batch.statuses:
            known = apply_status_update(
                db,
                wamid=status.wamid,
                status=status.status,
                timestamp=status.timestamp,
                error_code=status.error_code,
                error_message=status.error_message,
            )
            if known:
                summary.statuses_applied += 1
            else:
                summary.statuses_unknown += 1

        for event in batch.messages:
            self._handle_message(db, event, now, summary)

        db.commit()
        return summary

    def _handle_message(
        self,
        db: Session,
        event: MessageEvent,
        now: datetime.datetime,
        summary: WebhookSummary,
    ) -> None:
        if inbound_repo.exists(db, event.wamid):
            summary.messages_duplicated += 1
            return

        original = outbox_repo.get_by_wamid(db, event.context_wamid) if event.context_wamid else None
        message = inbound_repo.add(db, InboundMessage(
            wamid=event.wamid,
            sender=event.sender,
            sender_name=event.sender_name,
            kind=event.kind,
            text=event.text,
            button_payload=event.button_payload,
            context_wamid=event.context_wamid,
            outbox_message_id=original.id if original else None,
            sent_at=event.timestamp,
            received_at=now,
        ))
        summary.messages_stored += 1
        logger.info(
            f"[Webhook] Mensagem de {event.sender} ({event.kind})"
            + (f": '{event.text}'" if event.text else "")
        )

        if event.kind not in BUTTON_KINDS or original is None:
            return

        handler = self.button_handlers.get(original.ref_type or "")
        if handler is None:
            logger.warning(
                f"[Webhook] Clique em '{event.text}' sem handler para "
                f"ref_type='{original.ref_type}' (outbox {original.id}). Gravado sem resposta."
            )
            return

        # Erro no handler não pode perder o registro da mensagem nem dos status:
        # isola a falha num savepoint e segue.
        try:
            with db.begin_nested():
                handler(db, message, original)
                message.handled_at = now
            summary.buttons_handled += 1
        except Exception:
            logger.exception(
                f"[Webhook] Handler de '{original.ref_type}' falhou para a mensagem {event.wamid}."
            )
