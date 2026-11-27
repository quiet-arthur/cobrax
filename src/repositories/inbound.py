"""
Acesso a dados das mensagens recebidas (webhook da Cloud API).

Como em `outbox.py`, nenhuma função faz commit: quem chama controla a
transação.
"""
import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.domain.models import InboundMessage


def exists(db: Session, wamid: str) -> bool:
    """True se a mensagem já foi registrada (reentrega do webhook)."""
    return (
        db.query(InboundMessage.id).filter(InboundMessage.wamid == wamid).first()
        is not None
    )


def add(db: Session, message: InboundMessage) -> InboundMessage:
    db.add(message)
    db.flush()
    return message


def last_inbound_at(db: Session, sender: str) -> datetime.datetime | None:
    """
    Momento da última mensagem do contato. Texto livre só é aceito pela Meta
    até 24h depois disso; fora da janela, só template.
    """
    return (
        db.query(func.max(InboundMessage.sent_at))
        .filter(InboundMessage.sender == sender)
        .scalar()
    )
