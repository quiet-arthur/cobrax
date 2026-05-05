from pydantic import BaseModel, Field, field_validator
from typing import Optional
import re

class DebtRecord(BaseModel):
    unidade: str = Field(alias="Unidade")
    nome_pagador: str = Field(alias="Nome do Pagador")
    doc: str = Field(alias="Doc")
    vencimento: str = Field(alias="Venc")
    valor_total: Optional[str] = Field(None, alias="Vlr Total")  # não utilizado no fluxo
    status: str = Field(alias="Status")

class UnitRecord(BaseModel):
    unidade: str = Field(alias="Unidade")
    bloco: Optional[str] = None
    cpf_cnpj: str = Field(alias="ProprietarioCpfCnpj")
    nome: str = Field(alias="ProprietarioNome")
    telefone1: Optional[str] = Field(None, alias="ProprietarioTelefone1")
    telefone2: Optional[str] = Field(None, alias="ProprietarioTelefone2")
    email1: Optional[str] = Field(None, alias="ProprietarioEmail1")
    email2: Optional[str] = Field(None, alias="ProprietarioEmail2")

    @field_validator('cpf_cnpj')
    def clean_cpf(cls, v: str) -> str:
        # Remove non-digits
        cv = re.sub(r"\D", "", v)
        return cv

    @field_validator('telefone1', 'telefone2')
    def clean_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        cv = re.sub(r"\D", "", str(v))
        return cv
