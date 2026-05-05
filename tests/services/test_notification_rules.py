"""
Testes para as regras de elegibilidade de notificação (get_units_pending_notification).

Cenários cobertos:
  - Regra base: unidade com débito >90 dias + telefone + do_not_notify=False → elegível
  - do_not_notify=True → bloqueada
  - Sem contato (phones e emails vazios) → bloqueada
  - Cooldown: unidade notificada recentemente → bloqueada
  - Filtro por condominium_ids (single, multiple, None)
  - Lista vazia de condominium_ids → sem filtro (comportamento = None)
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Base, Debt, Unit
from src.services.processor import get_units_pending_notification


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> Session:
    """Cria banco SQLite in-memory com schema limpo para cada teste."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()


def _make_unit(
    db: Session,
    *,
    condominium_id: str = "999",
    adm: str = "alpha",
    unit_name: str = "101",
    owner_name: str = "John",
    phones: str | None = "1199999999",
    emails: str | None = None,
    do_not_notify: bool = False,
    last_notified_at: datetime.datetime | None = None,
) -> Unit:
    """Helper para criar unidades de teste com valores sensatos de default."""
    unit = Unit(
        condominium_id=condominium_id,
        adm=adm,
        unit_name=unit_name,
        owner_name=owner_name,
        phones=phones,
        emails=emails,
        do_not_notify=do_not_notify,
        last_notified_at=last_notified_at,
    )
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


def _make_debt(
    db: Session,
    unit: Unit,
    *,
    doc_number: str = "D1",
    days_overdue: int = 95,
    status: str = "Vencido",
) -> Debt:
    """Helper para criar débitos de teste com due_date relativa a hoje."""
    debt = Debt(
        unit_id=unit.id,
        doc_number=doc_number,
        due_date=datetime.date.today() - datetime.timedelta(days=days_overdue),
        status=status,
    )
    db.add(debt)
    db.commit()
    return debt


# ── Testes de regras base ─────────────────────────────────────────────────────

class TestBaseRules:
    """Testes das regras de elegibilidade (sem considerar filtro de condomínio)."""

    def test_eligible_unit_is_returned(self, db: Session) -> None:
        """Unidade com débito >90 dias, telefone e do_not_notify=False → elegível."""
        u = _make_unit(db)
        _make_debt(db, u, doc_number="D1", days_overdue=95)

        result = get_units_pending_notification(db, overdue_days=90)
        assert len(result) == 1
        assert result[0].unit_name == "101"

    def test_do_not_notify_blocks_unit(self, db: Session) -> None:
        """Unidade com do_not_notify=True NÃO deve ser retornada."""
        u = _make_unit(db, do_not_notify=True)
        _make_debt(db, u, doc_number="D2", days_overdue=100)

        result = get_units_pending_notification(db, overdue_days=90)
        assert len(result) == 0

    def test_no_contact_blocks_unit(self, db: Session) -> None:
        """Unidade sem telefone E sem email NÃO deve ser retornada."""
        u = _make_unit(db, phones=None, emails=None)
        _make_debt(db, u, doc_number="D3", days_overdue=100)

        result = get_units_pending_notification(db, overdue_days=90)
        assert len(result) == 0

    def test_debt_below_threshold_not_eligible(self, db: Session) -> None:
        """Unidade cujo débito mais antigo tem < 90 dias NÃO é elegível."""
        u = _make_unit(db)
        _make_debt(db, u, doc_number="D4", days_overdue=80)

        result = get_units_pending_notification(db, overdue_days=90)
        assert len(result) == 0

    def test_cooldown_blocks_recently_notified(self, db: Session) -> None:
        """Unidade notificada há menos de 15 dias NÃO é elegível."""
        recently = datetime.datetime.now() - datetime.timedelta(days=5)
        u = _make_unit(db, last_notified_at=recently)
        _make_debt(db, u, doc_number="D5", days_overdue=95)

        result = get_units_pending_notification(db, overdue_days=90, cooldown_days=15)
        assert len(result) == 0

    def test_cooldown_expired_is_eligible(self, db: Session) -> None:
        """Unidade notificada há mais de 15 dias É elegível novamente."""
        long_ago = datetime.datetime.now() - datetime.timedelta(days=20)
        u = _make_unit(db, last_notified_at=long_ago)
        _make_debt(db, u, doc_number="D6", days_overdue=95)

        result = get_units_pending_notification(db, overdue_days=90, cooldown_days=15)
        assert len(result) == 1


# ── Testes do filtro por condominium_ids ──────────────────────────────────────

class TestCondominiumIdsFilter:
    """Testes específicos para o filtro de escopo por lista de condominium_ids."""

    def _seed_multi_condo(self, db: Session) -> tuple[Unit, Unit, Unit]:
        """Cria 3 unidades em condomínios distintos, todas elegíveis."""
        u_a = _make_unit(db, condominium_id="10", unit_name="A1", owner_name="Alice")
        _make_debt(db, u_a, doc_number="DA1", days_overdue=100)

        u_b = _make_unit(db, condominium_id="20", unit_name="B1", owner_name="Bob")
        _make_debt(db, u_b, doc_number="DB1", days_overdue=100)

        u_c = _make_unit(db, condominium_id="30", unit_name="C1", owner_name="Carol")
        _make_debt(db, u_c, doc_number="DC1", days_overdue=100)

        return u_a, u_b, u_c

    def test_filter_single_condo(self, db: Session) -> None:
        """Passando 1 ID, retorna apenas unidades daquele condomínio."""
        self._seed_multi_condo(db)

        result = get_units_pending_notification(
            db, overdue_days=90, condominium_ids=["10"]
        )
        assert len(result) == 1
        assert result[0].condominium_id == "10"

    def test_filter_multiple_condos(self, db: Session) -> None:
        """Passando 2 IDs, retorna unidades de ambos os condomínios."""
        self._seed_multi_condo(db)

        result = get_units_pending_notification(
            db, overdue_days=90, condominium_ids=["10", "30"]
        )
        assert len(result) == 2
        returned_ids = {u.condominium_id for u in result}
        assert returned_ids == {"10", "30"}

    def test_filter_none_returns_all(self, db: Session) -> None:
        """Passando None (sem filtro), retorna TODAS as unidades elegíveis."""
        self._seed_multi_condo(db)

        result = get_units_pending_notification(
            db, overdue_days=90, condominium_ids=None
        )
        assert len(result) == 3

    def test_filter_empty_list_returns_all(self, db: Session) -> None:
        """Passando lista vazia, comporta-se como None (sem filtro)."""
        self._seed_multi_condo(db)

        result = get_units_pending_notification(
            db, overdue_days=90, condominium_ids=[]
        )
        assert len(result) == 3

    def test_filter_nonexistent_id_returns_empty(self, db: Session) -> None:
        """Passando ID inexistente, retorna lista vazia sem erro."""
        self._seed_multi_condo(db)

        result = get_units_pending_notification(
            db, overdue_days=90, condominium_ids=["99999"]
        )
        assert len(result) == 0
