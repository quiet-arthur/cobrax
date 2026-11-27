"""
Acesso a dados do outbox de mensagens.

Toda leitura/escrita em `outbox_messages` passa por aqui — produtores e o
OutboxSender nunca montam queries por conta própria. As funções NÃO fazem
commit: quem chama controla a transação (isso permite, por exemplo, gravar
uma notificação de cobrança e o seu registro no outbox na MESMA transação).
"""
import datetime
import logging

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.domain.models import OutboxMessage, OutboxStatus


logger = logging.getLogger(__name__)


def enqueue_template(
    db: Session,
    *,
    recipient: str,
    template_name: str,
    params: list[str] | None = None,
    language: str = "pt_BR",
    dedupe_key: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    now: datetime.datetime | None = None,
) -> OutboxMessage | None:
    """
    Enfileira um template aprovado para envio.

    Returns:
        A mensagem criada, ou None se já existir uma com o mesmo dedupe_key
        (a mesma notificação não é enfileirada duas vezes).
    """
    return _enqueue(
        db,
        recipient=recipient,
        kind="template",
        template_name=template_name,
        language=language,
        params=params or [],
        dedupe_key=dedupe_key,
        ref_type=ref_type,
        ref_id=ref_id,
        now=now,
    )


def enqueue_text(
    db: Session,
    *,
    recipient: str,
    text: str,
    dedupe_key: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    now: datetime.datetime | None = None,
) -> OutboxMessage | None:
    """
    Enfileira texto livre. Atenção: a Meta só aceita texto livre dentro da
    janela de 24h aberta por uma mensagem do contato (fora dela → erro 131047).
    """
    return _enqueue(
        db,
        recipient=recipient,
        kind="text",
        text=text,
        dedupe_key=dedupe_key,
        ref_type=ref_type,
        ref_id=ref_id,
        now=now,
    )


def claim_due(
    db: Session,
    *,
    now: datetime.datetime,
    limit: int = 50,
) -> list[OutboxMessage]:
    """
    Retorna as mensagens prontas para envio: status pending e sem espera de
    retry pendente (next_attempt_at vazio ou já vencido), das mais antigas
    para as mais novas.
    """
    return (
        db.query(OutboxMessage)
        .filter(
            OutboxMessage.status == OutboxStatus.PENDING,
            or_(
                OutboxMessage.next_attempt_at.is_(None),
                OutboxMessage.next_attempt_at <= now,
            ),
        )
        .order_by(OutboxMessage.created_at, OutboxMessage.id)
        .limit(limit)
        .all()
    )


def recover_stale_sending(
    db: Session,
    *,
    now: datetime.datetime,
    older_than: datetime.timedelta,
) -> int:
    """
    Devolve para 'pending' mensagens presas em 'sending' há mais de
    `older_than` — sinal de que o processo caiu no meio de um envio.

    Escolha consciente ("at-least-once"): numa queda exatamente entre a API
    aceitar e o banco gravar, o destinatário pode receber a mensagem duas
    vezes. Preferimos um raro duplicado a perder um aviso de audiência.

    Returns:
        Quantidade de mensagens recuperadas.
    """
    stale = (
        db.query(OutboxMessage)
        .filter(
            OutboxMessage.status == OutboxStatus.SENDING,
            OutboxMessage.sending_at < now - older_than,
        )
        .all()
    )
    for msg in stale:
        logger.warning(
            f"[Outbox] Mensagem {msg.id} presa em 'sending' desde {msg.sending_at} — "
            f"voltando para a fila."
        )
        msg.status = OutboxStatus.PENDING
        msg.next_attempt_at = None
    return len(stale)


def get_by_wamid(db: Session, wamid: str) -> OutboxMessage | None:
    """Localiza a mensagem pelo id devolvido pela Meta (usado pelo webhook)."""
    return db.query(OutboxMessage).filter(OutboxMessage.wamid == wamid).one_or_none()


# ── Private ───────────────────────────────────────────────────────────────────

def _enqueue(
    db: Session,
    *,
    recipient: str,
    kind: str,
    dedupe_key: str | None,
    now: datetime.datetime | None,
    **fields,
) -> OutboxMessage | None:
    if dedupe_key is not None:
        existing = (
            db.query(OutboxMessage.id)
            .filter(OutboxMessage.dedupe_key == dedupe_key)
            .first()
        )
        if existing is not None:
            logger.info(f"[Outbox] Ignorado: dedupe_key '{dedupe_key}' já enfileirado.")
            return None

    msg = OutboxMessage(
        recipient=recipient,
        kind=kind,
        dedupe_key=dedupe_key,
        status=OutboxStatus.PENDING,
        attempts=0,
        created_at=now or datetime.datetime.now(),
        **fields,
    )
    db.add(msg)
    db.flush()  # atribui o id sem fechar a transação de quem chamou
    return msg
