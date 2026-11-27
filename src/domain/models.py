from sqlalchemy import JSON, Column, Integer, String, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from src.repositories.database import Base

class Unit(Base):
    __tablename__ = "units"

    id = Column(Integer, primary_key=True, index=True)
    condominium_id = Column(String, index=True, nullable=False)
    adm = Column(String, nullable=False, default="alpha")  # "alpha" | "expresso"
    unit_name = Column(String, nullable=False)
    owner_name = Column(String, index=True)
    cpf_cnpj = Column(String)
    phones = Column(String) # JSON or comma separated
    emails = Column(String)
    do_not_notify = Column(Boolean, default=False)
    last_notified_at = Column(DateTime, nullable=True)  # notificação é por unidade

    debts = relationship("Debt", back_populates="unit")
    notification_logs = relationship("NotificationLog", back_populates="unit")

class Debt(Base):
    __tablename__ = "debts"

    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    doc_number = Column(String, unique=True, index=True, nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(String)

    unit = relationship("Unit", back_populates="debts")


class NotificationLog(Base):
    """
    Registro de auditoria de cada tentativa de notificação.
    Gerado para TODOS os desfechos: 'sent', 'failed' e 'skipped'.
    """
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    sent_at = Column(DateTime, nullable=False)              # timestamp do disparo
    channel = Column(String, nullable=False)                # "whatsapp" | "email" | "sms"
    template_name = Column(String, nullable=False)          # ex: "overdue_90d_whatsapp_v1"
    destination = Column(String, nullable=True)             # número ou e-mail de destino
    status = Column(String, nullable=False)                 # "sent" | "failed" | "skipped"
    error_message = Column(Text, nullable=True)             # detalhe do erro, se houver

    # Dados da unidade (desnormalizados para sobreviver a eventuais deletes)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    unit_name = Column(String)
    owner_name = Column(String)
    condominium_id = Column(String)

    unit = relationship("Unit", back_populates="notification_logs")


# ── Outbox de mensagens ───────────────────────────────────────────────────────

class OutboxStatus:
    """
    Ciclo de vida de uma mensagem no outbox.

        pending ──▶ sending ──▶ sent ──▶ delivered ──▶ read
           ▲           │          │          │
           └─(retry)───┘          └──────────┴──▶ failed

    - pending:   aguardando envio (novo ou aguardando nova tentativa).
    - sending:   reservada pelo sender; gravada ANTES da chamada à API, para
                 que uma queda no meio do envio fique visível.
    - sent:      aceita pela API da Meta (ainda NÃO significa entregue).
    - delivered / read: confirmados depois, via webhook.
    - failed:    erro permanente, tentativas esgotadas ou falha de entrega
                 reportada pelo webhook.
    """
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class OutboxMessage(Base):
    """
    Fila persistente de mensagens a enviar (padrão "transactional outbox").

    Produtores (cobrança, audiências, ...) apenas INSEREM linhas aqui; o
    OutboxSender é o único que fala com a API do WhatsApp. Cada linha guarda
    o ciclo de vida completo da mensagem, servindo também de trilha de
    auditoria (substitui, para o canal Cloud API, o NotificationLog).
    """
    __tablename__ = "outbox_messages"

    id = Column(Integer, primary_key=True, index=True)

    # O quê / para quem
    channel = Column(String, nullable=False, default="whatsapp")
    recipient = Column(String, nullable=False)              # E.164 sem '+', ex: 5567999999999
    kind = Column(String, nullable=False)                   # "template" | "text"
    template_name = Column(String, nullable=True)           # quando kind="template"
    language = Column(String, nullable=True)                # ex: "pt_BR"
    params = Column(JSON, nullable=True)                    # variáveis do template {{1}}, {{2}}...
    text = Column(Text, nullable=True)                      # quando kind="text"

    # Idempotência: a mesma notificação não entra duas vezes na fila
    dedupe_key = Column(String, unique=True, nullable=True)

    # Referência solta à entidade que originou a mensagem (auditoria)
    ref_type = Column(String, nullable=True)                # ex: "unit", "hearing_summary"
    ref_id = Column(String, nullable=True)

    # Estado e tentativas
    status = Column(String, nullable=False, default=OutboxStatus.PENDING, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    last_error_code = Column(Integer, nullable=True)

    # wamid devolvido pela Meta: é por ele que o webhook encontra esta linha
    wamid = Column(String, unique=True, nullable=True, index=True)

    # Linha do tempo (prova de envio/entrega)
    created_at = Column(DateTime, nullable=False)
    sending_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)


class InboundMessage(Base):
    """
    Mensagem recebida de um contato pelo webhook da Cloud API.

    Serve para três coisas:
      - auditoria (o que o contato respondeu e quando);
      - janela de 24h: texto livre só pode ser enviado até 24h depois da
        última mensagem do contato (ver last_inbound_at no repositório);
      - cliques em botões de template: `context_wamid` é o wamid da mensagem
        que enviamos, então `outbox_message_id` liga o clique ao aviso que o
        originou (ex: "Ver detalhes" de um resumo de audiências).

    A Meta reentrega webhooks em caso de falha/timeout: `wamid` é único para
    que uma reentrega não duplique a linha.
    """
    __tablename__ = "inbound_messages"

    id = Column(Integer, primary_key=True, index=True)
    wamid = Column(String, unique=True, nullable=False, index=True)
    sender = Column(String, nullable=False, index=True)     # wa_id do contato, ex: 5567999999999
    sender_name = Column(String, nullable=True)             # nome do perfil no WhatsApp
    kind = Column(String, nullable=False)                   # "text" | "button" | "interactive" | ...
    text = Column(Text, nullable=True)                      # corpo do texto ou rótulo do botão
    button_payload = Column(String, nullable=True)          # payload/id do botão clicado

    # Resposta a uma mensagem nossa (cliques em botões sempre trazem isso)
    context_wamid = Column(String, nullable=True, index=True)
    outbox_message_id = Column(Integer, ForeignKey("outbox_messages.id"), nullable=True)

    sent_at = Column(DateTime, nullable=False)              # timestamp informado pela Meta
    received_at = Column(DateTime, nullable=False)          # quando o webhook chegou aqui
    handled_at = Column(DateTime, nullable=True)            # quando um handler tratou o clique
