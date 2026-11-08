"""
test_report_importer.py — Ligação dos relatórios em PDF e compatibilidade com
o módulo de notificação.

O teste de compatibilidade passa os dados lidos dos PDFs pelo MESMO caminho do
fluxo atual (sync_data → get_units_pending_notification → _format_message /
_parse_phones), sem alterar nenhum desses módulos.
"""

import datetime
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.adapters.pdf_reports import (
    ReportFormatError,
    parse_debt_summary_pages,
    parse_debt_summary_pdf,
    parse_units_pages,
    parse_units_pdf,
)
from src.domain.models import Base
from src.domain.schemas import DebtRecord
from src.services.notifier import _format_message, _parse_phones
from src.services.processor import get_units_pending_notification, sync_data
from src.services.report_importer import link_reports, resolve_condo


@pytest.fixture()
def linked(units_pages, summary_pages, condos):
    return link_reports(parse_units_pages(units_pages), parse_debt_summary_pages(summary_pages), condos)


# ── Ligação ───────────────────────────────────────────────────────────────────

class TestLinkReports:
    def test_resolves_condo_ignoring_prefix_and_accents(self, condos):
        assert resolve_condo("CONDOMÍNIO TESTE FICTÍCIO", condos)["id"] == "999"

    def test_unknown_condo_raises(self, condos):
        with pytest.raises(ReportFormatError):
            resolve_condo("CONDOMINIO INEXISTENTE", condos)

    def test_links_by_block_and_unit(self, linked):
        assert linked.condo["id"] == "999"
        assert [(d.item.unidade, d.row.nome) for d in linked.linked] == [
            ("101", "MARIA DA SILVA PEREIRA"),
            ("202", "ANA LIMA"),
        ]

    def test_debtor_missing_from_units_report_is_reported(self, linked):
        assert [(i.grupo, i.unidade) for i in linked.unmatched] == [("03", "303")]

    def test_same_number_in_other_block_does_not_link(self, units_pages, summary_pages, condos):
        summary = parse_debt_summary_pages(summary_pages)
        summary.items[0].grupo = "02"  # unidade 101 do bloco 02 não existe
        result = link_reports(parse_units_pages(units_pages), summary, condos)
        assert "101" in [i.unidade for i in result.unmatched]

    def test_block_zero_padding_is_ignored(self, units_pages, summary_pages, condos):
        summary = parse_debt_summary_pages(summary_pages)
        summary.items[0].grupo = "1"
        result = link_reports(parse_units_pages(units_pages), summary, condos)
        assert "101" in [d.item.unidade for d in result.linked]

    def test_reports_from_different_condos_raise(self, units_pages, summary_pages, condos):
        summary = parse_debt_summary_pages(summary_pages)
        summary.condo_name = "OUTRO CONDOMINIO"
        with pytest.raises(ReportFormatError):
            link_reports(parse_units_pages(units_pages), summary, condos)

    def test_no_warnings_when_counts_and_totals_match(self, linked):
        assert linked.warnings == []

    def test_warns_when_total_does_not_match(self, units_pages, summary_pages, condos):
        summary = parse_debt_summary_pages(summary_pages)
        summary.items.pop()
        result = link_reports(parse_units_pages(units_pages), summary, condos)
        assert any("Total declarado" in w for w in result.warnings)
        assert any("declara 3" in w for w in result.warnings)


# ── Compatibilidade com o módulo de notificação ───────────────────────────────

class TestNotificationCompatibility:
    def test_phones_come_out_in_e164(self, linked):
        assert linked.linked[0].phones_for_notification == ["5567999990001"]
        assert linked.linked[1].phones_for_notification == ["556733330004"]

    def test_ddd_00_phone_is_discarded_by_notifier(self, units_pages):
        rec = parse_units_pages(units_pages).records[1]
        assert _parse_phones(rec.telefone1) == []

    def test_contact_counters(self, linked):
        assert len(linked.with_valid_phone) == 2
        assert linked.without_contact == []

    def test_linked_data_flows_through_current_pipeline(self, linked):
        """
        Simula o que o relatório DETALHADO fornecerá (Doc + Vencimento) e passa os
        dados lidos do PDF pelo fluxo atual, sem alterá-lo.
        """
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine)()
        try:
            due = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%d/%m/%Y")
            units = [d.unit_record for d in linked.linked]
            bills = [
                DebtRecord(**{
                    "Unidade": f"{d.item.grupo} {d.item.unidade}",
                    "Nome do Pagador": d.unit_record.nome,
                    "Doc": f"FICTICIO-{d.item.unidade}",
                    "Venc": due,
                    "Vlr Total": str(d.item.valor_total),
                    "Status": "Em aberto",
                })
                for d in linked.linked
            ]
            sync_data(linked.condo["id"], linked.condo["adm"], units, bills, db)

            pending = get_units_pending_notification(db, overdue_days=90, cooldown_days=30)
            assert {u.unit_name for u in pending} == {"Bloco 01 - 101", "Bloco 02 - 202"}

            maria = next(u for u in pending if u.unit_name == "Bloco 01 - 101")
            assert _parse_phones(maria.phones) == ["5567999990001"]
            msg = _format_message(maria, linked.condo["name"])
            assert "*Maria Da Silva Pereira*" in msg
            assert "*Bloco 01 - 101*" in msg
            assert "*TESTE FICTICIO*" in msg
        finally:
            db.close()
            engine.dispose()


# ── PDFs reais (opcional, local) ──────────────────────────────────────────────

_REAL_UNITS = os.getenv("COBRAX_PDF_UNIDADES")
_REAL_SUMMARY = os.getenv("COBRAX_PDF_INADIMPLENCIA")


@pytest.mark.skipif(not (_REAL_UNITS and _REAL_SUMMARY), reason="PDFs reais não informados")
def test_real_pdfs_link_completely():
    result = link_reports(parse_units_pdf(_REAL_UNITS), parse_debt_summary_pdf(_REAL_SUMMARY))
    assert result.warnings == []
    assert result.unmatched == []
    for d in result.linked:
        rec = d.unit_record
        assert rec.nome and rec.cpf_cnpj
        assert d.phones_for_notification or d.has_email
