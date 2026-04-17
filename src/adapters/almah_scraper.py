import os
import logging
import httpx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from src.config.settings import ADMS_CONFIG, ENDPOINTS
from datetime import datetime
from dotenv import load_dotenv
from src.domain.schemas import UnitRecord, DebtRecord

load_dotenv()

class AlmahScraper:
    """
    Handles authentication and data extraction from the Almah Web System.
    Validates data using Pydantic.
    """
    def __init__(self, condom_id: str, adm: str):
        if adm not in ADMS_CONFIG:
            raise ValueError(f"Administradora '{adm}' não encontrada nas configurações")
        
        self.config = ADMS_CONFIG[adm]
        self.ambiente = self.config["ids_ambiente"]
        self.user = os.getenv(self.config["user_login"])
        self.password = os.getenv(self.config["user_password"])

        self.endpoints = {
            "login": ENDPOINTS["login"],
            "access": ENDPOINTS["access"],
            "units": ENDPOINTS["units_export"],
            "bills": ENDPOINTS["units_bils_export"],
        }

        self.default_date = "01/01/2000"
        self.today = datetime.now().strftime("%d/%m/%Y")

        self.condom_id = condom_id
        self.logger = logging.getLogger(__name__)
        self.session = httpx.Client(timeout=30.0)
        self._is_authenticated = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.session.close()

    def login_and_set_cookies(self) -> bool:
        try:
            resp_login = self.session.post(
                self.config["url"] + self.endpoints["login"],
                json={"login": self.user, "senhaCriptografada": self.password}
            )
            resp_login.raise_for_status()

            params = {
                "Empresa": self.ambiente["id_empresa"],
                "Estabelecimento": self.ambiente["id_estabelecimento"],
                "PerfilUso": self.ambiente["id_perfil_de_uso"],
                "Usuario": self.ambiente["id_usuario"],
                "Condominio": self.condom_id,
            }

            self.session.get(
                self.config["url"] + self.endpoints["access"],
                params=params,
                follow_redirects=True
            ).raise_for_status() 

            self._is_authenticated = True
            self.logger.info("Authentication successful.")
            return True 
        except httpx.HTTPError as e:
            self.logger.error(f"Login HTTP error: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Login unexpected error: {e}")
            return False

    def get_units(self) -> list[UnitRecord]:
        if not self._is_authenticated:
            if not self.login_and_set_cookies():
                return []
        
        try:
            resp = self.session.post(
                self.config["url"] + self.endpoints["units"],
                json={"codigoCondominio": self.condom_id}
            )
            resp.raise_for_status()
            html = resp.json().get('d', '')
            if not html:
                return []
            return self._parse_html(html, UnitRecord)
        except Exception as e:
            self.logger.error(f"Error fetching units: {e}")
            return []

    def get_bills(self) -> list[DebtRecord]:
        if not self._is_authenticated:
            if not self.login_and_set_cookies():
                return []
        
        payload = {
            "dataInadimplencia": self.today,
            "listaCondominio": self.condom_id,
            "dataVencimentoInicial": self.default_date,
            "dataVencimentoFinal": self.today,
            "tipoRelatorio": "D"
        }
        
        try:
            resp = self.session.post(
                self.config["url"] + self.endpoints["bills"],
                json=payload
            )
            resp.raise_for_status()
            html = resp.json().get('d', '')
            if not html:
                return []
            return self._parse_html(html, DebtRecord)
        except Exception as e:
            self.logger.error(f"Error fetching bills: {e}")
            return []

    def _parse_html(self, html: str, model_class) -> list:
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table')
        if not table:
            return []

        headers = [th.get_text(strip=True) for th in table.thead.find_all('th')] if table.thead else []
        records = []
        
        if table.tbody:
            for row in table.tbody.find_all('tr'):
                data = [' '.join(td.get_text(strip=True).split()) for td in row.find_all('td')]
                if len(data) != len(headers):
                    continue
                row_dict = dict(zip(headers, data))
                try:
                    record = model_class(**row_dict)
                    records.append(record)
                except ValidationError as e:
                    self.logger.warning(f"Validation error for {model_class.__name__}: {e}")
                    
        return records

def main():
    with AlmahScraper("205", "alpha") as AlphaClient:
        print(AlphaClient.login_and_set_cookies())

if __name__ == "__main__":
    main()