"""
Parser do webhook da WhatsApp Cloud API
---------------------------------------
Camada anticorrupção: converte o JSON aninhado que a Meta envia em eventos
planos e tipados (StatusEvent, MessageEvent). Não acessa banco nem rede.

Formato (resumido) de um POST da Meta:

    {"object": "whatsapp_business_account",
     "entry": [{"id": "<WABA_ID>",
                "changes": [{"field": "messages",
                             "value": {"metadata": {"phone_number_id": "..."},
                                       "contacts": [{"wa_id": "...", "profile": {"name": "..."}}],
                                       "messages": [...],     # mensagens recebidas
                                       "statuses": [...]}}]}]} # status das que enviamos

Um mesmo POST pode trazer vários entries/changes, e cada change pode trazer
mensagens e status ao mesmo tempo.

O parser é tolerante: campos ausentes ou tipos de evento desconhecidos são
ignorados (com log), nunca levantam exceção. Uma mudança de formato da Meta
não pode derrubar o receptor.

Também verifica a assinatura `X-Hub-Signature-256` (HMAC-SHA256 do corpo
bruto com o App Secret), que prova que o POST veio da Meta.
"""
import datetime
import hashlib
import hmac
import logging
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

SIGNATURE_PREFIX: str = "sha256="


@dataclass(frozen=True)
class StatusEvent:
    """Mudança de status de uma mensagem que NÓS enviamos."""
    wamid: str
    status: str                                   # sent | delivered | read | failed
    timestamp: datetime.datetime
    recipient: str | None = field(default=None)
    error_code: int | None = field(default=None)
    error_message: str | None = field(default=None)


@dataclass(frozen=True)
class MessageEvent:
    """Mensagem enviada POR UM CONTATO para o nosso número."""
    wamid: str
    sender: str
    kind: str                                     # text | button | interactive | image | ...
    timestamp: datetime.datetime
    sender_name: str | None = field(default=None)
    text: str | None = field(default=None)        # corpo do texto, ou rótulo do botão clicado
    button_payload: str | None = field(default=None)
    context_wamid: str | None = field(default=None)  # wamid da nossa mensagem respondida


@dataclass
class WebhookBatch:
    statuses: list[StatusEvent] = field(default_factory=list)
    messages: list[MessageEvent] = field(default_factory=list)


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """
    Confere o cabeçalho X-Hub-Signature-256 ("sha256=<hex>").

    Falha fechada: sem App Secret configurado ou sem cabeçalho, rejeita.
    Usa compare_digest para não vazar informação pelo tempo de comparação.
    """
    if not app_secret or not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header[len(SIGNATURE_PREFIX):]
    return hmac.compare_digest(expected, received)


def parse_webhook(payload: dict, *, phone_number_id: str | None = None) -> WebhookBatch:
    """
    Extrai os eventos de um POST do webhook.

    Args:
        payload:          JSON já decodificado.
        phone_number_id:  Se informado, ignora eventos de outros números do
                          mesmo WABA (o webhook é por app, não por número).
    """
    batch = WebhookBatch()
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        logger.warning(f"[Webhook] Objeto inesperado: {payload.get('object') if isinstance(payload, dict) else type(payload)}")
        return batch

    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                logger.info(f"[Webhook] Campo ignorado: {change.get('field')}")
                continue
            value = change.get("value") or {}

            event_number = (value.get("metadata") or {}).get("phone_number_id")
            if phone_number_id and event_number and str(event_number) != str(phone_number_id):
                logger.info(f"[Webhook] Evento de outro número ({event_number}) ignorado.")
                continue

            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or []
            }

            for raw in value.get("statuses") or []:
                event = _parse_status(raw)
                if event:
                    batch.statuses.append(event)

            for raw in value.get("messages") or []:
                event = _parse_message(raw, names)
                if event:
                    batch.messages.append(event)

    return batch


# ── Private ───────────────────────────────────────────────────────────────────

def _parse_timestamp(raw: object) -> datetime.datetime | None:
    """Timestamp Unix (string) → datetime local ingênuo, como no resto do Cobrax."""
    try:
        return datetime.datetime.fromtimestamp(int(raw))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_status(raw: dict) -> StatusEvent | None:
    wamid, status = raw.get("id"), raw.get("status")
    timestamp = _parse_timestamp(raw.get("timestamp"))
    if not wamid or not status or timestamp is None:
        logger.warning(f"[Webhook] Status incompleto ignorado: {raw}")
        return None

    error_code: int | None = None
    error_message: str | None = None
    errors = raw.get("errors") or []
    if errors:
        err = errors[0]
        try:
            error_code = int(err.get("code")) if err.get("code") is not None else None
        except (TypeError, ValueError):
            error_code = None
        details = (err.get("error_data") or {}).get("details")
        error_message = details or err.get("message") or err.get("title")

    return StatusEvent(
        wamid=wamid,
        status=status,
        timestamp=timestamp,
        recipient=raw.get("recipient_id"),
        error_code=error_code,
        error_message=error_message,
    )


def _parse_message(raw: dict, names: dict[str | None, str | None]) -> MessageEvent | None:
    wamid, sender, kind = raw.get("id"), raw.get("from"), raw.get("type")
    timestamp = _parse_timestamp(raw.get("timestamp"))
    if not wamid or not sender or not kind or timestamp is None:
        logger.warning(f"[Webhook] Mensagem incompleta ignorada: {raw}")
        return None

    text: str | None = None
    payload: str | None = None
    if kind == "text":
        text = (raw.get("text") or {}).get("body")
    elif kind == "button":
        # Botão de resposta rápida de TEMPLATE (ex: "Ver detalhes")
        button = raw.get("button") or {}
        text, payload = button.get("text"), button.get("payload")
    elif kind == "interactive":
        # Botão/lista de mensagem interativa (texto livre com botões)
        interactive = raw.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text, payload = reply.get("title"), reply.get("id")

    return MessageEvent(
        wamid=wamid,
        sender=sender,
        kind=kind,
        timestamp=timestamp,
        sender_name=names.get(sender),
        text=text,
        button_payload=payload,
        context_wamid=(raw.get("context") or {}).get("id"),
    )
