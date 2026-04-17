import sys
import pytest
import logging
from src.adapters.almah_scraper import AlmahScraper

@pytest.mark.skip(reason="Needs real .env credentials and hits live Almah API")
def test_almah_scraper_integration():
    """Testa a integração real com a API da Almah (ignorado por padrão)."""
    logging.basicConfig(level=logging.INFO)
    with AlmahScraper("205", "alpha") as scraper:
        if scraper.login_and_set_cookies():
            units = scraper.get_units()
            assert isinstance(units, list)
            
            bills = scraper.get_bills()
            assert isinstance(bills, list)
        else:
            pytest.fail("Login failed. Check your .env credentials.")