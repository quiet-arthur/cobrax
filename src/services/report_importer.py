"""
report_importer.py — Relaciona os relatórios em PDF exportados da Almah.

Cruza o "Relatório de Inadimplência Resumido" (quem deve e quanto) com o
"Relatório de Unidades" (quem é o proprietário e como contatá-lo), usando a
chave bloco + unidade, e verifica se o resultado tem o que o módulo de
notificação (src/services/notifier.py) precisa.

Não altera o login (AlmahSession) nem o envio (NotificationService).

Dois relatórios de inadimplência são aceitos:
  - RESUMIDO (unidade + valor total): só relaciona e valida. Não tem número do
    boleto nem vencimento, então não alimenta a regra dos 90 dias e não é gravado.
  - DETALHADO ("Relatório de Inadimplência", um boleto por linha): agrupa os
    boletos por unidade, liga ao proprietário e gera os registros para
    `sync_data` — o mesmo caminho usado pela extração automática.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from pathlib import Path

from src.adapters.pdf_reports import (
    DebtDetailItem,
    DebtDetailReport,
    DebtSummaryItem,
    DebtSummaryReport,
    ReportFormatError,
    UnitRow,
    UnitsReport,
    normalize_name,
)
from src.domain.schemas import DebtRecord, UnitRecord
from src.services.notifier import _parse_phones

_CONDOS_CONFIG_PATH = Path(__file__).parent.parent / "config" / "condominios.json"
_CONDO_PREFIXES = ("CONDOMINIO ", "COND. ", "COND ")


def _condo_key(name: str) -> str:
    key = normalize_name(name)
    for prefix in _CONDO_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
    return key


def _unit_key(bloco: str | None, unidade: str) -> tuple[str, str]:
    """Chave de ligação; zeros à esquerda do bloco não importam ("01" == "1")."""
    return ((bloco or "").lstrip("0") or "0", unidade.strip().upper())


def resolve_condo(condo_name: str, condos: list[dict] | None = None) -> dict:
    """Acha o condomínio em condominios.json pelo nome impresso no relatório."""
    if condos is None:
        condos = json.loads(_CONDOS_CONFIG_PATH.read_text(encoding="utf-8"))
    key = _condo_key(condo_name)
    matches = [c for c in condos if _condo_key(c["name"]) == key]
    if len(matches) != 1:
        raise ReportFormatError(
            f"Condomínio '{condo_name}' não encontrado (ou ambíguo) em condominios.json."
        )
    return matches[0]


@dataclass
class LinkedDebtor:
    """Uma unidade inadimplente já ligada ao seu cadastro de proprietário."""
    item: DebtSummaryItem
    row: UnitRow

    @cached_property
    def unit_record(self) -> UnitRecord:
        return self.row.to_unit_record()

    @cached_property
    def phones_for_notification(self) -> list[str]:
        """Telefones exatamente como o notifier os normalizaria (E.164 sem '+')."""
        rec = self.unit_record
        csv = ",".join(filter(None, [rec.telefone1, rec.telefone2]))
        return _parse_phones(csv)

    @property
    def has_email(self) -> bool:
        return bool(self.unit_record.email1)


@dataclass
class ImportResult:
    condo: dict
    reference_date: str
    linked: list[LinkedDebtor] = field(default_factory=list)
    unmatched: list[DebtSummaryItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_value(self) -> Decimal:
        return sum((d.item.valor_total for d in self.linked), Decimal("0")) + sum(
            (i.valor_total for i in self.unmatched), Decimal("0")
        )

    @property
    def with_valid_phone(self) -> list[LinkedDebtor]:
        return [d for d in self.linked if d.phones_for_notification]

    @property
    def without_contact(self) -> list[LinkedDebtor]:
        return [d for d in self.linked if not d.phones_for_notification and not d.has_email]


def link_reports(
    units: UnitsReport,
    debts: DebtSummaryReport,
    condos: list[dict] | None = None,
) -> ImportResult:
    """
    Relaciona os dois relatórios do MESMO condomínio.

    Raises:
        ReportFormatError: relatórios de condomínios diferentes ou condomínio
            não cadastrado em condominios.json.
    """
    if _condo_key(units.condo_name) != _condo_key(debts.condo_name):
        raise ReportFormatError(
            "Os relatórios são de condomínios diferentes: "
            f"'{units.condo_name}' × '{debts.condo_name}'."
        )
    condo = resolve_condo(debts.condo_name, condos)
    result = ImportResult(condo=condo, reference_date=debts.reference_date)

    # Conferências de integridade da leitura dos PDFs
    if units.declared_count is not None and units.declared_count != len(units.rows):
        result.warnings.append(
            f"Relatório de unidades declara {units.declared_count} registros, "
            f"mas foram lidos {len(units.rows)}."
        )
    if debts.declared_count is not None and debts.declared_count != len(debts.items):
        result.warnings.append(
            f"Resumido declara {debts.declared_count} unidades inadimplentes, "
            f"mas foram lidas {len(debts.items)}."
        )
    if debts.declared_total is not None and debts.declared_total != debts.computed_total:
        result.warnings.append(
            f"Total declarado (R$ {debts.declared_total}) difere da soma lida "
            f"(R$ {debts.computed_total})."
        )

    by_key: dict[tuple[str, str], UnitRow] = {}
    for row in units.rows:
        key = _unit_key(row.bloco, row.unidade)
        if key in by_key:
            result.warnings.append(f"Unidade repetida no relatório de unidades: {row.bloco} {row.unidade}.")
        by_key[key] = row

    for item in debts.items:
        row = by_key.get(_unit_key(item.grupo, item.unidade))
        if row is None:
            result.unmatched.append(item)
        else:
            result.linked.append(LinkedDebtor(item=item, row=row))
    return result


# ── Relatório detalhado (um boleto por linha) ─────────────────────────────────

@dataclass
class LinkedUnitDebts:
    """Uma unidade inadimplente com seu cadastro e todos os boletos em atraso."""
    row: UnitRow
    bills: list[DebtDetailItem]

    @property
    def grupo(self) -> str:
        return self.bills[0].grupo

    @property
    def unidade(self) -> str:
        return self.bills[0].unidade

    @property
    def total(self) -> Decimal:
        return sum((b.valor_total for b in self.bills), Decimal("0"))

    @cached_property
    def unit_record(self) -> UnitRecord:
        return self.row.to_unit_record()

    @cached_property
    def phones_for_notification(self) -> list[str]:
        rec = self.unit_record
        return _parse_phones(",".join(filter(None, [rec.telefone1, rec.telefone2])))

    @property
    def has_email(self) -> bool:
        return bool(self.unit_record.email1)

    @property
    def oldest_due_date(self) -> datetime.date:
        return min(_to_date(b.vencimento) for b in self.bills)

    def debt_records(self) -> list[DebtRecord]:
        """Boletos no formato que `sync_data` espera (mesmo do feed da Almah)."""
        return [
            DebtRecord(**{
                "Unidade": f"{b.grupo} {b.unidade}",
                "Nome do Pagador": self.row.nome,
                "Doc": b.doc_key,
                "Venc": b.vencimento,
                "Vlr Total": str(b.valor_total),
                "Status": b.status,
            })
            for b in self.bills
        ]


def _to_date(ddmmyyyy: str) -> datetime.date:
    return datetime.datetime.strptime(ddmmyyyy, "%d/%m/%Y").date()


@dataclass
class DetailImportResult:
    condo: dict
    reference_date: str
    linked: list[LinkedUnitDebts] = field(default_factory=list)
    unmatched: list[DebtDetailItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def bills_count(self) -> int:
        return sum(len(u.bills) for u in self.linked) + len(self.unmatched)

    @property
    def total_value(self) -> Decimal:
        return sum((u.total for u in self.linked), Decimal("0")) + sum(
            (b.valor_total for b in self.unmatched), Decimal("0")
        )

    @property
    def with_valid_phone(self) -> list[LinkedUnitDebts]:
        return [u for u in self.linked if u.phones_for_notification]

    @property
    def without_contact(self) -> list[LinkedUnitDebts]:
        return [u for u in self.linked if not u.phones_for_notification and not u.has_email]

    def sync_records(self) -> tuple[list[UnitRecord], list[DebtRecord]]:
        """(unidades, boletos) prontos para `sync_data`, sem alterá-lo."""
        units = [u.unit_record for u in self.linked]
        bills = [r for u in self.linked for r in u.debt_records()]
        return units, bills


def link_detail_reports(
    units: UnitsReport,
    detail: DebtDetailReport,
    condos: list[dict] | None = None,
) -> DetailImportResult:
    """
    Agrupa os boletos do relatório detalhado por unidade e liga cada unidade ao
    seu proprietário no relatório de unidades (chave bloco + unidade).
    """
    if _condo_key(units.condo_name) != _condo_key(detail.condo_name):
        raise ReportFormatError(
            "Os relatórios são de condomínios diferentes: "
            f"'{units.condo_name}' × '{detail.condo_name}'."
        )
    condo = resolve_condo(detail.condo_name, condos)
    result = DetailImportResult(condo=condo, reference_date=detail.reference_date)

    if units.declared_count is not None and units.declared_count != len(units.rows):
        result.warnings.append(
            f"Relatório de unidades declara {units.declared_count} registros, "
            f"mas foram lidos {len(units.rows)}."
        )
    if detail.declared_bills is not None and detail.declared_bills != len(detail.items):
        result.warnings.append(
            f"Relatório de inadimplência declara {detail.declared_bills} boletos, "
            f"mas foram lidos {len(detail.items)}."
        )
    n_units = len(detail.totals_by_unit())
    if detail.declared_units is not None and detail.declared_units != n_units:
        result.warnings.append(
            f"Relatório de inadimplência declara {detail.declared_units} unidades, "
            f"mas foram lidas {n_units}."
        )
    if detail.declared_total is not None and detail.declared_total != detail.computed_total:
        result.warnings.append(
            f"Total declarado (R$ {detail.declared_total}) difere da soma lida "
            f"(R$ {detail.computed_total})."
        )
    for b in detail.items:
        if b.valor_original - b.valor_pago + b.multa + b.juros + b.correcao != b.valor_total:
            result.warnings.append(f"Boleto {b.doc_key}: componentes não somam o valor total.")

    by_key = {_unit_key(r.bloco, r.unidade): r for r in units.rows}
    grouped: dict[tuple[str, str], list[DebtDetailItem]] = {}
    for b in detail.items:
        grouped.setdefault(_unit_key(b.grupo, b.unidade), []).append(b)

    for key, bills in grouped.items():
        row = by_key.get(key)
        if row is None:
            result.unmatched.extend(bills)
        else:
            result.linked.append(LinkedUnitDebts(row=row, bills=bills))

    # sync_data identifica a unidade pelo NOME do proprietário: duas unidades
    # inadimplentes do mesmo dono seriam fundidas numa só (bug conhecido).
    owners: dict[str, list[str]] = {}
    for u in result.linked:
        owners.setdefault(normalize_name(u.row.nome), []).append(f"{u.grupo} {u.unidade}")
    for owner_units in owners.values():
        if len(owner_units) > 1:
            result.warnings.append(
                "Mesmo proprietário em várias unidades inadimplentes "
                f"({', '.join(owner_units)}): o banco atual as gravaria como uma só."
            )
    return result
