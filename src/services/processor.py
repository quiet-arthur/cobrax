import datetime
import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_

from src.domain.models import Unit, Debt
from src.domain.schemas import UnitRecord, DebtRecord

def _parse_date(date_str: str) -> datetime.date:
    try:
        return datetime.datetime.strptime(date_str, "%d/%m/%Y").date()
    except ValueError:
        return datetime.date.today()

def _parse_amount(amount_str: str) -> float:
    try:
        clean_str = amount_str.replace(".", "").replace(",", ".")
        return float(clean_str)
    except ValueError:
        return 0.0

def sync_data(condominium_id: str, units_list: list[UnitRecord], bills_list: list[DebtRecord], db: Session):
    logging.info(f"Syncing {len(units_list)} units and {len(bills_list)} bills to database.")
    
    # 1. Sync Units
    for u_rec in units_list:
        unit = db.query(Unit).filter(
            Unit.condominium_id == condominium_id,
            Unit.owner_name == u_rec.nome
        ).first()
        
        if not unit:
            unit = Unit(
                condominium_id=condominium_id,
                unit_name=u_rec.unidade,
                owner_name=u_rec.nome,
                cpf_cnpj=u_rec.cpf_cnpj,
                phones=u_rec.telefone1,
                emails=u_rec.email1,
                do_not_notify=False
            )
            db.add(unit)
            db.commit()
            db.refresh(unit)
        else:
            unit.unit_name = u_rec.unidade
            unit.cpf_cnpj = u_rec.cpf_cnpj
            if u_rec.telefone1:
                unit.phones = u_rec.telefone1
            db.commit()
            
    # 2. Sync Debts
    for d_rec in bills_list:
        unit = db.query(Unit).filter(
            Unit.condominium_id == condominium_id,
            Unit.owner_name == d_rec.nome_pagador
        ).first()

        if not unit:
            unit = Unit(
                condominium_id=condominium_id,
                unit_name=d_rec.unidade,
                owner_name=d_rec.nome_pagador,
                do_not_notify=False
            )
            db.add(unit)
            db.commit()
            db.refresh(unit)

        debt = db.query(Debt).filter(Debt.doc_number == d_rec.doc).first()
        if not debt:
            debt = Debt(
                unit_id=unit.id,
                doc_number=d_rec.doc,
                due_date=_parse_date(d_rec.vencimento),
                amount=_parse_amount(d_rec.valor_total),
                status=d_rec.status
            )
            db.add(debt)
            db.commit()

def get_pending_notifications(db: Session, overdue_days: int = 90, cooldown_days: int = 7):
    """
    Returns a list of Debts that match the notification business rules:
    - unit.do_not_notify is False
    - due_date is >= overdue_days strictly
    - last_notified_at is either null or older than cooldown_days
    """
    target_date = datetime.date.today() - datetime.timedelta(days=overdue_days)
    cooldown_date = datetime.datetime.now() - datetime.timedelta(days=cooldown_days)
    
    query = (
        db.query(Debt)
        .join(Unit)
        .filter(Unit.do_not_notify == False)
        .filter(Debt.due_date <= target_date) 
        .filter(
            or_(
                Debt.last_notified_at == None,
                Debt.last_notified_at <= cooldown_date
            )
        )
    )
    return query.all()
