from sqlalchemy import Column, Integer, String, Boolean, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from src.repositories.database import Base

class Unit(Base):
    __tablename__ = "units"

    id = Column(Integer, primary_key=True, index=True)
    condominium_id = Column(String, index=True, nullable=False)
    unit_name = Column(String, nullable=False)
    owner_name = Column(String, index=True)
    cpf_cnpj = Column(String)
    phones = Column(String) # JSON or comma separated
    emails = Column(String)
    do_not_notify = Column(Boolean, default=False)
    
    debts = relationship("Debt", back_populates="unit")

class Debt(Base):
    __tablename__ = "debts"

    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    doc_number = Column(String, unique=True, index=True, nullable=False)
    due_date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String)
    last_notified_at = Column(DateTime, nullable=True)

    unit = relationship("Unit", back_populates="debts")
