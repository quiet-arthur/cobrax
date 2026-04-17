import os
import pytest
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from src.adapters.almah_scraper import AlmahScraper

load_dotenv('.env')

@pytest.mark.skip(reason="Needs real .env credentials")
def test_almah_login_headers():
    with AlmahScraper('27', 'alpha') as scraper:
        scraper.login_and_set_cookies()
        resp = scraper.session.post(
            scraper.config["url"] + scraper.endpoints["units"],
            json={"codigoCondominio": "27"}
        )
        html = resp.json().get('d', '')
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table')
        
        assert table is not None, "No table found in the HTML response."
        assert table.thead is not None, "Table has no thead."
        
        headers = [th.get_text(strip=True) for th in table.thead.find_all('th')]
        assert len(headers) > 0, "No headers extracted."
