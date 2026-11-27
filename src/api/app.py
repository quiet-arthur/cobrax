"""
Receptor do webhook da WhatsApp Cloud API (FastAPI).

Rotas:
  GET  /webhook  → handshake de verificação: a Meta envia hub.verify_token e
                   hub.challenge; devolvemos o challenge se o token confere.
  POST /webhook  → eventos (status de entrega e mensagens recebidas). Só é
                   processado se a assinatura X-Hub-Signature-256 for válida.
  GET  /health   → verificação simples para monitoramento.

Sobre as respostas ao POST:
  - 200: evento processado (ou ignorado de propósito). A Meta para de tentar.
  - 403: assinatura inválida — não veio da Meta.
  - 500: erro nosso (ex: banco travado). A Meta reentrega depois; como o
         processamento é idempotente (status não regridem, mensagens são
         únicas por wamid), reprocessar é seguro.

Execução local:
    uv run uvicorn src.api.app:app --port 8000
"""
import json
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

import src.domain.models  # noqa: F401 — registra as tabelas no metadata antes do create_all
from src.adapters.whatsapp_webhook import parse_webhook, verify_signature
from src.repositories.database import Base
from src.services.webhook_handler import ButtonHandler, WebhookHandler


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookConfig:
    app_secret: str
    verify_token: str
    phone_number_id: str | None = None


def create_app(
    config: WebhookConfig,
    engine: Engine,
    button_handlers: dict[str, ButtonHandler] | None = None,
) -> FastAPI:
    """Monta a aplicação. Recebe config e engine por parâmetro para os testes."""
    if not config.app_secret:
        logger.error("[Webhook] WHATSAPP_APP_SECRET vazio: todo POST será rejeitado (403).")
    if not config.verify_token:
        logger.error("[Webhook] WHATSAPP_VERIFY_TOKEN vazio: o handshake da Meta vai falhar.")

    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    handler = WebhookHandler(button_handlers)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(bind=engine)
        yield

    app = FastAPI(title="Cobrax — webhook WhatsApp", lifespan=lifespan, docs_url=None, redoc_url=None)

    def get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify(
        mode: str | None = Query(None, alias="hub.mode"),
        token: str | None = Query(None, alias="hub.verify_token"),
        challenge: str | None = Query(None, alias="hub.challenge"),
    ) -> Response:
        if (
            mode == "subscribe"
            and config.verify_token
            and token is not None
            and secrets.compare_digest(token, config.verify_token)
        ):
            logger.info("[Webhook] Handshake de verificação aceito.")
            return PlainTextResponse(challenge or "")
        logger.warning("[Webhook] Handshake recusado (token incorreto ou modo inválido).")
        return PlainTextResponse("forbidden", status_code=403)

    # Rota síncrona de propósito: o FastAPI roda em thread pool, então o acesso
    # (síncrono) ao SQLAlchemy não trava o event loop.
    @app.post("/webhook")
    def receive(request_body: bytes = Depends(_raw_body),
                signature: str | None = Depends(_signature_header),
                db: Session = Depends(get_db)) -> Response:
        if not verify_signature(request_body, signature, config.app_secret):
            logger.warning("[Webhook] POST com assinatura inválida rejeitado.")
            return PlainTextResponse("invalid signature", status_code=403)

        try:
            payload = json.loads(request_body)
        except ValueError:
            logger.warning("[Webhook] Corpo assinado, mas não é JSON válido.")
            return PlainTextResponse("invalid json", status_code=400)

        batch = parse_webhook(payload, phone_number_id=config.phone_number_id)
        summary = handler.handle(db, batch)
        logger.info(f"[Webhook] {summary}")
        return PlainTextResponse("ok")

    return app


async def _raw_body(request: Request) -> bytes:
    # A assinatura é calculada sobre os bytes EXATOS recebidos: não pode ser
    # verificada sobre um JSON já decodificado e re-serializado.
    return await request.body()


def _signature_header(request: Request) -> str | None:
    return request.headers.get("X-Hub-Signature-256")


def _build_default_app() -> FastAPI:
    from src.config.settings import CLOUD_API_CONFIG
    from src.repositories.database import engine

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return create_app(
        WebhookConfig(
            app_secret=CLOUD_API_CONFIG["app_secret"],
            verify_token=CLOUD_API_CONFIG["verify_token"],
            phone_number_id=CLOUD_API_CONFIG["phone_number_id"] or None,
        ),
        engine,
    )


app = _build_default_app()
