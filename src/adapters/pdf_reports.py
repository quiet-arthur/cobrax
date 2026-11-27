"""
pdf_reports.py — Leitura dos relatórios em PDF exportados manualmente da Almah.

Alternativa à extração automática (AlmahSession) enquanto a Almah mantiver o
AWS WAF Challenge, que bloqueia clientes HTTP automatizados. Alguém do
escritório exporta os PDFs pela tela da Almah e este módulo os converte nos
mesmos schemas de domínio usados pelo resto do sistema.

Relatórios suportados:
  - "Relatório de Unidades" (PROPRIETÁRIOS)       → UnitsReport  (list[UnitRecord])
  - "Relatório de Inadimplência Resumido"          → DebtSummaryReport

O parsing é feito pela POSIÇÃO das palavras na página (coordenada x de cada
coluna, lida do cabeçalho da tabela), porque os nomes e endereços quebram em
várias linhas dentro da mesma célula. As funções `parse_*_pages` recebem as
palavras já extraídas, o que permite testá-las sem PDFs reais (que contêm
dados pessoais e nunca devem ir para o repositório).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from src.domain.schemas import UnitRecord

# Uma "palavra" é um dict com ao menos: text, x0, top (formato do pdfplumber).
Word = dict
Page = list[Word]

# Tolerância vertical (pt) para considerar palavras na mesma linha.
_LINE_TOLERANCE = 2.0

_MONEY_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$")
_FOOTER_MARKERS = ("Emitido em", "registro(s)", "Fração Total")


class ReportFormatError(ValueError):
    """O PDF não tem o formato esperado (relatório errado ou layout alterado)."""


# ── Utilitários ───────────────────────────────────────────────────────────────

def normalize_name(text: str) -> str:
    """Maiúsculas, sem acentos e com espaços simples — para comparar nomes."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.upper().split())


def parse_money(text: str) -> Decimal:
    """'7.623,93' → Decimal('7623.93')."""
    return Decimal(text.replace(".", "").replace(",", "."))


def load_pages(pdf_path: str | Path) -> list[Page]:
    """
    Abre o PDF e devolve as palavras de cada página.

    Os títulos da Almah são "negrito por sobreposição" (cada letra impressa duas
    vezes, ex.: "UUnniiddaaddee"); `dedupe_chars` remove as duplicatas.
    """
    import pdfplumber  # import tardio: só quem lê PDFs precisa da dependência

    pages: list[Page] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = page.dedupe_chars().extract_words(keep_blank_chars=False)
            pages.append([
                {"text": w["text"], "x0": float(w["x0"]), "top": float(w["top"])}
                for w in words
            ])
    return pages


def _group_lines(words: Page) -> list[list[Word]]:
    """Agrupa palavras em linhas (por `top`), cada linha ordenada por x."""
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _line_text(line: list[Word]) -> str:
    return " ".join(w["text"] for w in line)


def _all_text(pages: list[Page]) -> str:
    return "\n".join(_line_text(l) for p in pages for l in _group_lines(p))


def _search(pattern: str, text: str, what: str) -> re.Match:
    m = re.search(pattern, text)
    if not m:
        raise ReportFormatError(f"Não encontrei {what} no relatório.")
    return m


# ── Relatório de Unidades ─────────────────────────────────────────────────────

# Cabeçalhos da tabela de proprietários → nome interno da coluna.
_UNIT_COLUMNS = {
    "Unidade": "unidade",
    "Dia": "dia_venc",
    "Fração": "fracao",
    "Proprietário": "nome",
    "CPF/CNPJ": "cpf_cnpj",
    "Endereço": "endereco",
    "Telefone": "telefone",
    "E-mail": "email",
    "Boleto/Envio": "envio",
    "Síndico": "sindico",
    "Acesso": "acesso",
}


@dataclass
class UnitRow:
    """Uma linha do relatório de unidades, já com as células reconstruídas."""
    unidade: str          # número da unidade, ex.: "101"
    bloco: str | None     # 2º valor da célula "Unidade", ex.: "01"
    nome: str
    cpf_cnpj: str
    telefones: list[str]
    emails: list[str]

    def to_unit_record(self) -> UnitRecord:
        return UnitRecord(
            Unidade=self.unidade,
            bloco=self.bloco,
            ProprietarioCpfCnpj=self.cpf_cnpj,
            ProprietarioNome=self.nome,
            ProprietarioTelefone1=self.telefones[0] if len(self.telefones) > 0 else None,
            ProprietarioTelefone2=self.telefones[1] if len(self.telefones) > 1 else None,
            ProprietarioEmail1=self.emails[0] if len(self.emails) > 0 else None,
            ProprietarioEmail2=self.emails[1] if len(self.emails) > 1 else None,
        )


@dataclass
class UnitsReport:
    condo_name: str
    rows: list[UnitRow]
    declared_count: int | None = None

    @property
    def records(self) -> list[UnitRecord]:
        return [r.to_unit_record() for r in self.rows]


def _find_unit_columns(pages: list[Page]) -> tuple[dict[str, float], float, int]:
    """Localiza a linha de cabeçalho; devolve (x0 de cada coluna, top, página)."""
    for page_idx, page in enumerate(pages):
        for line in _group_lines(page):
            found = {
                _UNIT_COLUMNS[w["text"]]: w["x0"]
                for w in line if w["text"] in _UNIT_COLUMNS
            }
            if {"unidade", "nome", "cpf_cnpj", "telefone", "email"} <= found.keys():
                return found, line[0]["top"], page_idx
    raise ReportFormatError(
        "Cabeçalho da tabela de unidades não encontrado. "
        "Confira se o PDF é o 'Relatório de Unidades' (proprietários)."
    )


def _column_of(x0: float, starts: list[tuple[float, str]]) -> str | None:
    """Coluna cuja borda esquerda é a maior ≤ x0 (com 3pt de folga)."""
    col = None
    for start, name in starts:
        if x0 >= start - 3:
            col = name
    return col


def _split_phones(tokens: list[str]) -> list[str]:
    """
    Junta DDD + número: ["67", "998706040"] → ["67998706040"].
    DDD "00" é tratado como ausente (a Almah usa 00 quando não há DDD).
    """
    phones: list[str] = []
    ddd: str | None = None
    for tok in tokens:
        digits = re.sub(r"\D", "", tok)
        if not digits:
            continue
        if len(digits) == 2:
            ddd = digits
            continue
        if ddd and ddd != "00":
            phones.append(ddd + digits)
        else:
            phones.append(digits)
        ddd = None
    return phones


def parse_units_pages(pages: list[Page]) -> UnitsReport:
    """Converte as palavras do 'Relatório de Unidades' em UnitsReport."""
    text = _all_text(pages)
    condo = _search(r"Condomínios\s+(.+)", text, "o nome do condomínio").group(1).strip()
    count_m = re.search(r"(\d+)\s+registro\(s\)\s+encontrado", text)

    columns, header_top, header_page = _find_unit_columns(pages)
    starts = sorted((x, name) for name, x in columns.items())
    unit_x = columns["unidade"]
    next_x = min(x for x in columns.values() if x > unit_x)

    rows: list[UnitRow] = []
    current: dict[str, list[str]] | None = None

    def flush() -> None:
        if current is None:
            return
        # A célula "Unidade" traz número e bloco: "101 01" → ("101", "01").
        unit_tokens = current["unidade"]
        rows.append(UnitRow(
            unidade=unit_tokens[0],
            bloco=" ".join(unit_tokens[1:]) or None,
            nome=" ".join(current.get("nome", [])),
            cpf_cnpj="".join(current.get("cpf_cnpj", [])),
            telefones=_split_phones(current.get("telefone", [])),
            emails=[e for e in current.get("email", []) if "@" in e],
        ))

    for page_idx, page in enumerate(pages):
        if page_idx < header_page:
            continue
        for line in _group_lines(page):
            if page_idx == header_page and line[0]["top"] <= header_top + 12:
                continue  # cabeçalho da empresa / da tabela (2 linhas)
            text_line = _line_text(line)
            if any(m in text_line for m in _FOOTER_MARKERS):
                flush()
                current = None
                continue
            first = line[0]
            starts_row = (
                unit_x - 3 <= first["x0"] < next_x - 3
                and re.search(r"\d", first["text"])
            )
            if starts_row:
                flush()
                current = {}
            if current is None:
                continue  # texto antes da primeira linha da tabela na página
            for w in line:
                col = _column_of(w["x0"], starts)
                if col:
                    current.setdefault(col, []).append(w["text"])
    flush()

    if not rows:
        raise ReportFormatError("Nenhuma unidade encontrada no relatório de unidades.")
    return UnitsReport(
        condo_name=condo,
        rows=rows,
        declared_count=int(count_m.group(1)) if count_m else None,
    )


def parse_units_pdf(pdf_path: str | Path) -> UnitsReport:
    return parse_units_pages(load_pages(pdf_path))


# ── Relatório de Inadimplência Resumido ───────────────────────────────────────

@dataclass
class DebtSummaryItem:
    """Uma linha do resumido: 'CLIENTE / UNIDADE' + valor total em atraso."""
    grupo: str        # 1º campo (bloco/torre na numeração da Almah, ex.: "01")
    unidade: str      # número da unidade, ex.: "101"
    valor_total: Decimal


@dataclass
class DebtSummaryReport:
    condo_name: str
    reference_date: str                 # "Boletos em atraso até" (dd/mm/aaaa)
    items: list[DebtSummaryItem] = field(default_factory=list)
    declared_total: Decimal | None = None
    declared_count: int | None = None

    @property
    def computed_total(self) -> Decimal:
        return sum((i.valor_total for i in self.items), Decimal("0"))


def parse_debt_summary_pages(pages: list[Page]) -> DebtSummaryReport:
    """Converte as palavras do 'Relatório de Inadimplência Resumido'."""
    text = _all_text(pages)
    if "RESUMIDO" not in normalize_name(text):
        raise ReportFormatError(
            "O PDF não parece ser o 'Relatório de Inadimplência Resumido'."
        )
    condo = _search(
        r"Condomínio\s+(.+?)\s+Ignorar em", text, "o nome do condomínio"
    ).group(1).strip()
    ref = _search(
        r"Boletos em\s+(\d{2}/\d{2}/\d{4})", text, "a data de referência"
    ).group(1)
    total_m = re.search(r"^TOTAL\s+(\S+)$", text, re.MULTILINE)
    count_m = re.search(r"(\d+)\s+Unidade\(s\)\s+Inadimplente", text)

    items: list[DebtSummaryItem] = []
    for page in pages:
        for line in _group_lines(page):
            parts = [w["text"] for w in line]
            if len(parts) == 3 and _MONEY_RE.match(parts[2]) and re.search(r"\d", parts[1]):
                items.append(DebtSummaryItem(
                    grupo=parts[0], unidade=parts[1], valor_total=parse_money(parts[2])
                ))

    report = DebtSummaryReport(
        condo_name=condo,
        reference_date=ref,
        items=items,
        declared_total=parse_money(total_m.group(1)) if total_m and _MONEY_RE.match(total_m.group(1)) else None,
        declared_count=int(count_m.group(1)) if count_m else None,
    )
    if not items:
        raise ReportFormatError("Nenhuma unidade inadimplente encontrada no resumido.")
    return report


def parse_debt_summary_pdf(pdf_path: str | Path) -> DebtSummaryReport:
    return parse_debt_summary_pages(load_pages(pdf_path))


# ── Relatório de Inadimplência (detalhado: um boleto por linha) ──────────────

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# Cabeçalho: CLI/UNID | DOC | COMPTO. | VENC | VL ORIG | VL PAGTO | MULTA | JUROS |
#            CORREÇÃO MONETÁRIA | VL TOTAL | ACORDO | STATUS
_DETAIL_HEADER = {"CLI/UNID", "DOC", "COMPTO.", "VENC", "STATUS"}


@dataclass
class DebtDetailItem:
    """Um boleto em atraso do relatório detalhado."""
    grupo: str                  # bloco/torre (1º campo de CLI/UNID), ex.: "01"
    unidade: str                # ex.: "101"
    doc: str                    # nº do documento, ex.: "014833"
    nosso_numero: str | None    # nosso número bancário, ex.: "23218909"
    competencia: str            # texto livre da coluna COMPTO. (parcela/descrição)
    vencimento: str             # dd/mm/aaaa
    valor_original: Decimal
    valor_pago: Decimal
    multa: Decimal
    juros: Decimal
    correcao: Decimal
    valor_total: Decimal
    status: str                 # ex.: "Vencido", "Vencido Advogado", "Vencido Acordo"

    @property
    def doc_key(self) -> str:
        """Identificador único do boleto (nosso número; senão o nº do documento)."""
        return self.nosso_numero or self.doc


@dataclass
class DebtDetailReport:
    condo_name: str
    reference_date: str
    items: list[DebtDetailItem] = field(default_factory=list)
    declared_total: Decimal | None = None
    declared_bills: int | None = None   # "N registro(s) encontrado(s)"
    declared_units: int | None = None   # "N Unidade(s) Inadimplente(s)"

    @property
    def computed_total(self) -> Decimal:
        return sum((i.valor_total for i in self.items), Decimal("0"))

    def totals_by_unit(self) -> dict[tuple[str, str], Decimal]:
        totals: dict[tuple[str, str], Decimal] = {}
        for i in self.items:
            key = (i.grupo, i.unidade)
            totals[key] = totals.get(key, Decimal("0")) + i.valor_total
        return totals


def _find_detail_columns(pages: list[Page]) -> dict[str, float]:
    for page in pages:
        for line in _group_lines(page):
            texts = {w["text"] for w in line}
            if _DETAIL_HEADER <= texts:
                return {w["text"]: w["x0"] for w in line}
    raise ReportFormatError(
        "Cabeçalho do relatório de inadimplência detalhado não encontrado. "
        "Confira se o PDF é o 'Relatório de Inadimplência' (não o resumido)."
    )


def parse_debt_detail_pages(pages: list[Page]) -> DebtDetailReport:
    """Converte as palavras do 'Relatório de Inadimplência' (detalhado)."""
    text = _all_text(pages)
    if "RESUMIDO" in normalize_name(text):
        raise ReportFormatError(
            "Este é o relatório RESUMIDO; exporte o 'Relatório de Inadimplência' detalhado."
        )
    cols = _find_detail_columns(pages)
    doc_x, compto_x, venc_x = cols["DOC"], cols["COMPTO."], cols["VENC"]
    status_x = cols["STATUS"]

    condo = _search(r"Condomínio\s+(.+?)\s+Ignorar", text, "o nome do condomínio").group(1).strip()
    ref = _search(r"Boletos em\s+(\d{2}/\d{2}/\d{4})", text, "a data de referência").group(1)
    bills_m = re.search(r"(\d+)\s*registro\(s\)\s+encontrado", text)
    units_m = re.search(r"(\d+)\s+Unidade\(s\)\s+Inadimplente", text)
    total_m = re.search(r"^TOTAL(?:\s+\S+)*?\s+(\S+)$", text, re.MULTILINE)

    items: list[DebtDetailItem] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        doc_tokens = [t for t in current["doc"] if t not in ("BL", "NN:")]
        nn = None
        if "NN:" in current["doc"]:
            after = current["doc"][current["doc"].index("NN:") + 1:]
            nn = after[0] if after else None
            doc_tokens = [t for t in doc_tokens if t != nn]
        m = current["money"]
        items.append(DebtDetailItem(
            grupo=current["grupo"],
            unidade=current["unidade"],
            doc=doc_tokens[0] if doc_tokens else "",
            nosso_numero=nn,
            competencia=" ".join(current["compto"]),
            vencimento=current["venc"],
            valor_original=m[0], valor_pago=m[1], multa=m[2],
            juros=m[3], correcao=m[4], valor_total=m[5],
            status=" ".join(current["status"]),
        ))
        current = None

    for page in pages:
        for line in _group_lines(page):
            texts = [w["text"] for w in line]
            joined = " ".join(texts)
            if joined.startswith("Emitido em") or "registro(s)" in joined or texts[0] == "TOTAL":
                flush()
                continue
            money = [parse_money(t) for t in texts if _MONEY_RE.match(t)]
            first = line[0]
            starts_row = (
                first["x0"] < doc_x - 30          # coluna CLI
                and len(line) > 1
                and line[1]["x0"] < doc_x - 3      # coluna UNID
                and any(_DATE_RE.match(t) for t in texts)
                and len(money) >= 6
            )
            if starts_row:
                flush()
                current = {
                    "grupo": line[0]["text"], "unidade": line[1]["text"],
                    "doc": [], "compto": [], "status": [],
                    "venc": next(t for t in texts if _DATE_RE.match(t)),
                    "money": money[-6:],
                }
                rest = line[2:]
            elif len(money) >= 6:
                flush()  # linha de subtotal da unidade
                continue
            elif current is not None:
                rest = line
            else:
                continue  # cabeçalhos de página/tabela
            for w in rest:
                if _MONEY_RE.match(w["text"]) or _DATE_RE.match(w["text"]):
                    continue
                if w["x0"] >= status_x - 5:
                    current["status"].append(w["text"])
                elif compto_x - 3 <= w["x0"] < venc_x - 3:
                    current["compto"].append(w["text"])
                elif doc_x - 3 <= w["x0"] < compto_x - 3:
                    current["doc"].append(w["text"])
    flush()

    if not items:
        raise ReportFormatError("Nenhum boleto encontrado no relatório de inadimplência.")
    declared_total = None
    if total_m and _MONEY_RE.match(total_m.group(1)):
        declared_total = parse_money(total_m.group(1))
    return DebtDetailReport(
        condo_name=condo,
        reference_date=ref,
        items=items,
        declared_total=declared_total,
        declared_bills=int(bills_m.group(1)) if bills_m else None,
        declared_units=int(units_m.group(1)) if units_m else None,
    )


def parse_debt_detail_pdf(pdf_path: str | Path) -> DebtDetailReport:
    return parse_debt_detail_pages(load_pages(pdf_path))
