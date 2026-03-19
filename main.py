import json
import logging
import sys
from src.adapters.almah_scraper import AlmahScraper
from src.repositories.database import engine, Base, SessionLocal
from src.services.processor import sync_data

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

def main():
    # Init DB
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    condos = load_condominios()
    if not condos:
        logging.warning("Nenhum condomínio configurado em condominios.json.")
        return

    # Check for CLI args to run a specific one
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
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
        # Batch run all
        selected = condos

    logging.info(f"Iniciando processamento de {len(selected)} condomínio(s)...")
    for c in selected:
        process_condominio(c, db)
    
    db.close()

if __name__ == "__main__":
    main()
