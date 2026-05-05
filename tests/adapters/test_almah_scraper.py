"""
test_almah_scraper.py — Teste de integração legado para AlmahSession.

Mantido para backward-compatibility. Usa o alias AlmahScraper → AlmahSession.
"""

import pytest
import logging

from src.adapters.almah_scraper import AlmahScraper


@pytest.mark.skip(reason="Needs real .env credentials and hits live Almah API")
def test_almah_scraper_integration():
    """Testa a integração real com a API da Almah via alias legado."""
    logging.basicConfig(level=logging.INFO)
    with AlmahScraper("alpha") as session:
        if session.login():
            session.switch_condominio("205")
            units = session.get_units("205")
            assert isinstance(units, list)

            bills = session.get_bills("205")
            assert isinstance(bills, list)
        else:
            pytest.fail("Login failed. Check your .env credentials.")