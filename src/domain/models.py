from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, ForeignKey, Text
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
