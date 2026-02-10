import os
import logging
import httpx

from config import ADMS_CONFIG, ENDPOINTS
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class AlmahScraper:
    """
    Handles authentication and data extraction from the Almah Web System.
    """
    def __init__(self, condom_id: str, adm: str):
        """_summary_

        Args:
            condom_id (str): _description_
        """ 
        if adm not in ADMS_CONFIG:
            raise ValueError(f"Administradora '{adm}' não encontrada nas configurações")
        
        ## Adms settings
        self.config = ADMS_CONFIG[adm]
        self.ambiente = self.config["ids_ambiente"]
        self.user = os.getenv(self.config["user_login"])
        self.password = os.getenv(self.config["user_password"])

        ## Endpoints settings
        self.endpoints = {
            "login": ENDPOINTS["login"],
            "access": ENDPOINTS["access"],
            "units": ENDPOINTS["units_export"],
            "bills": ENDPOINTS["units_bils_export"],
        }

        ## Dates
        self.default_date = "01/01/2000"
        self.today = datetime.now().strftime("%d/%m/%Y")

        ## Internals
        self.condom_id = condom_id
        self.logger = logging.getLogger(__name__)
        self.session = httpx.Client(timeout=30.0)
        self._is_authenticated = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def login_and_set_cookies(self) -> bool:
        """
        Performs the login request and sets the session cookies.
        """
        # 1. Login Request
        try:
            resp_login = self.session.post(
                self.config["url"] + self.endpoints["login"],
                json={"login": self.user, "senhaCriptografada": self.password}
            )
            resp_login.raise_for_status()

            data = resp_login.json()

            # 2. Set cookies
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
        ## Retomar o fetching de dados
        
        except httpx.HTTPError as e:
            self.logger.error(f"Login HTTP error: {e}")
            return False
            
        except Exception as e:
            self.logger.error(f"Login unexpected error: {e}")
            return False
                

def main():
    AlphaClient = AlmahScraper("205", "alpha")

    print(AlphaClient.login_and_set_cookies())

if __name__ == "__main__":
    main()