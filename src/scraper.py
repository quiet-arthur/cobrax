import os
import httpx

from dotenv import load_dotenv
from config import ADMS_CONFIG, ENDPOINTS

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

        self.config = ADMS_CONFIG[adm]
        self.ambiente = self.config["ids_ambiente"]

        self.user = os.getenv(self.config["user_login"])
        self.password = os.getenv(self.config["user_password"])

def main():
    AlphaClient = AlmahScraper("123", "alpha")

    print(AlphaClient.config)


main()