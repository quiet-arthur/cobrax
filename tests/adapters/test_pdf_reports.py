"""
test_pdf_reports.py — Leitura dos relatórios em PDF da Almah (layout fictício).

Teste opcional com os PDFs reais: defina COBRAX_PDF_UNIDADES e
COBRAX_PDF_INADIMPLENCIA com os caminhos dos arquivos (nunca versioná-los).
"""

import os
from decimal import Decimal

import pytest

from src.adapters.pdf_reports import (
    ReportFormatError,
    parse_debt_summary_pages,
    parse_debt_summary_pdf,
    parse_units_pages,
    parse_units_pdf,
)


# ── Relatório de Unidades ─────────────────────────────────────────────────────

class TestParseUnits:
    def test_reads_condo_and_all_rows_across_pages(self, units_pages):
        report = parse_units_pages(units_pages)
        assert report.condo_name == "CONDOMINIO TESTE FICTICIO"
        assert [r.unidade for r in report.rows] == ["101", "102", "201", "202"]
        assert report.declared_count == 4

    def test_splits_unit_number_and_block(self, units_pages):
        rows = parse_units_pages(units_pages).rows
        assert [(r.bloco, r.unidade) for r in rows] == [
            ("01", "101"), ("01", "102"), ("02", "201"), ("02", "202"),
        ]

    def test_joins_owner_name_wrapped_in_several_lines(self, units_pages):
        rows = parse_units_pages(units_pages).rows
        assert rows[0].nome == "MARIA DA SILVA PEREIRA"
        assert rows[2].nome == "EMPRESA FICTICIA LTDA"

    def test_address_words_do_not_leak_into_name(self, units_pages):
        for row in parse_units_pages(units_pages).rows:
            assert "RUA" not in row.nome and "GRANDE-MS" not in row.nome

    def test_phone_joins_ddd_and_number(self, units_pages):
        rows = parse_units_pages(units_pages).rows
        assert rows[0].telefones == ["67999990001"]
        assert rows[3].telefones == ["6733330004"]

    def test_ddd_00_is_treated_as_missing(self, units_pages):
        assert parse_units_pages(units_pages).rows[1].telefones == ["999990002"]

    def test_unit_without_phone(self, units_pages):
        assert parse_units_pages(units_pages).rows[2].telefones == []

    def test_converts_to_unit_record(self, units_pages):
        rec = parse_units_pages(units_pages).records[0]
        assert rec.unidade == "101"
        assert rec.bloco == "01"
        assert rec.cpf_cnpj == "11111111111"
        assert rec.telefone1 == "67999990001"
        assert rec.email1 == "maria@exemplo.com"

    def test_cnpj_owner(self, units_pages):
        assert parse_units_pages(units_pages).records[2].cpf_cnpj == "33333333000133"

    def test_rejects_other_report(self, summary_pages):
        with pytest.raises(ReportFormatError):
            parse_units_pages(summary_pages)


# ── Relatório de Inadimplência Resumido ───────────────────────────────────────

class TestParseDebtSummary:
    def test_reads_header_fields(self, summary_pages):
        report = parse_debt_summary_pages(summary_pages)
        assert report.condo_name == "CONDOMINIO TESTE FICTICIO"
        assert report.reference_date == "08/11/2026"
        assert report.declared_count == 3

    def test_reads_items_and_values(self, summary_pages):
        items = parse_debt_summary_pages(summary_pages).items
        assert [(i.grupo, i.unidade, i.valor_total) for i in items] == [
            ("01", "101", Decimal("7623.93")),
            ("02", "202", Decimal("159.66")),
            ("03", "303", Decimal("1204.19")),
        ]

    def test_total_matches_sum(self, summary_pages):
        report = parse_debt_summary_pages(summary_pages)
        assert report.declared_total == Decimal("8987.78") == report.computed_total

    def test_rejects_other_report(self, units_pages):
        with pytest.raises(ReportFormatError):
            parse_debt_summary_pages(units_pages)


# ── PDFs reais (opcional, local) ──────────────────────────────────────────────

_REAL_UNITS = os.getenv("COBRAX_PDF_UNIDADES")
_REAL_SUMMARY = os.getenv("COBRAX_PDF_INADIMPLENCIA")


@pytest.mark.skipif(not (_REAL_UNITS and _REAL_SUMMARY), reason="PDFs reais não informados")
def test_real_pdfs_read_consistently():
    units = parse_units_pdf(_REAL_UNITS)
    summary = parse_debt_summary_pdf(_REAL_SUMMARY)
    assert units.declared_count == len(units.rows)
    assert summary.declared_count == len(summary.items)
    assert summary.declared_total == summary.computed_total
    assert all(r.nome and r.cpf_cnpj for r in units.rows)
