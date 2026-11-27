"""Testes do laço de --send-outbox (run_outbox), com um sender falso."""
import logging

from src.main import run_outbox
from src.services.outbox_sender import SendSummary


class FakeSender:
    """Devolve um SendSummary roteirizado por chamada de process_due."""

    def __init__(self, batches: list[SendSummary]):
        self.batches = list(batches)
        self.calls = 0

    def process_due(self, db, *, limit):
        self.calls += 1
        return self.batches.pop(0) if self.batches else SendSummary()


def test_para_quando_o_lote_vem_incompleto():
    sender = FakeSender([SendSummary(sent=10), SendSummary(sent=3, failed=1), SendSummary(sent=99)])
    total = run_outbox(None, sender, batch_size=10)
    assert sender.calls == 2
    assert (total.sent, total.failed) == (13, 1)


def test_para_imediatamente_em_falha_de_autenticacao():
    sender = FakeSender([SendSummary(sent=10), SendSummary(halted=True, halt_reason="token expirado")])
    total = run_outbox(None, sender, batch_size=10)
    assert sender.calls == 2
    assert total.halted and total.halt_reason == "token expirado"


def test_respeita_limite_de_lotes(caplog):
    sender = FakeSender([SendSummary(sent=10)] * 5)
    with caplog.at_level(logging.WARNING):
        total = run_outbox(None, sender, batch_size=10, max_batches=3)
    assert sender.calls == 3 and total.sent == 30
    assert "Limite de 3 lotes" in caplog.text


def test_fila_vazia_faz_uma_chamada():
    sender = FakeSender([])
    total = run_outbox(None, sender, batch_size=10)
    assert sender.calls == 1 and total.sent == 0
