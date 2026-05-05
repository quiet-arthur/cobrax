"""
test_sync_data.py — Testes unitários para sync_data e _format_unit_name.

Valida:
  - _format_unit_name: normalização do nome da unidade com/sem bloco
  - Unit criada com bloco → unit_name = "Bloco X - NN"
  - Unit criada sem bloco → unit_name = "NN" (sem prefixo)
  - Unit com bloco embutido na unidade → sem duplicação (ex: "A - 001")
  - Unit atualizada → unit_name formatado corretamente
  - Debt sync não sobrescreve o nome formatado com bloco
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Base, Debt, Unit
from src.domain.schemas import DebtRecord, UnitRecord
from src.services.processor import sync_data, _format_unit_name


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


def _make_unit_record(
    *,
    unidade: str = "01",
    bloco: str | None = None,
    cpf_cnpj: str = "12345678900",
    nome: str = "João Silva",
    telefone1: str | None = "67999999999",
    telefone2: str | None = None,
    email1: str | None = "joao@test.com",
    email2: str | None = None,
) -> UnitRecord:
    """Helper para criar UnitRecord com valores sensatos de default."""
    return UnitRecord(
        Unidade=unidade,
        ProprietarioCpfCnpj=cpf_cnpj,
        ProprietarioNome=nome,
        ProprietarioTelefone1=telefone1,
        ProprietarioTelefone2=telefone2,
        ProprietarioEmail1=email1,
        ProprietarioEmail2=email2,
        bloco=bloco,
    )


def _make_debt_record(
    *,
    unidade: str = "A 01",
    nome_pagador: str = "João Silva",
    doc: str = "DOC-001",
    vencimento: str = "01/01/2025",
    status: str = "Vencido",
) -> DebtRecord:
    """Helper para criar DebtRecord."""
    return DebtRecord(
        **{
            "Unidade": unidade,
            "Nome do Pagador": nome_pagador,
            "Doc": doc,
            "Venc": vencimento,
            "Vlr Total": "R$ 500,00",
            "Status": status,
        }
    )


# ── Testes de _format_unit_name (função pura) ─────────────────────────────────


class TestFormatUnitName:
    """Testes da normalização de nomes de unidades com blocos."""

    @pytest.mark.parametrize(
        "unidade, bloco, expected",
        [
            # Cenário simples: bloco separado da unidade
            ("01", "A", "Bloco A - 01"),
            ("03", "B", "Bloco B - 03"),
            # Sem bloco: retorna o valor cru
            ("01", None, "01"),
            ("A - 001", None, "A - 001"),
            # Bloco embutido na unidade (condomínio 164 - RITA VIEIRA PARQUE)
            ("A - 001", "A", "Bloco A - 001"),
            ("A - 101", "A", "Bloco A - 101"),
            ("B - 003", "B", "Bloco B - 003"),
            # Variações de separador
            ("A-001", "A", "Bloco A - 001"),
            ("A 001", "A", "Bloco A - 001"),
            # Bloco vazio (string vazia é falsy)
            ("01", "", "01"),
        ],
    )
    def test_format_variations(self, unidade: str, bloco: str | None, expected: str) -> None:
        """Deve formatar corretamente para cada variação real da API."""
        assert _format_unit_name(unidade, bloco) == expected

    def test_bug_condominio_164_no_duplication(self) -> None:
        """
        Regressão: condomínio 164 gerava 'Bloco BLOCO A - A - 101'.
        Com a normalização do bloco no adapter ('BLOCO A' → 'A')
        e a deduplicação aqui, deve gerar 'Bloco A - 101'.
        """
        # O adapter já normaliza "BLOCO A" → "A", então o processor recebe bloco="A"
        result = _format_unit_name("A - 101", "A")
        assert result == "Bloco A - 101"
        assert "BLOCO" not in result  # Sem duplicação de "BLOCO"
        assert "A - A" not in result  # Sem duplicação do prefixo do bloco


# ── Testes de sync_data (integração com DB) ───────────────────────────────────


class TestSyncDataWithBlock:
    """Testes do sync_data para formatação de unit_name com bloco."""

    def test_new_unit_with_block_formats_name(self, db: Session) -> None:
        """Unit nova com bloco deve salvar como 'Bloco A - 01'."""
        units = [_make_unit_record(unidade="01", bloco="A", nome="Alice")]
        sync_data("87", "expresso", units, [], db)

        unit = db.query(Unit).first()
        assert unit is not None
        assert unit.unit_name == "Bloco A - 01"

    def test_new_unit_without_block_saves_raw(self, db: Session) -> None:
        """Unit nova sem bloco deve salvar apenas o numeral (ex: '01')."""
        units = [_make_unit_record(unidade="01", bloco=None, nome="Bob")]
        sync_data("205", "alpha", units, [], db)

        unit = db.query(Unit).first()
        assert unit is not None
        assert unit.unit_name == "01"

    def test_update_unit_with_block_formats_name(self, db: Session) -> None:
        """Unit existente atualizada com bloco deve ter o nome re-formatado."""
        # Primeiro sync sem bloco
        units_v1 = [_make_unit_record(unidade="03", bloco=None, nome="Carol")]
        sync_data("87", "expresso", units_v1, [], db)

        unit = db.query(Unit).first()
        assert unit.unit_name == "03"

        # Segundo sync com bloco (simulando que agora o datatable retornou o bloco)
        units_v2 = [_make_unit_record(unidade="03", bloco="B", nome="Carol")]
        sync_data("87", "expresso", units_v2, [], db)

        db.refresh(unit)
        assert unit.unit_name == "Bloco B - 03"

    def test_multiple_units_different_blocks(self, db: Session) -> None:
        """Unidades de blocos distintos devem ser formatadas individualmente."""
        units = [
            _make_unit_record(unidade="01", bloco="A", nome="Alice", cpf_cnpj="11111111111"),
            _make_unit_record(unidade="01", bloco="B", nome="Bob", cpf_cnpj="22222222222"),
            _make_unit_record(unidade="02", bloco="A", nome="Carol", cpf_cnpj="33333333333"),
        ]
        sync_data("87", "expresso", units, [], db)

        all_units = db.query(Unit).order_by(Unit.owner_name).all()
        assert len(all_units) == 3

        alice = next(u for u in all_units if u.owner_name == "Alice")
        assert alice.unit_name == "Bloco A - 01"

        bob = next(u for u in all_units if u.owner_name == "Bob")
        assert bob.unit_name == "Bloco B - 01"

        carol = next(u for u in all_units if u.owner_name == "Carol")
        assert carol.unit_name == "Bloco A - 02"

    def test_debt_sync_does_not_overwrite_formatted_name(self, db: Session) -> None:
        """
        O sync de dívidas NÃO deve sobrescrever o unit_name formatado.
        A unidade já existe com 'Bloco A - 01', o sync de debt
        encontra pelo nome do pagador e apenas insere o débito.
        """
        # Primeiro: sync units com bloco
        units = [_make_unit_record(unidade="01", bloco="A", nome="João Silva")]
        sync_data("87", "expresso", units, [], db)

        unit = db.query(Unit).first()
        assert unit.unit_name == "Bloco A - 01"

        # Segundo: sync debts (o debt vem do Almah com unidade simplificada)
        debts = [_make_debt_record(unidade="A 01", nome_pagador="João Silva")]
        sync_data("87", "expresso", [], debts, db)

        db.refresh(unit)
        # O nome formatado deve permanecer intacto
        assert unit.unit_name == "Bloco A - 01"

    def test_debt_creates_unit_when_not_found(self, db: Session) -> None:
        """
        Se um débito chegar para um proprietário que não existe
        na tabela de units, deve criá-lo com o nome bruto da unidade do boleto.
        """
        debts = [_make_debt_record(unidade="B 03", nome_pagador="Desconhecido")]
        sync_data("87", "expresso", [], debts, db)

        unit = db.query(Unit).first()
        assert unit is not None
        assert unit.owner_name == "Desconhecido"
        assert unit.unit_name == "B 03"

        debt = db.query(Debt).first()
        assert debt is not None
        assert debt.doc_number == "DOC-001"

    def test_unit_with_embedded_block_no_duplication(self, db: Session) -> None:
        """
        Regressão condomínio 164: unidade já vem como 'A - 101' e bloco='A'.
        Deve salvar como 'Bloco A - 101', NÃO como 'Bloco A - A - 101'.
        """
        units = [
            _make_unit_record(unidade="A - 101", bloco="A", nome="Marilza"),
            _make_unit_record(unidade="B - 003", bloco="B", nome="Carlos", cpf_cnpj="22222222222"),
        ]
        sync_data("164", "alpha", units, [], db)

        all_units = db.query(Unit).order_by(Unit.owner_name).all()
        assert len(all_units) == 2

        carlos = next(u for u in all_units if u.owner_name == "Carlos")
        assert carlos.unit_name == "Bloco B - 003"

        marilza = next(u for u in all_units if u.owner_name == "Marilza")
        assert marilza.unit_name == "Bloco A - 101"

