# COBRAX 🐍

Sistema de automação para cobrança de condomínio. Realiza o scraping de dados financeiros, processa inadimplentes salva em banco de dados e enviará notificações.

## 🛠 Tech Stack (Fase 2)
- **Gerenciador:** uv
- **Scraping & API:** httpx + beautifulsoup4
- **Validação de Dados:** Pydantic
- **Banco de Dados:** SQLite + SQLAlchemy
- **Notificação:** Whatsapp - Evolution API 

## 🏗 Estrutura do Projeto (Clean Architecture)
O sistema opera segmentando domínios limpos sem atrito de infraestrutura (`src/`):
- `src/domain/`: Regras e Models (Pydantic / SQLAlchemy).
- `src/adapters/`: Interfaces (Almah APIs, Requisições).
- `src/services/`: Orquestradores das regras de negócio.
- `src/repositories/`: Classes de banco de dados e consultas.
- `tests/`: Suíte automatizada sob paradigma `pytest`.
- `scripts/`: Utilitários isolados (ex: `migrate_db.py`).
- `data/`: Persistência local ignorada no repositório (`cobrax.db`).

## 🚀 Como Rodar e Testar

1. **Instalar dependências (Padrão e Dev):**
   Certifique-se de ter o `uv` instalado.
   ```bash
   uv sync
   ```

2. **Executar a Aplicação:**
   Para simular o disparo de notificações de dívidas (sem enviar nada para os números reais), use:
   ```bash
   uv run python -m src.main --dry-run
   ```
   Para enviar as notificações de dívidas (requer confirmação explícita), use:
   ```bash
   uv run python -m src.main --notify
   ```
   Para escolher especificamente qual condomínio você quer atualizar a base interativamente e enviar notificações, use as flags combinadas:
   ```bash
   uv run python -m src.main --interactive --notify
   ```

3. **Executar Testes de Regressão e Cobertura:**
   ```bash
   uv run pytest tests/ --cov=src --cov-report=term-missing
   ```