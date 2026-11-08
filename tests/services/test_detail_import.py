"""
test_detail_import.py — 'Relatório de Inadimplência' (detalhado) em PDF:
leitura, agrupamento por unidade com o relatório de unidades e gravação pelo
fluxo atual (sync_data → get_units_pending_notification), que não é alterado.

Teste opcional com os PDFs reais: COBRAX_PDF_UNIDADES e COBRAX_PDF_DETALHADO.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.adapters.pdf_reports import (
    ReportFormatError,
    parse_debt_detail_pages,
    parse_debt_detail_pdf,
    parse_units_pages,
    parse_units_pdf,
)
from src.domain.models import Base, Debt, Unit
from src.services.notifier import _format_message, _parse_phones
from src.services.processor import get_units_pending_notification, sync_data
from src.services.report_importer import link_detail_reports


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def result(units_pages, detail_pages, condos):
    return link_detail_reports(parse_units_pages(units_pages), parse_debt_detail_pages(detail_pages), condos)


# ── Leitura do PDF ────────────────────────────────────────────────────────────

class TestParseDetail:
    def test_reads_header_and_counts(self, detail_pages):
        report = parse_debt_detail_pages(detail_pages)
        assert report.condo_name == "CONDOMINIO TESTE FICTICIO"
        assert report.reference_date == "08/11/2026"
        assert report.declared_bills == 4
        assert report.declared_units == 3

    def test_one_item_per_bill_subtotals_ignored(self, detail_pages):
        items = parse_debt_detail_pages(detail_pages).items
        assert [(i.grupo, i.unidade, i.doc) for i in items] == [
            ("01", "101", "000101"), ("01", "101", "000102"),
            ("02", "202", "000201"), ("09", "999", "000901"),
        ]

    def test_reads_doc_and_nosso_numero(self, detail_pages):
        first = parse_debt_detail_pages(detail_pages).items[0]
        assert first.doc == "000101"
        assert first.nosso_numero == "90000001"
        assert first.doc_key == "90000001"

    def test_reads_all_values(self, detail_pages):
        b = parse_debt_detail_pages(detail_pages).items[0]
        assert (b.valor_original, b.valor_pago, b.multa, b.juros, b.correcao, b.valor_total) == (
            Decimal("100.00"), Decimal("0.00"), Decimal("2.00"),
            Decimal("10.00"), Decimal("3.00"), Decimal("115.00"),
        )

    def test_competencia_and_status_wrapped_lines(self, detail_pages):
        items = parse_debt_detail_pages(detail_pages).items
        assert items[0].competencia == "PARCELA 01/10 - CONFORME PLANILHA DA IDEAL"
        assert items[0].status == "Vencido Advogado"
        assert items[1].status == "Vencido"
        assert items[2].status == "Vencido Acordo"

    def test_bill_after_page_break_is_complete(self, detail_pages):
        b = parse_debt_detail_pages(detail_pages).items[2]
        assert (b.doc, b.nosso_numero) == ("000201", "90000003")
        assert "ALPHA" not in b.competencia

    def test_totals(self, detail_pages):
        report = parse_debt_detail_pages(detail_pages)
        assert report.declared_total == Decimal("387.80") == report.computed_total
        assert report.totals_by_unit()[("01", "101")] == Decimal("320.00")

    def test_rejects_summary_report(self, summary_pages):
        with pytest.raises(ReportFormatError):
            parse_debt_detail_pages(summary_pages)

    def test_rejects_units_report(self, units_pages):
        with pytest.raises(ReportFormatError):
            parse_debt_detail_pages(units_pages)


# ── Agrupamento com o relatório de unidades ───────────────────────────────────

class TestLinkDetail:
    def test_groups_bills_by_unit_and_links_owner(self, result):
        assert [(u.grupo, u.unidade, u.row.nome, len(u.bills)) for u in result.linked] == [
            ("01", "101", "MARIA DA SILVA PEREIRA", 2),
            ("02", "202", "ANA LIMA", 1),
        ]

    def test_unit_missing_from_units_report(self, result):
        assert [(b.grupo, b.unidade) for b in result.unmatched] == [("09", "999")]

    def test_totals_and_counts(self, result):
        assert result.bills_count == 4
        assert result.total_value == Decimal("387.80")
        assert result.linked[0].total == Decimal("320.00")

    def test_no_warnings_on_consistent_reports(self, result):
        assert result.warnings == []

    def test_warns_when_a_bill_is_lost(self, units_pages, detail_pages, condos):
        detail = parse_debt_detail_pages(detail_pages)
        detail.items.pop()
        r = link_detail_reports(parse_units_pages(units_pages), detail, condos)
        assert any("declara 4 boletos" in w for w in r.warnings)
        assert any("Total declarado" in w for w in r.warnings)

    def test_warns_when_same_owner_has_two_debtor_units(self, units_pages, detail_pages, condos):
        units = parse_units_pages(units_pages)
        units.rows[3].nome = units.rows[0].nome  # 202 passa a ser da Maria
        r = link_detail_reports(units, parse_debt_detail_pages(detail_pages), condos)
        assert any("Mesmo proprietário" in w for w in r.warnings)

    def test_sync_records_shape(self, result):
        units, bills = result.sync_records()
        assert [u.unidade for u in units] == ["101", "202"]
        assert bills[0].doc == "90000001"
        assert bills[0].nome_pagador == "MARIA DA SILVA PEREIRA"
        assert bills[0].unidade == "01 101"
        assert bills[0].vencimento == "15/01/2023"
        assert bills[0].status == "Vencido Advogado"


# ── Gravação e regra de notificação (fluxo atual, sem alterações) ─────────────

class TestPipeline:
    def _sync(self, result, db):
        units, bills = result.sync_records()
        sync_data(result.condo["id"], result.condo["adm"], units, bills, db)

    def test_persists_units_and_bills(self, result, db):
        self._sync(result, db)
        assert db.query(Unit).count() == 2
        assert db.query(Debt).count() == 3
        maria = db.query(Unit).filter_by(unit_name="Bloco 01 - 101").one()
        assert {d.doc_number for d in maria.debts} == {"90000001", "90000002"}
        assert maria.cpf_cnpj == "11111111111"

    def test_reimport_is_idempotent(self, result, db):
        self._sync(result, db)
        self._sync(result, db)
        assert db.query(Unit).count() == 2
        assert db.query(Debt).count() == 3

    def test_only_units_with_bill_over_90_days_are_eligible(self, result, db):
        self._sync(result, db)
        pending = get_units_pending_notification(db, overdue_days=90, cooldown_days=30)
        # 101 tem boleto de 2023; 202 só tem boleto de 30 dias atrás
        assert [u.unit_name for u in pending] == ["Bloco 01 - 101"]

    def test_message_and_phone_for_eligible_unit(self, result, db):
        self._sync(result, db)
        unit = get_units_pending_notification(db, overdue_days=90, cooldown_days=30)[0]
        assert _parse_phones(unit.phones) == ["5567999990001"]
        msg = _format_message(unit, result.condo["name"])
        assert "*Maria Da Silva Pereira*" in msg and "*Bloco 01 - 101*" in msg


# ── PDFs reais (opcional, local) ──────────────────────────────────────────────

_REAL_UNITS = os.getenv("COBRAX_PDF_UNIDADES")
_REAL_DETAIL = os.getenv("COBRAX_PDF_DETALHADO")


@pytest.mark.skipif(not (_REAL_UNITS and _REAL_DETAIL), reason="PDFs reais não informados")
def test_real_detail_report_links_completely(db):
    r = link_detail_reports(parse_units_pdf(_REAL_UNITS), parse_debt_detail_pdf(_REAL_DETAIL))
    assert r.warnings == []
    assert r.unmatched == []
    assert all(b.nosso_numero and b.vencimento for u in r.linked for b in u.bills)
    units, bills = r.sync_records()
    sync_data(r.condo["id"], r.condo["adm"], units, bills, db)
    assert db.query(Debt).count() == len(bills)
