"""
import_reports.py — Importa os relatórios em PDF exportados da Almah.

Uso (rodar na raiz do projeto):
    python -m src.import_reports --unidades "Relatório de Unidades.pdf" \
        --inadimplencia "Relatório Inadimplência.pdf" [--gravar] [--simular-envio] [--detalhado]

--inadimplencia aceita o relatório DETALHADO ("Relatório de Inadimplência",
um boleto por linha) ou o RESUMIDO; o tipo é detectado pelo conteúdo.

Sem flags: só lê, agrupa os boletos por unidade, liga ao proprietário e mostra
o resultado. Nada é gravado nem enviado.
  --gravar         grava unidades e boletos em data/cobrax.db via sync_data
                   (o mesmo caminho da extração automática). Só com o DETALHADO.
  --simular-envio  depois de gravar, roda o módulo de notificação em modo
                   simulação (dry-run) para este condomínio. Nada é enviado.
  --detalhado      inclui o nome do proprietário na listagem (dados pessoais).
"""

import argparse
import logging
import sys
from pathlib import Path

from src.adapters.pdf_reports import (
    ReportFormatError,
    load_pages,
    normalize_name,
    parse_debt_detail_pages,
    parse_debt_summary_pages,
    parse_units_pdf,
)
from src.services.report_importer import link_detail_reports, link_reports


def _brl(value) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _print_summary_mode(units, debts, args) -> int:
    result = link_reports(units, debts)
    c = result.condo
    print(f"Condomínio: {c['name']} (id {c['id']}, adm {c['adm']}) — relatório RESUMIDO")
    print(f"Inadimplência em: {result.reference_date}")
    print(f"Unidades no cadastro: {len(units.rows)} | inadimplentes: {len(debts.items)} "
          f"| total: {_brl(result.total_value)}")
    print(f"Ligadas ao cadastro: {len(result.linked)} | sem cadastro: {len(result.unmatched)}")
    print(f"Com telefone válido p/ WhatsApp: {len(result.with_valid_phone)} "
          f"| sem nenhum contato: {len(result.without_contact)}")
    for w in result.warnings:
        print(f"AVISO: {w}")
    print()
    for d in result.linked:
        tel = "tel ok" if d.phones_for_notification else "SEM TEL"
        mail = "e-mail ok" if d.has_email else "sem e-mail"
        name = f" | {d.unit_record.nome}" if args.detalhado else ""
        print(f"  Bloco {d.item.grupo} - {d.item.unidade:>5} | {_brl(d.item.valor_total):>14} | {tel:7} | {mail}{name}")
    for i in result.unmatched:
        print(f"  Bloco {i.grupo} - {i.unidade:>5} | {_brl(i.valor_total):>14} | NÃO ENCONTRADA NO CADASTRO")
    print()
    print("Obs.: o RESUMIDO não traz vencimentos; exporte o 'Relatório de Inadimplência' "
          "(detalhado) para gravar e aplicar a regra dos 90 dias.")
    if args.gravar or args.simular_envio:
        print("ERRO: --gravar/--simular-envio exigem o relatório detalhado.", file=sys.stderr)
        return 1
    return 0


def _print_detail_mode(units, detail, args) -> int:
    result = link_detail_reports(units, detail)
    c = result.condo
    statuses: dict[str, int] = {}
    for u in result.linked:
        for b in u.bills:
            statuses[b.status] = statuses.get(b.status, 0) + 1

    print(f"Condomínio: {c['name']} (id {c['id']}, adm {c['adm']}) — relatório DETALHADO")
    print(f"Inadimplência em: {result.reference_date}")
    print(f"Unidades no cadastro: {len(units.rows)} | inadimplentes: {len(result.linked) + len({(b.grupo, b.unidade) for b in result.unmatched})} "
          f"| boletos: {result.bills_count} | total: {_brl(result.total_value)}")
    print(f"Ligadas ao cadastro: {len(result.linked)} | boletos sem cadastro: {len(result.unmatched)}")
    print(f"Com telefone válido p/ WhatsApp: {len(result.with_valid_phone)} "
          f"| sem nenhum contato: {len(result.without_contact)}")
    print("Status dos boletos: " + ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items())))
    for w in result.warnings:
        print(f"AVISO: {w}")
    print()
    for u in result.linked:
        tel = "tel ok" if u.phones_for_notification else "SEM TEL"
        mail = "e-mail ok" if u.has_email else "sem e-mail"
        name = f" | {u.unit_record.nome}" if args.detalhado else ""
        print(f"  Bloco {u.grupo} - {u.unidade:>5} | {len(u.bills):3} boleto(s) | desde "
              f"{u.oldest_due_date:%d/%m/%Y} | {_brl(u.total):>14} | {tel:7} | {mail}{name}")
    for b in result.unmatched:
        print(f"  Bloco {b.grupo} - {b.unidade:>5} | boleto {b.doc_key} | NÃO ENCONTRADA NO CADASTRO")

    if not (args.gravar or args.simular_envio):
        print("\nNada foi gravado. Use --gravar para gravar no banco local.")
        return 0

    # Só agora toca no banco: data/ precisa existir antes de o SQLite abrir o arquivo.
    Path("data").mkdir(exist_ok=True)
    from src.repositories.database import Base, SessionLocal, engine
    from src.services.processor import sync_data

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        unit_records, debt_records = result.sync_records()
        sync_data(str(c["id"]), c["adm"], unit_records, debt_records, db)
        print(f"\nGravado em data/cobrax.db: {len(unit_records)} unidades, {len(debt_records)} boletos.")
        if args.simular_envio:
            from src.main import run_notifications
            print("\n=== Simulação do envio (dry-run) — nada é enviado ===")
            run_notifications(db, dry_run=True, condominium_ids=[str(c["id"])])
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa relatórios em PDF da Almah.")
    parser.add_argument("--unidades", required=True, help="PDF 'Relatório de Unidades'")
    parser.add_argument("--inadimplencia", required=True,
                        help="PDF 'Relatório de Inadimplência' (detalhado) ou o Resumido")
    parser.add_argument("--gravar", action="store_true", help="grava no banco local")
    parser.add_argument("--simular-envio", action="store_true",
                        help="grava e simula as notificações (dry-run)")
    parser.add_argument("--detalhado", action="store_true", help="mostra o nome do proprietário")
    args = parser.parse_args(argv)
    if args.simular_envio:
        args.gravar = True
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        units = parse_units_pdf(args.unidades)
        debt_pages = load_pages(args.inadimplencia)
        is_summary = "RESUMIDO" in normalize_name(
            " ".join(w["text"] for p in debt_pages[:1] for w in p)
        )
        if is_summary:
            return _print_summary_mode(units, parse_debt_summary_pages(debt_pages), args)
        return _print_detail_mode(units, parse_debt_detail_pages(debt_pages), args)
    except (ReportFormatError, FileNotFoundError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
