"""
test_schemas.py — Testes unitários para os schemas Pydantic do domínio.

Valida:
  - UnitRecord: campo bloco (novo), limpeza de CPF, limpeza de telefone
  - DebtRecord: parsing correto dos aliases
"""

import pytest
from pydantic import ValidationError

from src.domain.schemas import DebtRecord, UnitRecord


# ── UnitRecord ────────────────────────────────────────────────────────────────


class TestUnitRecord:
    """Testes do schema UnitRecord."""

    def test_minimal_valid_unit(self) -> None:
        """Deve criar UnitRecord com campos obrigatórios preenchidos."""
        record = UnitRecord(
            Unidade="01",
            ProprietarioCpfCnpj="123.456.789-00",
            ProprietarioNome="João Silva",
        )
        assert record.unidade == "01"
        assert record.cpf_cnpj == "12345678900"
        assert record.nome == "João Silva"
        assert record.bloco is None

    def test_bloco_field_accepts_value(self) -> None:
        """Campo bloco deve aceitar string quando fornecido."""
        record = UnitRecord(
            Unidade="03",
            ProprietarioCpfCnpj="99868237149",
            ProprietarioNome="Maria",
            bloco="A",
        )
        assert record.bloco == "A"

    def test_bloco_field_defaults_to_none(self) -> None:
        """Campo bloco deve ser None por padrão (condomínio sem blocos)."""
        record = UnitRecord(
            Unidade="01",
            ProprietarioCpfCnpj="12345678900",
            ProprietarioNome="Carlos",
        )
        assert record.bloco is None

    def test_cpf_cleaned_of_punctuation(self) -> None:
        """CPF com pontuação deve ser limpo para apenas dígitos."""
        record = UnitRecord(
            Unidade="01",
            ProprietarioCpfCnpj="123.456.789-00",
            ProprietarioNome="Test",
        )
        assert record.cpf_cnpj == "12345678900"

    def test_phone_cleaned_of_punctuation(self) -> None:
        """Telefone com caracteres não-numéricos deve ser limpo."""
        record = UnitRecord(
            Unidade="01",
            ProprietarioCpfCnpj="12345678900",
            ProprietarioNome="Test",
            ProprietarioTelefone1="(67) 99999-9999",
        )
        assert record.telefone1 == "67999999999"

    def test_empty_phone_becomes_none(self) -> None:
        """Telefone vazio deve virar None."""
        record = UnitRecord(
            Unidade="01",
            ProprietarioCpfCnpj="12345678900",
            ProprietarioNome="Test",
            ProprietarioTelefone1="",
        )
        assert record.telefone1 is None

    def test_bloco_can_be_mutated_post_creation(self) -> None:
        """
        Bloco deve ser mutável após criação do record,
        pois o Adapter faz enriquecimento pós-parse.
        """
        record = UnitRecord(
            Unidade="05",
            ProprietarioCpfCnpj="12345678900",
            ProprietarioNome="Test",
        )
        assert record.bloco is None
        record.bloco = "B"
        assert record.bloco == "B"


# ── DebtRecord ────────────────────────────────────────────────────────────────


class TestDebtRecord:
    """Testes do schema DebtRecord."""

    def test_valid_debt_record(self) -> None:
        """Deve criar DebtRecord com todos os campos de alias corretos."""
        record = DebtRecord(
            **{
                "Unidade": "B 03",
                "Nome do Pagador": "João Silva",
                "Doc": "DOC-001",
                "Venc": "01/01/2025",
                "Vlr Total": "R$ 500,00",
                "Status": "Vencido",
            }
        )
        assert record.unidade == "B 03"
        assert record.nome_pagador == "João Silva"
        assert record.doc == "DOC-001"
        assert record.status == "Vencido"

    def test_missing_required_field_raises(self) -> None:
        """Campo obrigatório ausente deve levantar ValidationError."""
        with pytest.raises(ValidationError):
            DebtRecord(
                **{
                    "Unidade": "01",
                    "Nome do Pagador": "Test",
                    # "Doc" ausente
                    "Venc": "01/01/2025",
                    "Status": "Vencido",
                }
            )
