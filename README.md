# COBRAX 🐍

Sistema de automação para cobrança de condomínio. Realiza o scraping de dados financeiros, processa inadimplentes salva em banco de dados e enviará notificações.

## 🛠 Tech Stack (Fase 2)
- **Gerenciador:** uv
- **Scraping & API:** httpx + beautifulsoup4
- **Validação de Dados:** Pydantic
- **Banco de Dados:** SQLite + SQLAlchemy
- **Notificação:** Meta WhatsApp Cloud API (Planejado na Fase 3)

## 🏗 Arquitetura (Clean Architecture)
- `src/domain`: Modelos SQLAlchemy e Schemas Pydantic.
- `src/adapters`: Integração com API externa (Almah).
- `src/services`: Processador de regras de negócio (Threshold de 90 dias, tags de não notificar).
- `src/repositories`: Conexão unificada com o banco SQLite.

## 🚀 Como rodar

1. **Instalar dependências:**
   Certifique-se de ter o `uv` instalado.
   ```bash
   uv sync