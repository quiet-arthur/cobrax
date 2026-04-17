# Plano de Refatoração e Implementação Futura (Cobrax)

Este documento delineia as implementações de arquitetura planejadas para elevar o nível de escalabilidade, resiliência e estabilidade do sistema Cobrax. Estes itens foram identificados nas análises iniciais de arquitetura de mercado.

## 1. Implementação de Idempotência e Trilha de Auditoria (Audit Trails)
O envio de notificações e processamento de pagamentos são eventos críticos. Falhas na rede não devem ocasionar cobranças duplicadas ou não notificadas.

*   **Ação:** Criar uma tabela de `NotificationLogs` no banco de dados.
*   **Fluxo Proposto:**
    1. Antes de acionar qualquer serviço externo (WhatsApp/Email), o sistema registra o evento com um `status="PENDING"` e um `correlation_id` único baseado no identificador da dívida + data.
    2. O serviço dispara a notificação na web.
    3. Se houver sucesso, atualiza-se para `status="SENT"`. Se o request falhar com timeout/500, o status será `status="FAILED"`.
    4. Nos próximos ciclos do script, registros "FAILED" ou pendentes há mais de 'X' minutos são reprocessados.

## 2. Resiliência e Tolerância a Falhas em Web Scraping (Retry Patterns)
APIs e sistemas de administração condominial (ex: Almah) são frequentemente instáveis ou lentos, levando a timeouts.

*   **Ação:** Implementação de um padrão de Circuit Breaker ou Retry.
*   **Ferramenta:** Utilizar a biblioteca **Tenacity** do Python na camada `adapters`.
*   **Fluxo Proposto:**
    *   Toda requisição HTTP de extração ou API de envio deve ser reempacotada em decoradores `retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))`.
    *   Logar claramente (via `logger`) qual extração falhou e qual o tempo de backoff.

## 3. Injeção de Dependências e Desacoplamento (DI)
O sistema precisa ser de fácil testabilidade com pytest, eliminando conexões embutidas dentro das funções.

*   **Ação:** Isolar a criação de instâncias de adaptadores/bancos de dados do core da aplicação.
*   **Fluxo Proposto:** 
    *   Implementar injeção declarativa em parâmetros de todos os casos de uso no `/services`.
    *   Substituir instâncias explícitas (`Adapter()`) por interfaces ou dependências que podem receber instâncias Mockadas durante os testes unitários.
*   **Meta:** Cobrir 100% da camada `/domain` e `/services` com testes rápidos e independentes de SQLite ou conexão com a Internet.

## 4. Background Workers (Filas de Mensageria)
O envio linear síncrono é um gargalo, além de gerar possíveis bloqueios (Rate Limit) por fornecedores.

*   **Ação:** Implementar padrão de fila produtor/consumidor (Task Queues).
*   **Ferramentas Possíveis:** Celery (com Redis ou RabbitMQ), RQ, ou até um agendador enxuto atrelado ao SQLite.
*   **Fluxo Proposto:**
    *   O script principal atuará apenas como **Produtor**: Ele varre as dívidas e "enfileira" tarefas do tipo "Enviar Notificação para Unidade 402".
    *   Um script **Consumidor** (Worker) ficará rodando em paralelo, consumindo a fila, respeitando atrasos predefinidos (ex: delay de 2 segundos) entre chamadas no WhatsApp, protegendo o número contra banimentos na Meta API.
