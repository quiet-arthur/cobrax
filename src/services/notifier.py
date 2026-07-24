"""
NotificationService
-------------------
Orquestra o fluxo completo de notificação de inadimplência via WhatsApp:

  1. Verifica conexão da instância Evolution
  2. Consulta unidades elegíveis via processor.get_units_pending_notification()
  3. Para cada unidade:
       a. Normaliza telefone(s) para formato E.164 (BR)
       b. Formata mensagem com dados da unidade e condomínio
       c. Tenta enviar via telefone principal; em caso de erro, tenta telefone2 (fallback)
       d. Em caso de sucesso: arquiva o chat + registra last_notified_at + grava log de auditoria
       e. Em caso de falha ou skip: grava log de auditoria com detalhe do motivo
  4. Aplica rate limiting (sleep) entre envios para evitar bloqueios do WhatsApp
"""
import datetime
import json
import logging
import pathlib
import re
import time

from sqlalchemy.orm import Session

from src.adapters.evolution_client import EvolutionAPIClient, SendResult
from src.domain.models import NotificationLog, Unit
from src.services.processor import get_units_pending_notification


logger = logging.getLogger(__name__)

# ── Constantes de negócio ──────────────────────────────────────────────────────

OVERDUE_DAYS: int = 90       # Débito deve ter ≥ 90 dias de atraso para qualificar
COOLDOWN_DAYS: int = 30      # Intervalo mínimo entre notificações para a mesma unidade
MSG_DELAY_MS: int = 1500     # Delay de "digitação" simulado na API
RATE_LIMIT_SLEEP: float = 3  # Segundos de espera entre envios consecutivos

CHANNEL = "whatsapp"
TEMPLATE_NAME = "overdue_90d_whatsapp_v1"  # versão do template — incrementar ao mudar o texto

# ── Template de mensagem ───────────────────────────────────────────────────────

MESSAGE_TEMPLATE = """\
Olá, *{owner_name}*! 👋

Identificamos débitos em aberto referentes à unidade *{unit_name}* do condomínio *{condo_name}* com mais de 90 dias de vencimento.

Por favor, entre em contato com a administradora para regularizar sua situação e evitar medidas administrativas e legais.

Caso já tenha realizado o pagamento, favor desconsiderar essa mensagem!"""

# ── Caminho do config de condomínios ──────────────────────────────────────────

_CONDOS_CONFIG_PATH = (
    pathlib.Path(__file__).parent.parent / "config" / "condominios.json"
)


# ── Funções auxiliares ─────────────────────────────────────────────────────────

def _normalize_phone(raw_phone: str) -> str | None:
    """
    Normaliza um número de telefone para o formato E.164 sem '+' (ex: '5567999999999').

    Regras brasileiras:
    - Remove todos os caracteres não-dígitos
    - Remove zero inicial (DDI 0xx)
    - 10 ou 11 dígitos → adiciona código do país 55
    - 12 ou 13 dígitos → assume que já contém DDI 55
    - Comprimento fora de [10, 13] após limpeza → retorna None (número inválido)
    """
    digits = re.sub(r"\D", "", raw_phone or "")
    if not digits:
        return None
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) in (10, 11):
        digits = "55" + digits
    if len(digits) not in (12, 13):
        logger.warning(f"Número ignorado (formato inválido): '{raw_phone}' → '{digits}'")
        return None
    return digits


def _parse_phones(phones_raw: str | None) -> list[str]:
    """
    Retorna lista de números normalizados a partir do campo phones (pode ser CSV).
    Exemplo: "67999999999,6788888888" → ["5567999999999", "556788888888"]
    """
    if not phones_raw:
        return []
    candidates = [p.strip() for p in phones_raw.split(",") if p.strip()]
    result = []
    for p in candidates:
        normalized = _normalize_phone(p)
        if normalized:
            result.append(normalized)
    return result


def _format_message(unit: Unit, condo_name: str) -> str:
    """Renderiza o template de mensagem com os dados da unidade."""
    owner = (unit.owner_name or "Proprietário").title()
    return MESSAGE_TEMPLATE.format(
        owner_name=owner,
        unit_name=unit.unit_name,
        condo_name=condo_name,
    )


def _get_condo_name(condominium_id: str) -> str:
    """
    Resolve o nome amigável do condomínio a partir de condominios.json.
    Retorna um fallback genérico se não encontrar.
    """
    try:
        condos: list[dict] = json.loads(
            _CONDOS_CONFIG_PATH.read_text(encoding="utf-8")
        )
        for c in condos:
            if str(c.get("id", "")) == str(condominium_id):
                return c.get("name", f"Condomínio {condominium_id}")
    except Exception as e:
        logger.warning(f"Não foi possível carregar condominios.json: {e}")
    return f"Condomínio {condominium_id}"


# ── Serviço principal ──────────────────────────────────────────────────────────

class NotificationService:
    """
    Orquestrador do fluxo de notificação de inadimplência.

    Args:
        evolution_client: Instância de EvolutionAPIClient já configurada.
        db:               Sessão SQLAlchemy ativa.
        dry_run:          Se True, apenas loga as mensagens sem enviá-las.
    """

    def __init__(
        self,
        evolution_client: EvolutionAPIClient,
        db: Session,
        dry_run: bool = False,
    ):
        self.client = evolution_client
        self.db = db
        self.dry_run = dry_run

    def run(
        self,
        overdue_days: int = OVERDUE_DAYS,
        cooldown_days: int = COOLDOWN_DAYS,
        condominium_ids: list[str] | None = None,
    ) -> dict:
        """
        Executa o lote completo de notificações.

        Args:
            condominium_ids: Se fornecido (lista não vazia), processa apenas as
                             unidades dos condomínios listados. Se None ou lista
                             vazia, processa todos os condomínios.

        Returns:
            dict com chaves: sent, skipped, errors, total
        """
        stats = {"sent": 0, "skipped": 0, "errors": 0, "total": 0}

        # Verifica conexão antes de processar (falha rápida)
        if not self.dry_run and not self.client.check_connection():
            logger.error(
                "Instância Evolution desconectada. Abortando execução das notificações."
            )
            return stats

        # Busca unidades elegíveis (escopo: condomínios selecionados ou todos)
        scope_label = f"condomínios {condominium_ids}" if condominium_ids else "todos os condomínios"
        logger.info(f"Escopo da notificação: {scope_label}")

        units: list[Unit] = get_units_pending_notification(
            self.db, overdue_days, cooldown_days, condominium_ids=condominium_ids
        )
        stats["total"] = len(units)
        logger.info(f"Unidades elegíveis para notificação: {len(units)}")

        for i, unit in enumerate(units):
            outcome = self._notify_unit(unit)
            if outcome in stats:
                stats[outcome] += 1
            else:
                stats["errors"] += 1

            # Rate limiting: aguarda entre envios (exceto após o último)
            if not self.dry_run and i < len(units) - 1:
                time.sleep(RATE_LIMIT_SLEEP)

        logger.info(
            f"Notificações concluídas — "
            f"Enviadas: {stats['sent']} | "
            f"Puladas: {stats['skipped']} | "
            f"Erros: {stats['errors']} | "
            f"Total elegíveis: {stats['total']}"
        )
        return stats

    # ── Métodos privados ───────────────────────────────────────────────────────

    def _notify_unit(self, unit: Unit) -> str:
        """
        Processa a notificação de uma única unidade.

        Tenta o telefone principal primeiro; em caso de falha, tenta o telefone
        secundário como fallback (se disponível). Grava log de auditoria para
        todos os desfechos.

        Returns:
            'sent'    → mensagem enviada com sucesso (e chat arquivado)
            'skipped' → sem telefone válido cadastrado
            'errors'  → todos os telefones falharam
        """
        phones = _parse_phones(unit.phones)

        if not phones:
            logger.info(
                f"[{unit.unit_name}] Nenhum telefone válido cadastrado — pulando."
            )
            self._write_log(
                unit=unit,
                status="skipped",
                destination=unit.phones or "",
                error_message="Sem telefone válido cadastrado",
            )
            return "skipped"

        condo_name = _get_condo_name(unit.condominium_id)
        message = _format_message(unit, condo_name)

        if self.dry_run:
            logger.info(
                f"[DRY-RUN] [{unit.unit_name}] Enviaria para {phones[0]}:\n{message}\n"
            )
            return "sent"

        # Tenta cada número disponível (principal + fallback)
        for attempt, phone in enumerate(phones):
            label = "principal" if attempt == 0 else f"fallback #{attempt}"
            logger.info(f"[{unit.unit_name}] Tentando envio ({label}) → {phone}")

            result: SendResult = self.client.send_text(
                phone, message, delay_ms=MSG_DELAY_MS
            )

            if result.success:
                # 1. Arquivar o chat para manter a caixa limpa
                msg_key = getattr(result, "_msg_key", {})
                if msg_key:
                    self.client.archive_chat(phone, msg_key)
                else:
                    logger.warning(
                        f"[{unit.unit_name}] Chave da mensagem não encontrada na resposta — "
                        "chat não arquivado automaticamente."
                    )

                # 2. Registrar timestamp da notificação no banco
                self._stamp_notified(unit)

                # 3. Gravar log de auditoria
                self._write_log(unit=unit, status="sent", destination=phone)
                return "sent"
            else:
                logger.warning(
                    f"[{unit.unit_name}] Falha no número {label}: {result.error}"
                )

        # Todos os números falharam
        logger.error(
            f"[{unit.unit_name}] Todos os números falharam. Unidade marcada como erro."
        )
        self._write_log(
            unit=unit,
            status="failed",
            destination=",".join(phones),
            error_message="Todos os números tentados falharam",
        )
        return "errors"

    def _stamp_notified(self, unit: Unit) -> None:
        """Atualiza last_notified_at da unidade no banco de dados."""
        try:
            unit.last_notified_at = datetime.datetime.now()
            self.db.commit()
        except Exception as e:
            logger.error(
                f"[{unit.unit_name}] Erro ao registrar last_notified_at: {e}"
            )
            self.db.rollback()

    def _write_log(
        self,
        unit: Unit,
        status: str,
        destination: str = "",
        error_message: str | None = None,
    ) -> None:
        """
        Grava um registro de auditoria na tabela notification_logs.

        Chamado para TODOS os desfechos: 'sent', 'skipped' e 'failed'.
        Os dados da unidade são desnormalizados no log para garantir
        rastreabilidade mesmo se a unidade for editada/removida no futuro.
        """
        try:
            log = NotificationLog(
                sent_at=datetime.datetime.now(),
                channel=CHANNEL,
                template_name=TEMPLATE_NAME,
                destination=destination,
                status=status,
                error_message=error_message,
                # Dados desnormalizados da unidade
                unit_id=unit.id,
                unit_name=unit.unit_name,
                owner_name=unit.owner_name,
                condominium_id=unit.condominium_id,
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.error(
                f"[{unit.unit_name}] Erro ao gravar log de auditoria: {e}"
            )
            self.db.rollback()
