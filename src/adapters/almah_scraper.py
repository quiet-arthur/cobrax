"""
almah_scraper.py — Sessão autenticada com o sistema Almah.

Responsabilidades (camada Adapter):
  - Autenticação HTTP com a administradora (login único por sessão)
  - Troca de contexto de condomínio via /novo_acesso.aspx (leve, sem re-login)
  - Extração de dados de unidades e boletos via endpoints ASMX
  - Validação dos dados extraídos usando schemas Pydantic do domínio

A classe AlmahSession mantém um único httpx.Client por administradora,
permitindo processar N condomínios com apenas 1 login.
"""

import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pydantic import ValidationError

from src.config.settings import ADMS_CONFIG, ENDPOINTS, get_adm_credentials
from src.domain.schemas import DebtRecord, UnitRecord

load_dotenv()

logger = logging.getLogger(__name__)


class AlmahSession:
    """
    Sessão autenticada com uma administradora Almah.

    Mantém o httpx.Client aberto e permite trocar o condomínio ativo
    sem re-autenticação. O login é feito uma única vez por sessão;
    a troca de contexto usa apenas GET /novo_acesso.aspx.

    Usage:
        with AlmahSession("alpha") as session:
            session.login()
            for condo_id in ["27", "4", "205"]:
                session.switch_condominio(condo_id)
                units = session.get_units(condo_id)
                bills = session.get_bills(condo_id)
    """

    def __init__(self, adm: str) -> None:
        if adm not in ADMS_CONFIG:
            raise ValueError(f"Administradora '{adm}' não encontrada nas configurações")

        self.adm: str = adm
        self.config: dict = ADMS_CONFIG[adm]
        self.ambiente: dict = self.config["ids_ambiente"]

        credentials = get_adm_credentials(adm)
        self.user: str = credentials["user"]
        self.password: str = credentials["password"]

        self.endpoints: dict[str, str] = {
            "login": ENDPOINTS["login"],
            "access": ENDPOINTS["access"],
            "units": ENDPOINTS["units_export"],
            "units_datatable": ENDPOINTS["units_datatable"],
            "bills": ENDPOINTS["units_bils_export"],
        }

        self.default_date: str = "01/01/2000"
        self.today: str = datetime.now().strftime("%d/%m/%Y")

        self._current_condom_id: str | None = None
        self._is_authenticated: bool = False
        self._session: httpx.Client = httpx.Client(timeout=30.0)

    def __enter__(self) -> "AlmahSession":
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object) -> None:
        self.close()

    def close(self) -> None:
        """Fecha o httpx.Client subjacente."""
        self._session.close()

    # ── Autenticação ───────────────────────────────────────────────────────────

    def login(self) -> bool:
        """
        Autentica na administradora. Deve ser chamado UMA vez por sessão.

        Returns:
            True se o login foi bem-sucedido, False caso contrário.
        """
        try:
            resp = self._session.post(
                self.config["url"] + self.endpoints["login"],
                json={"login": self.user, "senhaCriptografada": self.password},
            )
            resp.raise_for_status()

            self._is_authenticated = True
            logger.info(f"[{self.adm}] Autenticação bem-sucedida.")
            return True
        except httpx.HTTPError as e:
            logger.error(f"[{self.adm}] Erro HTTP no login: {e}")
            return False
        except Exception as e:
            logger.error(f"[{self.adm}] Erro inesperado no login: {e}")
            return False

    # ── Troca de Contexto ──────────────────────────────────────────────────────

    def switch_condominio(self, condom_id: str) -> bool:
        """
        Troca o condomínio ativo na sessão autenticada.

        Reutiliza os cookies de autenticação existentes e faz apenas
        GET /novo_acesso.aspx — muito mais leve que um login completo.

        Args:
            condom_id: ID do condomínio para ativar.

        Returns:
            True se a troca foi bem-sucedida, False caso contrário.

        Raises:
            RuntimeError: Se a sessão não estiver autenticada.
        """
        if not self._is_authenticated:
            raise RuntimeError("Sessão não autenticada. Chame login() primeiro.")

        params = {
            "Empresa": self.ambiente["id_empresa"],
            "Estabelecimento": self.ambiente["id_estabelecimento"],
            "PerfilUso": self.ambiente["id_perfil_de_uso"],
            "Usuario": self.ambiente["id_usuario"],
            "Condominio": condom_id,
        }

        try:
            self._session.get(
                self.config["url"] + self.endpoints["access"],
                params=params,
                follow_redirects=True,
            ).raise_for_status()

            self._current_condom_id = condom_id
            logger.info(f"[{self.adm}] Contexto trocado para condomínio {condom_id}.")
            return True
        except httpx.HTTPError as e:
            logger.error(f"[{self.adm}] Falha ao trocar contexto para {condom_id}: {e}")
            return False

    # ── Extração de Dados ──────────────────────────────────────────────────────

    def get_units(self, condom_id: str) -> list[UnitRecord]:
        """
        Busca a lista de unidades do condomínio informado.

        Consolida dados de duas fontes:
          1. Endpoint Exportar (HTML) → dados de contato completos (CPF, telefones, e-mails).
          2. Endpoint DataTable (JSON) → mapeamento Unidade → Bloco.

        O cruzamento é feito em memória pela chave composta (nome + unidade),
        garantindo que o campo `bloco` do UnitRecord seja preenchido antes
        de retornar ao chamador.

        Args:
            condom_id: ID do condomínio (deve coincidir com o contexto ativo).

        Returns:
            Lista de UnitRecord validados pelo Pydantic, com campo `bloco` enriquecido.
        """
        try:
            resp = self._session.post(
                self.config["url"] + self.endpoints["units"],
                json={"codigoCondominio": condom_id},
            )
            resp.raise_for_status()
            html = resp.json().get("d", "")
            if not html:
                return []
            records = self._parse_html(html, UnitRecord)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403) and self._try_reauth(condom_id):
                return self.get_units(condom_id)
            logger.error(f"[{self.adm}] Erro HTTP ao buscar unidades: {e}")
            return []
        except Exception as e:
            logger.error(f"[{self.adm}] Erro ao buscar unidades: {e}")
            return []

        # Enriquecimento: cruzar com datatable de blocos
        blocks_map = self._get_blocks_mapping()
        for record in records:
            key = f"{record.nome}_{record.unidade}"
            record.bloco = blocks_map.get(key)

        return records

    def get_bills(self, condom_id: str) -> list[DebtRecord]:
        """
        Busca a lista de boletos inadimplentes do condomínio informado.

        Args:
            condom_id: ID do condomínio (deve coincidir com o contexto ativo).

        Returns:
            Lista de DebtRecord validados pelo Pydantic.
        """
        payload = {
            "dataInadimplencia": self.today,
            "listaCondominio": condom_id,
            "dataVencimentoInicial": self.default_date,
            "dataVencimentoFinal": self.today,
            "tipoRelatorio": "D",
        }

        try:
            resp = self._session.post(
                self.config["url"] + self.endpoints["bills"],
                json=payload,
            )
            resp.raise_for_status()
            html = resp.json().get("d", "")
            if not html:
                return []
            return self._parse_html(html, DebtRecord)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403) and self._try_reauth(condom_id):
                return self.get_bills(condom_id)
            logger.error(f"[{self.adm}] Erro HTTP ao buscar boletos: {e}")
            return []
        except Exception as e:
            logger.error(f"[{self.adm}] Erro ao buscar boletos: {e}")
            return []

    # ── Métodos Internos ───────────────────────────────────────────────────────

    def _get_blocks_mapping(self) -> dict[str, str]:
        """
        Consulta o DataTable de unidades (CND00701) para obter o mapeamento
        de cada unidade ao seu bloco correspondente.

        A resposta JSON contém um array "data" onde cada item possui:
          - "1": número da unidade (ex: "01" ou "A - 001")
          - "2": identificador do bloco (ex: "A" ou "BLOCO A")
          - "3": nome do proprietário (ex: "JOÃO SILVA")

        Normalização aplicada:
          - Bloco: remove prefixo "BLOCO " se presente (ex: "BLOCO A" → "A").
          - Unidade: usa o valor original como chave (sem remover prefixo),
            pois precisa bater exatamente com o UnitRecord que veio do Exportar.

        Returns:
            Dict com chave "NOME_UNIDADE" e valor sendo a letra do bloco
            já normalizada. Dict vazio em caso de falha (graceful degradation).
        """
        try:
            resp = self._session.get(
                self.config["url"] + self.endpoints["units_datatable"],
            )
            resp.raise_for_status()
            payload = resp.json()

            # O endpoint pode retornar {"d": {"data": [...]}} ou {"data": [...]}
            data_container = payload.get("d", payload)
            rows: list[dict] = data_container.get("data", [])

            mapping: dict[str, str] = {}
            for row in rows:
                unit_number: str = row.get("1", "").strip()
                block_raw: str = row.get("2", "").strip()
                owner_name: str = row.get("3", "").strip()

                # Normaliza: "BLOCO A" → "A", "BLOCO B" → "B", "A" → "A"
                block_letter: str = (
                    block_raw.removeprefix("BLOCO").strip()
                    if block_raw.upper().startswith("BLOCO")
                    else block_raw
                )

                if unit_number and owner_name:
                    key = f"{owner_name}_{unit_number}"
                    mapping[key] = block_letter

            logger.info(
                f"[{self.adm}] Mapeamento de blocos carregado: "
                f"{len(mapping)} unidades mapeadas."
            )
            return mapping

        except Exception as e:
            logger.warning(
                f"[{self.adm}] Falha ao buscar mapeamento de blocos "
                f"(graceful degradation): {e}"
            )
            return {}

    def _try_reauth(self, condom_id: str) -> bool:
        """
        Tenta re-autenticação automática em caso de sessão expirada (401/403).

        Executa no máximo 1 tentativa para evitar loops infinitos.

        Returns:
            True se re-autenticação + troca de contexto foram bem-sucedidas.
        """
        if getattr(self, "_reauth_attempted", False):
            logger.error(f"[{self.adm}] Re-autenticação já tentada. Abortando.")
            return False

        self._reauth_attempted = True
        logger.warning(f"[{self.adm}] Sessão expirada. Tentando re-autenticação...")

        self._is_authenticated = False
        if self.login() and self.switch_condominio(condom_id):
            self._reauth_attempted = False  # Reset para o próximo condomínio
            return True

        logger.error(f"[{self.adm}] Re-autenticação falhou.")
        return False

    def _parse_html(self, html: str, model_class: type) -> list:
        """
        Extrai registros de uma tabela HTML e valida com o schema Pydantic informado.

        Args:
            html: String HTML contendo uma <table>.
            model_class: Classe Pydantic (UnitRecord ou DebtRecord).

        Returns:
            Lista de instâncias validadas do model_class.
        """
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        headers = (
            [th.get_text(strip=True) for th in table.thead.find_all("th")]
            if table.thead
            else []
        )
        records: list = []

        if table.tbody:
            for row in table.tbody.find_all("tr"):
                data = [
                    " ".join(td.get_text(strip=True).split())
                    for td in row.find_all("td")
                ]
                if len(data) != len(headers):
                    continue
                row_dict = dict(zip(headers, data))
                try:
                    record = model_class(**row_dict)
                    records.append(record)
                except ValidationError as e:
                    logger.warning(f"Erro de validação para {model_class.__name__}: {e}")

        return records


# ── Backward-compatibility alias ──────────────────────────────────────────────
# Permite que imports existentes como `from src.adapters.almah_scraper import AlmahScraper`
# continuem funcionando durante a migração.
AlmahScraper = AlmahSession


def main() -> None:
    """Função auxiliar para teste manual rápido."""
    with AlmahSession("alpha") as session:
        print(session.login())


if __name__ == "__main__":
    main()