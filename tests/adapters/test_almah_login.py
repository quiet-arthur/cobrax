"""
test_almah_login.py — Teste de integração legado para login e extração de headers.

Mantido para backward-compatibility. Usa AlmahSession com a nova API.
"""

import pytest
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from src.adapters.almah_scraper import AlmahSession

load_dotenv(".env")


@pytest.mark.skip(reason="Needs real .env credentials")
def test_almah_login_headers():
    """Testa login e extração de headers da tabela de unidades."""
    with AlmahSession("alpha") as session:
        session.login()
        session.switch_condominio("27")

        resp = session._session.post(
            session.config["url"] + session.endpoints["units"],
            json={"codigoCondominio": "27"},
        )
        html = resp.json().get("d", "")
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")

        assert table is not None, "No table found in the HTML response."
        assert table.thead is not None, "Table has no thead."

        headers = [th.get_text(strip=True) for th in table.thead.find_all("th")]
        assert len(headers) > 0, "No headers extracted."
