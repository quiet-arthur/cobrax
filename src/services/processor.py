import datetime
import logging
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_


from src.domain.models import Unit, Debt
from src.domain.schemas import UnitRecord, DebtRecord

def _parse_date(date_str: str) -> datetime.date:
    try:
        return datetime.datetime.strptime(date_str, "%d/%m/%Y").date()
    except ValueError:
        return datetime.date.today()

def sync_data(condominium_id: str, units_list: list[UnitRecord], bills_list: list[DebtRecord], db: Session):
    logging.info(f"Syncing {len(units_list)} units and {len(bills_list)} bills to database.")
    
    # 1. Sync Units
    for u_rec in units_list:
        unit = db.query(Unit).filter(
            Unit.condominium_id == condominium_id,
            Unit.owner_name == u_rec.nome
        ).first()
        
        if not unit:
            phones_csv = ",".join(
                filter(None, [u_rec.telefone1, u_rec.telefone2])
            )
            unit = Unit(
                condominium_id=condominium_id,
                unit_name=u_rec.unidade,
                owner_name=u_rec.nome,
                cpf_cnpj=u_rec.cpf_cnpj,
                phones=phones_csv or None,
                emails=u_rec.email1,
                do_not_notify=False
            )
            db.add(unit)
            db.commit()
            db.refresh(unit)
        else:
            phones_csv = ",".join(
                filter(None, [u_rec.telefone1, u_rec.telefone2])
            )
            unit.unit_name = u_rec.unidade
            unit.cpf_cnpj = u_rec.cpf_cnpj
            if phones_csv:
                unit.phones = phones_csv
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
                status=d_rec.status
            )
            db.add(debt)
            db.commit()

def get_units_pending_notification(
    db: Session,
    overdue_days: int = 90,
    cooldown_days: int = 15,
    condominium_id: str | None = None,
) -> list[Unit]:
    """
    Retorna uma lista de Units elegíveis para notificação, com base nas regras de negócio:
    - do_not_notify == False
    - Possui telefone e/ou e-mail válido cadastrado
    - Tem pelo menos 1 débito com due_date <= (hoje - overdue_days)
    - last_notified_at está vazio OU é mais antigo que cooldown_days

    Args:
        condominium_id: Se fornecido, filtra apenas as unidades deste condomínio.
                        Se None (padrão), consulta todos os condomínios.

    Retornar Unit (não Debt) garante que cada unidade apareça apenas UMA vez,
    independente de quantos débitos ela tenha em atraso.
    """
    target_date = datetime.date.today() - datetime.timedelta(days=overdue_days)
    cooldown_date = datetime.datetime.now() - datetime.timedelta(days=cooldown_days)

    query = (
        db.query(Unit)
        .filter(Unit.do_not_notify == False)
        .filter(
            or_(
                Unit.phones.isnot(None),
                Unit.emails.isnot(None)
            )
        )
        .filter(
            Unit.debts.any(Debt.due_date <= target_date)
        )
        .filter(
            or_(
                Unit.last_notified_at == None,
                Unit.last_notified_at <= cooldown_date
            )
        )
    )

    if condominium_id is not None:
        query = query.filter(Unit.condominium_id == str(condominium_id))

    return query.all()


def get_units_without_contact(
    db: Session,
    overdue_days: int = 90,
    include_do_not_notify: bool = False,
) -> list[Unit]:
    """
    Retorna unidades em débito elegível (≥ overdue_days) que NÃO possuem
    nenhum dado de contato válido (sem telefone E sem e-mail).

    Útil como relatório de auditoria cadastral: identifica devedores que
    o sistema nunca conseguirá notificar por falta de dados.

    Args:
        overdue_days:          Threshold de atraso (padrão: 90 dias).
        include_do_not_notify: Se True, inclui também unidades com do_not_notify=True.
                               Por padrão, foca apenas nas que deveriam ser notificadas.

    Returns:
        Lista de Unit ordenada por condominium_id e unit_name.
    """
    target_date = datetime.date.today() - datetime.timedelta(days=overdue_days)

    query = (
        db.query(Unit)
        .filter(
            and_(
                or_(Unit.phones == None, Unit.phones == ""),
                or_(Unit.emails == None, Unit.emails == ""),
            )
        )
        .filter(Unit.debts.any(Debt.due_date <= target_date))
        .order_by(Unit.condominium_id, Unit.unit_name)
    )

    if not include_do_not_notify:
        query = query.filter(Unit.do_not_notify == False)

    return query.all()

