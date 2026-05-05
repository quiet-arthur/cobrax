"""
test_almah_session.py — Testes unitários para AlmahSession.

Valida:
  - Inicialização com ADM válida/inválida
  - Login (sucesso e falha HTTP)
  - switch_condominio (sucesso, falha, sessão não autenticada)
  - get_units e get_bills com respostas mockadas
  - Re-autenticação automática em caso de 401/403
  - group_by_adm — agrupamento correto por administradora
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import httpx

from src.adapters.almah_scraper import AlmahSession, AlmahScraper
from src.main import group_by_adm


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_credentials():
    """Mocka get_adm_credentials para retornar credenciais fake."""
    with patch("src.adapters.almah_scraper.get_adm_credentials") as mock:
        mock.return_value = {"user": "test_user", "password": "test_pass"}
        yield mock


@pytest.fixture
def session(mock_credentials):
    """Cria uma AlmahSession para ADM 'alpha' com credenciais mockadas."""
    s = AlmahSession("alpha")
    yield s
    s.close()


@pytest.fixture
def authenticated_session(session):
    """Retorna uma AlmahSession com flag de autenticação ativa."""
    session._is_authenticated = True
    return session


# ── Testes de Inicialização ───────────────────────────────────────────────────


class TestAlmahSessionInit:
    """Testes de inicialização da sessão."""

    def test_init_valid_adm(self, mock_credentials):
        """ADM válida deve inicializar sem erros."""
        session = AlmahSession("alpha")
        assert session.adm == "alpha"
        assert session._is_authenticated is False
        assert session._current_condom_id is None
        session.close()

    def test_init_invalid_adm(self):
        """ADM inválida deve levantar ValueError."""
        with pytest.raises(ValueError, match="não encontrada"):
            AlmahSession("administradora_inexistente")

    def test_backward_compat_alias(self):
        """AlmahScraper deve ser um alias para AlmahSession."""
        assert AlmahScraper is AlmahSession

    def test_context_manager(self, mock_credentials):
        """Deve funcionar como context manager (with statement)."""
        with AlmahSession("alpha") as s:
            assert isinstance(s, AlmahSession)
        # Após sair do with, a sessão deve estar fechada
        assert s._session.is_closed


# ── Testes de Login ───────────────────────────────────────────────────────────


class TestLogin:
    """Testes do método login()."""

    def test_login_success(self, session):
        """Login bem-sucedido deve setar _is_authenticated = True."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        session._session.post = MagicMock(return_value=mock_response)

        result = session.login()

        assert result is True
        assert session._is_authenticated is True
        session._session.post.assert_called_once()

    def test_login_http_error(self, session):
        """Erro HTTP no login deve retornar False."""
        session._session.post = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "401", request=MagicMock(), response=MagicMock()
            )
        )

        result = session.login()

        assert result is False
        assert session._is_authenticated is False

    def test_login_unexpected_error(self, session):
        """Erro inesperado no login deve retornar False sem propagar exceção."""
        session._session.post = MagicMock(side_effect=ConnectionError("timeout"))

        result = session.login()

        assert result is False
        assert session._is_authenticated is False


# ── Testes de switch_condominio ───────────────────────────────────────────────


class TestSwitchCondominio:
    """Testes do método switch_condominio()."""

    def test_switch_success(self, authenticated_session):
        """Troca de contexto com sucesso deve atualizar _current_condom_id."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        authenticated_session._session.get = MagicMock(return_value=mock_response)

        result = authenticated_session.switch_condominio("205")

        assert result is True
        assert authenticated_session._current_condom_id == "205"

    def test_switch_without_auth_raises(self, session):
        """Tentar trocar contexto sem autenticação deve levantar RuntimeError."""
        with pytest.raises(RuntimeError, match="não autenticada"):
            session.switch_condominio("205")

    def test_switch_http_error(self, authenticated_session):
        """Erro HTTP na troca de contexto deve retornar False."""
        authenticated_session._session.get = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock()
            )
        )

        result = authenticated_session.switch_condominio("205")

        assert result is False
        assert authenticated_session._current_condom_id is None


# ── Testes de get_units / get_bills ───────────────────────────────────────────


SAMPLE_UNITS_HTML = """
<table>
  <thead>
    <tr>
      <th>Unidade</th>
      <th>ProprietarioCpfCnpj</th>
      <th>ProprietarioNome</th>
      <th>ProprietarioTelefone1</th>
      <th>ProprietarioTelefone2</th>
      <th>ProprietarioEmail1</th>
      <th>ProprietarioEmail2</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>01</td>
      <td>123.456.789-00</td>
      <td>João Silva</td>
      <td>67999999999</td>
      <td></td>
      <td>joao@test.com</td>
      <td></td>
    </tr>
  </tbody>
</table>
"""

SAMPLE_BILLS_HTML = """
<table>
  <thead>
    <tr>
      <th>Unidade</th>
      <th>Nome do Pagador</th>
      <th>Doc</th>
      <th>Venc</th>
      <th>Vlr Total</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>01</td>
      <td>João Silva</td>
      <td>DOC-001</td>
      <td>01/01/2025</td>
      <td>R$ 500,00</td>
      <td>Aberto</td>
    </tr>
  </tbody>
</table>
"""


class TestGetData:
    """Testes dos métodos get_units() e get_bills()."""

    def test_get_units_success(self, authenticated_session):
        """Deve parsear HTML e retornar lista de UnitRecord."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"d": SAMPLE_UNITS_HTML}
        authenticated_session._session.post = MagicMock(return_value=mock_response)
        # Mock do _get_blocks_mapping (sem blocos)
        authenticated_session._get_blocks_mapping = MagicMock(return_value={})

        units = authenticated_session.get_units("205")

        assert len(units) == 1
        assert units[0].nome == "João Silva"
        assert units[0].cpf_cnpj == "12345678900"
        assert units[0].bloco is None  # Sem mapeamento de bloco

    def test_get_bills_success(self, authenticated_session):
        """Deve parsear HTML e retornar lista de DebtRecord."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"d": SAMPLE_BILLS_HTML}
        authenticated_session._session.post = MagicMock(return_value=mock_response)

        bills = authenticated_session.get_bills("205")

        assert len(bills) == 1
        assert bills[0].doc == "DOC-001"
        assert bills[0].nome_pagador == "João Silva"

    def test_get_units_empty_response(self, authenticated_session):
        """Resposta vazia deve retornar lista vazia."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"d": ""}
        authenticated_session._session.post = MagicMock(return_value=mock_response)

        assert authenticated_session.get_units("205") == []

    def test_get_units_error(self, authenticated_session):
        """Erro genérico deve retornar lista vazia sem propagar exceção."""
        authenticated_session._session.post = MagicMock(
            side_effect=ConnectionError("network error")
        )

        assert authenticated_session.get_units("205") == []


# ── Testes de Enriquecimento de Blocos ─────────────────────────────────────────


# Resposta simulada do DataTable CND00701
SAMPLE_DATATABLE_RESPONSE = {
    "d": {
        "draw": 1,
        "recordsTotal": 3,
        "recordsFiltered": 3,
        "data": [
            {
                "0": "<div>checkbox html</div>",
                "1": "01",
                "2": "A",
                "3": "João Silva",
                "4": "",
                "5": "0,000000000",
                "6": "",
                "7": "<td>Ativo</td>",
                "DT_RowData": {"codigo": "1348"},
            },
            {
                "0": "<div>checkbox html</div>",
                "1": "02",
                "2": "A",
                "3": "Maria Souza",
                "4": "",
                "5": "0,000000000",
                "6": "",
                "7": "<td>Ativo</td>",
                "DT_RowData": {"codigo": "1349"},
            },
            {
                "0": "<div>checkbox html</div>",
                "1": "01",
                "2": "B",
                "3": "Carlos Pereira",
                "4": "",
                "5": "0,000000000",
                "6": "",
                "7": "<td>Ativo</td>",
                "DT_RowData": {"codigo": "1350"},
            },
        ],
    }
}

# HTML com múltiplas unidades para testar cruzamento com blocos
SAMPLE_MULTI_UNITS_HTML = """
<table>
  <thead>
    <tr>
      <th>Unidade</th>
      <th>ProprietarioCpfCnpj</th>
      <th>ProprietarioNome</th>
      <th>ProprietarioTelefone1</th>
      <th>ProprietarioTelefone2</th>
      <th>ProprietarioEmail1</th>
      <th>ProprietarioEmail2</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>01</td>
      <td>12345678900</td>
      <td>João Silva</td>
      <td>67999999999</td>
      <td></td>
      <td>joao@test.com</td>
      <td></td>
    </tr>
    <tr>
      <td>02</td>
      <td>98765432100</td>
      <td>Maria Souza</td>
      <td>67888888888</td>
      <td></td>
      <td>maria@test.com</td>
      <td></td>
    </tr>
    <tr>
      <td>01</td>
      <td>11122233300</td>
      <td>Carlos Pereira</td>
      <td>67777777777</td>
      <td></td>
      <td>carlos@test.com</td>
      <td></td>
    </tr>
  </tbody>
</table>
"""


class TestBlockEnrichment:
    """Testes do mecanismo de enriquecimento de blocos (DataTable CND00701)."""

    def test_get_blocks_mapping_success(self, authenticated_session):
        """
        _get_blocks_mapping deve retornar dict correto com chave NOME_UNIDADE -> bloco.
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = SAMPLE_DATATABLE_RESPONSE
        authenticated_session._session.get = MagicMock(return_value=mock_response)

        mapping = authenticated_session._get_blocks_mapping()

        assert mapping == {
            "João Silva_01": "A",
            "Maria Souza_02": "A",
            "Carlos Pereira_01": "B",
        }

    def test_get_blocks_mapping_graceful_degradation(self, authenticated_session):
        """
        Falha no endpoint de blocos deve retornar dict vazio (sem quebrar o fluxo).
        """
        authenticated_session._session.get = MagicMock(
            side_effect=ConnectionError("network error")
        )

        mapping = authenticated_session._get_blocks_mapping()

        assert mapping == {}

    def test_get_blocks_mapping_empty_data(self, authenticated_session):
        """
        DataTable sem registros deve retornar dict vazio.
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"d": {"data": []}}
        authenticated_session._session.get = MagicMock(return_value=mock_response)

        mapping = authenticated_session._get_blocks_mapping()

        assert mapping == {}

    def test_get_blocks_mapping_normalizes_bloco_prefix(self, authenticated_session):
        """
        Regressão condomínio 164: DataTable retorna 'BLOCO A' em vez de 'A'.
        O mapping deve normalizar para apenas a letra.
        """
        datatable_with_prefix = {
            "d": {
                "data": [
                    {
                        "0": "<div>checkbox</div>",
                        "1": "A - 001",
                        "2": "BLOCO A",
                        "3": "MARILZA GOMES GONÇALVES",
                        "DT_RowData": {"codigo": "100"},
                    },
                    {
                        "0": "<div>checkbox</div>",
                        "1": "B - 001",
                        "2": "BLOCO B",
                        "3": "CARLOS SILVA",
                        "DT_RowData": {"codigo": "101"},
                    },
                ]
            }
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = datatable_with_prefix
        authenticated_session._session.get = MagicMock(return_value=mock_response)

        mapping = authenticated_session._get_blocks_mapping()

        # "BLOCO A" deve ser normalizado para "A"
        assert mapping == {
            "MARILZA GOMES GONÇALVES_A - 001": "A",
            "CARLOS SILVA_B - 001": "B",
        }

    def test_get_units_enriched_with_blocks(self, authenticated_session):
        """
        get_units deve cruzar dados do Exportar + DataTable e preencher o campo bloco.
        João (01) -> Bloco A; Maria (02) -> Bloco A; Carlos (01) -> Bloco B.
        Nota: João e Carlos têm a mesma unidade "01" mas blocos diferentes.
        """
        # Mock do POST (Exportar HTML de unidades)
        mock_html_response = MagicMock()
        mock_html_response.raise_for_status = MagicMock()
        mock_html_response.json.return_value = {"d": SAMPLE_MULTI_UNITS_HTML}
        authenticated_session._session.post = MagicMock(return_value=mock_html_response)

        # Mock do GET (DataTable de blocos)
        mock_dt_response = MagicMock()
        mock_dt_response.raise_for_status = MagicMock()
        mock_dt_response.json.return_value = SAMPLE_DATATABLE_RESPONSE
        authenticated_session._session.get = MagicMock(return_value=mock_dt_response)

        units = authenticated_session.get_units("87")

        assert len(units) == 3

        joao = next(u for u in units if u.nome == "João Silva")
        assert joao.bloco == "A"
        assert joao.unidade == "01"

        maria = next(u for u in units if u.nome == "Maria Souza")
        assert maria.bloco == "A"
        assert maria.unidade == "02"

        carlos = next(u for u in units if u.nome == "Carlos Pereira")
        assert carlos.bloco == "B"
        assert carlos.unidade == "01"

    def test_get_units_without_block_match(self, authenticated_session):
        """
        Quando o DataTable não contém match para uma unidade,
        o campo bloco deve permanecer None.
        """
        mock_html_response = MagicMock()
        mock_html_response.raise_for_status = MagicMock()
        mock_html_response.json.return_value = {"d": SAMPLE_UNITS_HTML}
        authenticated_session._session.post = MagicMock(return_value=mock_html_response)

        # DataTable vazio — nenhum match possível
        mock_dt_response = MagicMock()
        mock_dt_response.raise_for_status = MagicMock()
        mock_dt_response.json.return_value = {"d": {"data": []}}
        authenticated_session._session.get = MagicMock(return_value=mock_dt_response)

        units = authenticated_session.get_units("205")

        assert len(units) == 1
        assert units[0].bloco is None
        assert units[0].nome == "João Silva"

    def test_get_units_block_endpoint_failure_graceful(self, authenticated_session):
        """
        Se o endpoint de blocos falhar, get_units deve retornar unidades
        normalmente, apenas sem o campo bloco preenchido.
        """
        mock_html_response = MagicMock()
        mock_html_response.raise_for_status = MagicMock()
        mock_html_response.json.return_value = {"d": SAMPLE_UNITS_HTML}
        authenticated_session._session.post = MagicMock(return_value=mock_html_response)

        # Simula falha no GET do DataTable
        authenticated_session._session.get = MagicMock(
            side_effect=ConnectionError("network error")
        )

        units = authenticated_session.get_units("205")

        assert len(units) == 1
        assert units[0].bloco is None
        assert units[0].nome == "João Silva"


# ── Testes de Re-autenticação ─────────────────────────────────────────────────


class TestReauth:
    """Testes do mecanismo de re-autenticação automática."""

    def test_reauth_on_401(self, authenticated_session):
        """Deve tentar re-autenticação em caso de 401."""
        mock_401_response = MagicMock()
        mock_401_response.status_code = 401
        mock_success_response = MagicMock()
        mock_success_response.raise_for_status = MagicMock()
        mock_success_response.json.return_value = {"d": SAMPLE_UNITS_HTML}

        # Primeira chamada: 401, segunda (após reauth): sucesso
        authenticated_session._session.post = MagicMock(
            side_effect=[
                httpx.HTTPStatusError(
                    "401",
                    request=MagicMock(),
                    response=mock_401_response,
                ),
                MagicMock(),  # login post
                mock_success_response,  # get_units retry
            ]
        )
        # Mock switch_condominio para sucesso
        authenticated_session._session.get = MagicMock(
            return_value=MagicMock(raise_for_status=MagicMock())
        )

        units = authenticated_session.get_units("205")

        assert len(units) == 1

    def test_reauth_prevents_infinite_loop(self, authenticated_session):
        """Re-autenticação deve acontecer no máximo 1 vez."""
        authenticated_session._reauth_attempted = True

        result = authenticated_session._try_reauth("205")

        assert result is False


# ── Testes de group_by_adm ────────────────────────────────────────────────────


class TestGroupByAdm:
    """Testes da função de agrupamento por administradora."""

    def test_empty_list(self):
        """Lista vazia deve retornar dict vazio."""
        assert group_by_adm([]) == {}

    def test_single_adm(self):
        """Todos da mesma ADM devem ficar em um único grupo."""
        condos = [
            {"id": "1", "name": "A", "adm": "alpha"},
            {"id": "2", "name": "B", "adm": "alpha"},
        ]
        result = group_by_adm(condos)
        assert list(result.keys()) == ["alpha"]
        assert len(result["alpha"]) == 2

    def test_multiple_adms(self):
        """Condomínios de ADMs diferentes devem ser separados corretamente."""
        condos = [
            {"id": "1", "name": "A", "adm": "alpha"},
            {"id": "2", "name": "B", "adm": "expresso"},
            {"id": "3", "name": "C", "adm": "alpha"},
        ]
        result = group_by_adm(condos)
        assert set(result.keys()) == {"alpha", "expresso"}
        assert len(result["alpha"]) == 2
        assert len(result["expresso"]) == 1

    def test_single_condo_interactive(self):
        """Cenário interativo com 1 condo deve gerar grupo com 1 item."""
        condos = [{"id": "87", "name": "ALBUQUERQUE II", "adm": "expresso"}]
        result = group_by_adm(condos)
        assert list(result.keys()) == ["expresso"]
        assert len(result["expresso"]) == 1


# ── Teste de integração (skipped por padrão) ──────────────────────────────────


@pytest.mark.skip(reason="Needs real .env credentials and hits live Almah API")
def test_almah_session_integration():
    """Testa a integração real com a API da Almah (ignorado por padrão)."""
    import logging
    logging.basicConfig(level=logging.INFO)

    with AlmahSession("alpha") as session:
        if not session.login():
            pytest.fail("Login failed. Check your .env credentials.")

        if not session.switch_condominio("205"):
            pytest.fail("Failed to switch to condominium 205.")

        units = session.get_units("205")
        assert isinstance(units, list)

        bills = session.get_bills("205")
        assert isinstance(bills, list)
