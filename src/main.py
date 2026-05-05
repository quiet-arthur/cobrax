"""
main.py — Ponto de entrada do sistema Cobrax.

Fluxo principal:
  1. Carrega configuração de condomínios (condominios.json)
  2. Agrupa condomínios por administradora (ADM)
  3. Para cada ADM, abre UMA sessão autenticada e processa todos os condos do lote
  4. Opcionalmente executa notificações (--dry-run ou --notify)

Flags:
  --interactive  : Escolhe condomínio(s) interativamente
  --condo <id>   : Filtra sincronização E notificações para um condomínio
  --dry-run      : Simula notificações (sem envio real)
  --notify       : Envia notificações de verdade
  (sem flags)    : Sincroniza dados apenas, sem notificações
"""

import json
import logging
import sys
from itertools import groupby
from operator import itemgetter

from sqlalchemy.orm import Session

from src.adapters.almah_scraper import AlmahSession
from src.adapters.evolution_client import EvolutionAPIClient
from src.config.settings import EVOLUTION_CONFIG
from src.repositories.database import Base, SessionLocal, engine
from src.services.notifier import NotificationService
from src.services.processor import sync_data


def load_condominios(filepath: str = "src/config/condominios.json") -> list[dict]:
    """Carrega a lista de condomínios do arquivo JSON de configuração."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load {filepath}: {e}")
        return []


def group_by_adm(condos: list[dict]) -> dict[str, list[dict]]:
    """
    Agrupa condomínios pelo campo 'adm'.

    Args:
        condos: Lista de dicts com ao menos as chaves 'id', 'name', 'adm'.

    Returns:
        Dict mapeando cada ADM à sua lista de condomínios.
        Exemplo: {"alpha": [...], "expresso": [...]}
    """
    sorted_condos = sorted(condos, key=itemgetter("adm"))
    return {
        adm: list(group)
        for adm, group in groupby(sorted_condos, key=itemgetter("adm"))
    }


def process_adm_batch(adm: str, condos: list[dict], db: Session) -> None:
    """
    Processa todos os condomínios de uma administradora com sessão compartilhada.

    Login: 1 vez por ADM. Troca de contexto: N vezes (1 por condomínio).
    Falha em um condomínio individual não interrompe os demais.

    Args:
        adm:    Chave da administradora ("alpha" ou "expresso").
        condos: Lista de condomínios a processar neste lote.
        db:     Sessão SQLAlchemy ativa.
    """
    logging.info(f"=== Iniciando lote da ADM '{adm}' ({len(condos)} condomínio(s)) ===")

    with AlmahSession(adm) as session:
        if not session.login():
            logging.error(f"Falha na autenticação da ADM '{adm}'. Pulando lote inteiro.")
            return

        for condom in condos:
            condom_id: str = str(condom["id"])
            condom_name: str = condom["name"]
            logging.info(f"--- Processando: {condom_name} ({condom_id}) ---")

            if not session.switch_condominio(condom_id):
                logging.error(f"Falha ao trocar contexto para {condom_name}. Pulando.")
                continue

            units = session.get_units(condom_id)
            logging.info(f"[{condom_name}] Unidades encontradas: {len(units)}")

            bills = session.get_bills(condom_id)
            logging.info(f"[{condom_name}] Boletos encontrados: {len(bills)}")

            if units or bills:
                sync_data(condom_id, adm, units, bills, db)

    logging.info(f"=== Lote da ADM '{adm}' finalizado ===")


def run_notifications(
    db: Session,
    dry_run: bool = False,
    condominium_ids: list[str] | None = None,
) -> None:
    """
    Executa o fluxo de notificação via Evolution API (WhatsApp).

    Args:
        db:               Sessão SQLAlchemy ativa.
        dry_run:          Se True, apenas loga as mensagens sem enviá-las de fato.
        condominium_ids:  Se fornecido (lista não vazia), notifica apenas as
                          unidades dos condomínios listados. Se None, notifica
                          todos os condomínios.
    """
    mode = "DRY-RUN" if dry_run else "REAL"
    scope = f"condomínios {condominium_ids}" if condominium_ids else "todos os condomínios"
    logging.info(f"=== Notificações [{mode}] | Escopo: {scope} ===")
    with EvolutionAPIClient(
        base_url=EVOLUTION_CONFIG["base_url"],
        api_key=EVOLUTION_CONFIG["api_key"],
        instance=EVOLUTION_CONFIG["instance"],
    ) as client:
        service = NotificationService(client, db, dry_run=dry_run)
        stats = service.run(condominium_ids=condominium_ids)
    logging.info(f"=== Notificações encerradas | Resultado: {stats} ===")


def main() -> None:
    """Ponto de entrada principal do Cobrax."""
    # Init DB
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    condos = load_condominios()
    if not condos:
        logging.warning("Nenhum condomínio configurado em condominios.json.")
        return

    # ── Modo de execução ───────────────────────────────────────────────────────
    # --interactive      : escolhe condomínio(s) interativamente
    # --condo <id>       : filtra sincronização E notificações para um condomínio
    # --dry-run          : simula notificações (sem envio real)
    # --notify           : envia notificações de verdade (requer confirmação explícita)
    # (sem flags)        : sincroniza dados apenas, sem notificações

    interactive = "--interactive" in sys.argv
    dry_run     = "--dry-run"     in sys.argv
    do_notify   = "--notify"      in sys.argv

    # Extrai --condo <id> se presente
    condo_filter: str | None = None
    if "--condo" in sys.argv:
        idx = sys.argv.index("--condo")
        if idx + 1 < len(sys.argv):
            condo_filter = sys.argv[idx + 1]
            # Valida se o id existe em condominios.json
            known_ids = {str(c["id"]) for c in condos}
            if condo_filter not in known_ids:
                logging.error(
                    f"Condomínio '{condo_filter}' não encontrado em condominios.json. "
                    f"IDs válidos: {sorted(known_ids)}"
                )
                db.close()
                return
        else:
            logging.error("Flag --condo requer um ID como argumento. Exemplo: --condo 4")
            db.close()
            return

    # Determina quais condomínios sincronizar
    if condo_filter:
        # --condo restringe também o sync ao condomínio informado
        selected = [c for c in condos if str(c["id"]) == condo_filter]
        logging.info(f"Modo filtrado: sincronizando apenas '{selected[0]['name']}' ({condo_filter})")
    elif interactive:
        print("Selecione um condomínio para processar:")
        for i, c in enumerate(condos):
            print(f"[{i+1}] {c['name']}")
        print(f"[{len(condos)+1}] TODOS")

        val = input("Opção: ")
        try:
            choice = int(val)
            if choice == len(condos) + 1:
                selected = condos
            elif 1 <= choice <= len(condos):
                selected = [condos[choice-1]]
            else:
                print("Opção inválida.")
                return
        except ValueError:
            print("Entrada inválida.")
            return
    else:
        selected = condos

    # ── Processamento por ADM (sessão compartilhada) ───────────────────────────
    adm_groups = group_by_adm(selected)
    total_condos = sum(len(batch) for batch in adm_groups.values())
    logging.info(
        f"Iniciando processamento de {total_condos} condomínio(s) "
        f"em {len(adm_groups)} administradora(s)..."
    )
    for adm, batch in adm_groups.items():
        process_adm_batch(adm, batch, db)

    # ── Notificações ─────────────────────────────────────────────────────────────────────
    # Deriva lista de IDs selecionados para restringir o escopo das notificações.
    # Se todos os condomínios foram selecionados, passa None (sem filtro).
    all_selected = len(selected) == len(condos)
    notify_ids: list[str] | None = (
        None if all_selected else [str(c["id"]) for c in selected]
    )

    if dry_run:
        run_notifications(db, dry_run=True, condominium_ids=notify_ids)
    elif do_notify:
        run_notifications(db, dry_run=False, condominium_ids=notify_ids)
    else:
        logging.info(
            "Notificações não executadas. Use --dry-run (simulação) ou --notify (envio real)."
        )

    db.close()


if __name__ == "__main__":
    main()
