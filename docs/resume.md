# Importação de relatórios em PDF da Almah

Branch: `feat/importacao-relatorios-pdf` · Data: 2026-11-08

## Por que existe

Em novembro/2026 a Almah ativou o **AWS WAF Challenge** nos portais (Alpha e Expresso):
toda requisição recebe primeiro um HTTP 202 com uma página que executa JavaScript e grava o
cookie `aws-waf-token`. A extração automática do Cobrax (`AlmahSession`, via `httpx`) não
executa JavaScript e passou a falhar no login — não é erro de senha nem de redirecionamento.

Enquanto não houver acesso autorizado (liberação de IP no WAF ou API), os relatórios são
**exportados manualmente pela tela da Almah em PDF** e importados por este módulo. O login
automático (`AlmahSession`) e o módulo de envio (`NotificationService` / Evolution) **não foram
alterados** e voltam a ser usados se o acesso for liberado.

## Relatórios aceitos

| Relatório (nome na Almah) | Conteúdo usado | Papel |
|---|---|---|
| **Relatório de Unidades** (proprietários) | unidade + bloco, proprietário, CPF/CNPJ, telefone(s), e-mail(s) | cadastro / contato |
| **Relatório de Inadimplência** (detalhado) | um boleto por linha: bloco, unidade, nº doc, nosso número, competência, vencimento, valor original, pago, multa, juros, correção, total, **status** | débitos — **é este que alimenta o banco** |
| Relatório de Inadimplência **Resumido** | unidade + valor total | só conferência (sem vencimento ⇒ não grava) |

## Como usar

Na raiz do projeto, com o venv ativo:

```bash
python -m src.import_reports --unidades "Relatório de Unidades.pdf" --inadimplencia "Relatório Inadimplência.pdf"
```

| Opção | Efeito |
|---|---|
| (nenhuma) | lê, agrupa os boletos por unidade, liga ao proprietário e mostra o resultado. Não grava nem envia. |
| `--gravar` | grava unidades e boletos em `data/cobrax.db` via `sync_data` (mesmo caminho da extração automática). Cria `data/` se faltar. |
| `--simular-envio` | grava e roda o envio em **dry-run** só para o condomínio importado. Nada é enviado. |
| `--detalhado` | inclui o nome do proprietário na listagem (dados pessoais — só no terminal). |

O condomínio é identificado pelo nome impresso no PDF e procurado em
`src/config/condominios.json` (ignora acentos e o prefixo "CONDOMINIO"). Os dois PDFs precisam
ser do mesmo condomínio.

## Como funciona

```
PDF Unidades ──▶ parse_units_pdf ──────────┐
                                           ├─▶ link_detail_reports ──▶ sync_records() ──▶ sync_data (inalterado)
PDF Inadimplência ─▶ parse_debt_detail_pdf ┘      chave: bloco + unidade                     │
                                                                                              ▼
                                                              get_units_pending_notification / NotificationService (inalterados)
```

- `src/adapters/pdf_reports.py` — leitura dos PDFs pela **posição das colunas** (lida do
  cabeçalho da tabela). Necessário porque nomes, endereços, descrição e status quebram em várias
  linhas. Os títulos da Almah têm letras duplicadas ("negrito por sobreposição"); `dedupe_chars`
  do pdfplumber remove.
- `src/services/report_importer.py` — liga os relatórios, agrupa boletos por unidade, confere a
  integridade e gera `UnitRecord`/`DebtRecord` no formato que `sync_data` já espera.
- `src/import_reports.py` — linha de comando.

Decisões:
- **Chave da unidade = bloco + número.** No Relatório de Unidades a célula "Unidade" traz
  `101 01` (unidade 101, bloco 01); no de inadimplência, `01 101`. Zeros à esquerda do bloco
  são ignorados.
- **Identificador do boleto = nosso número** (único no banco). O nº do documento pode repetir
  entre condomínios e `Debt.doc_number` é único globalmente.
- **DDD "00"** é tratado como ausente; o número fica sem DDD e o notifier o descarta.
- O **status** vai para `Debt.status` como impresso: `Vencido`, `Vencido Advogado`,
  `Vencido Acordo`.

## Conferências automáticas (avisos na saída)

- registros declarados no rodapé × lidos (unidades, boletos, unidades inadimplentes);
- total declarado × soma dos boletos;
- por boleto: original − pago + multa + juros + correção = total;
- mesmo proprietário em mais de uma unidade inadimplente (o `sync_data` atual identifica a
  unidade pelo nome do dono e fundiria as duas).

## Validação

- **Testes:** 112 aprovados, 6 pulados. Os PDFs reais contêm dados pessoais e **nunca** vão
  para o repositório (`*.pdf` no `.gitignore`); os testes reproduzem o layout com dados fictícios
  (`tests/conftest.py`).
- **Testes com PDFs reais (opcionais, locais):** defina `COBRAX_PDF_UNIDADES`,
  `COBRAX_PDF_INADIMPLENCIA` (resumido) e `COBRAX_PDF_DETALHADO` e rode `pytest -k real`.
- **Validado com um condomínio real da Alpha:** 100% dos boletos e unidades lidos, totais
  idênticos ao rodapé e ao relatório resumido (por unidade), todas as unidades ligadas ao
  cadastro. Ponta a ponta (banco temporário + envio em dry-run): a regra dos 90 dias excluiu
  corretamente a unidade cujo boleto mais antigo era recente.

## Ganho: status da cobrança

O relatório detalhado informa se o boleto já está **com o advogado** ou **em acordo**. Isso não
existia no feed XLS usado antes e é a ponte para o EasyJur:

- `Vencido Advogado` ⇒ deve existir cobrança/processo no EasyJur — conferir e vincular;
- `Vencido Acordo` ⇒ deve existir acordo (e parcelas/receitas) no EasyJur — conferir;
- divergências (Almah diz "advogado", EasyJur não tem nada) viram pendência de cadastro.

## Pendências

- [ ] **Regra de envio por status.** O notifier não olha `Debt.status`: unidades em acordo ou já
      com o advogado receberiam a mensagem padrão. Definir antes de qualquer envio real.
- [ ] **Boletos pagos:** cada PDF detalhado é a foto completa da inadimplência do condomínio;
      boletos que sumirem entre duas importações devem ser marcados como pagos (bug conhecido do
      `sync_data`, que só insere).
- [ ] Importação em lote (pasta com vários pares de PDFs).
- [ ] Regerar `uv.lock` (nova dependência `pdfplumber`) — o uv 0.13.7 trava nesta máquina.
- [ ] Layout validado só com relatórios da **Alpha**; conferir um par da **Expresso**.
