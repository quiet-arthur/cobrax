import json
import logging
import sys
from src.adapters.almah_scraper import AlmahScraper
from src.adapters.evolution_client import EvolutionAPIClient
from src.repositories.database import engine, Base, SessionLocal
from src.services.processor import sync_data
from src.services.notifier import NotificationService
from src.config.settings import EVOLUTION_CONFIG

def load_condominios(filepath="src/config/condominios.json"):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load {filepath}: {e}")
        return []

def process_condominio(condom, db):
    condom_id = condom["id"]
    condom_name = condom["name"]
    adm = condom.get("adm", "alpha")
    
    logging.info(f"--- Processando Condomínio: {condom_name} ({condom_id}) ---")
    
    with AlmahScraper(condom_id, adm) as scraper:
        if not scraper.login_and_set_cookies():
            logging.error(f"Falha na autenticação para {condom_name}")
            return
        
        units = scraper.get_units()
        logging.info(f"[{condom_name}] Unidades encontradas: {len(units)}")
        
        bills = scraper.get_bills()
        logging.info(f"[{condom_name}] Boletos encontrados: {len(bills)}")
        
        if units or bills:
            sync_data(condom_id, units, bills, db)


def run_notifications(db, dry_run: bool = False, condominium_id: str | None = None):
    """
    Executa o fluxo de notificação via Evolution API (WhatsApp).

    Args:
        dry_run:        Se True, apenas loga as mensagens sem enviá-las de fato.
        condominium_id: Se fornecido, notifica apenas as unidades deste condomínio.
    """
    mode = "DRY-RUN" if dry_run else "REAL"
    scope = f"condomínio {condominium_id}" if condominium_id else "todos os condomínios"
    logging.info(f"=== Notificações [{mode}] | Escopo: {scope} ===")
    with EvolutionAPIClient(
        base_url=EVOLUTION_CONFIG["base_url"],
        api_key=EVOLUTION_CONFIG["api_key"],
        instance=EVOLUTION_CONFIG["instance"],
    ) as client:
        service = NotificationService(client, db, dry_run=dry_run)
        stats = service.run(condominium_id=condominium_id)
    logging.info(f"=== Notificações encerradas | Resultado: {stats} ===")

def main():
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

    logging.info(f"Iniciando processamento de {len(selected)} condomínio(s)...")
    for c in selected:
        process_condominio(c, db)

    # ── Notificações ───────────────────────────────────────────────────────────
    if dry_run:
        run_notifications(db, dry_run=True, condominium_id=condo_filter)
    elif do_notify:
        run_notifications(db, dry_run=False, condominium_id=condo_filter)
    else:
        logging.info(
            "Notificações não executadas. Use --dry-run (simulação) ou --notify (envio real)."
        )

    db.close()


if __name__ == "__main__":
    main()
