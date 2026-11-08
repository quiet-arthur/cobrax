"""
Fixtures compartilhadas.

Os relatórios em PDF da Almah contêm dados pessoais e NUNCA vão para o
repositório. Aqui reproduzimos o layout deles (posição x/y de cada palavra,
medida nos PDFs reais) com dados fictícios.
"""

import datetime

import pytest


def W(text: str, x0: float, top: float) -> dict:
    return {"text": text, "x0": x0, "top": top}


# Bordas esquerdas das colunas no "Relatório de Unidades" real.
COL = {
    "unidade": 77.0, "bloco": 91.4, "fracao": 143.0, "nome": 197.0, "cpf": 274.4,
    "endereco": 343.4, "ddd": 403.4, "fone": 414.2, "email": 457.4, "envio": 643.5,
    "sindico": 731.1, "acesso": 767.7,
}


def units_header(top: float = 209.8) -> list[dict]:
    return [
        W("Unidade", 76.7, top), W("Dia", 119.6, top), W("Fração", 142.7, top),
        W("Proprietário", 196.7, top), W("CPF/CNPJ", 274.1, top),
        W("Endereço", 343.1, top), W("Telefone", 403.1, top), W("E-mail", 457.1, top),
        W("Boleto/Envio", 643.2, top), W("Síndico", 717.3, top), W("/", 750.9, top),
        W("Acesso", 769.2, top),
        W("Venc", 117.5, top + 7.8), W("Total", 142.7, top + 7.8),
        W("Conselheiro", 713.1, top + 7.8),
    ]


def unit_row(top, unidade, bloco, nome_linhas, cpf, ddd=None, fone=None, email=None):
    """Uma unidade; `nome_linhas` simula o nome quebrando em várias linhas."""
    words = [
        W(unidade, COL["unidade"], top), W(bloco, COL["bloco"], top),
        W("0,000000000", COL["fracao"], top), W(cpf, COL["cpf"], top),
        W("RUA", COL["endereco"], top), W("FICTICIA", COL["endereco"] + 14, top),
        W("IMPRESSO", COL["envio"], top), W("NÃO", COL["sindico"], top),
        W("Liberado", COL["acesso"], top),
    ]
    for i, linha in enumerate(nome_linhas):
        x = COL["nome"]
        for palavra in linha.split():
            words.append(W(palavra, x, top + 7.8 * i))
            x += 6 * len(palavra) + 4
    words += [W("100", COL["endereco"], top + 7.8), W("MAIL", COL["envio"], top + 7.8),
              W("79000-000", COL["endereco"], top + 15.6), W("CAMPO", COL["endereco"] + 36, top + 15.6),
              W("GRANDE-MS", COL["endereco"], top + 23.4)]
    if ddd:
        words.append(W(ddd, COL["ddd"], top))
    if fone:
        words.append(W(fone, COL["fone"], top))
    if email:
        words.append(W(email, COL["email"], top))
    return words


@pytest.fixture()
def units_pages() -> list[list[dict]]:
    """Relatório de Unidades fictício: 2 páginas, 4 unidades."""
    page1 = [
        W("ALPHA", 574.2, 13.2), W("ASSESSORIA", 606.6, 13.2),
        W("PROPRIETÁRIOS", 380.0, 62.0),
        W("Condomínios", 76.7, 96.0), W("CONDOMINIO", 150.0, 96.0),
        W("TESTE", 210.0, 96.0), W("FICTICIO", 245.0, 96.0),
        W("NOME", 77.0, 169.0), W("CNPJ", 274.0, 169.0),
        W("CONDOMINIO", 77.0, 184.0), W("TESTE", 140.0, 184.0), W("00.000.000/0001-00", 274.0, 184.0),
        *units_header(),
        *unit_row(232.0, "101", "01", ["MARIA DA SILVA", "PEREIRA"], "111.111.111-11",
                  ddd="67", fone="999990001", email="maria@exemplo.com"),
        *unit_row(269.2, "102", "01", ["JOAO SOUZA"], "222.222.222-22",
                  ddd="00", fone="999990002", email="joao@exemplo.com"),
        W("Emitido", 77.0, 560.0), W("em", 110.0, 560.0), W("08/11/2026", 125.0, 560.0),
    ]
    page2 = [
        W("ALPHA", 574.2, 13.2), W("ASSESSORIA", 606.6, 13.2),
        *unit_row(59.8, "201", "02", ["EMPRESA FICTICIA", "LTDA"], "33.333.333/0001-33",
                  email="contato@exemplo.com"),
        *unit_row(97.0, "202", "02", ["ANA LIMA"], "444.444.444-44", ddd="67", fone="33330004"),
        W("4", 77.0, 140.0), W("registro(s)", 84.0, 140.0), W("encontrado(s)", 130.0, 140.0),
        W("Fração", 77.0, 150.0), W("Total:", 104.0, 150.0), W("0", 130.0, 150.0),
        W("Emitido", 77.0, 560.0), W("em", 110.0, 560.0), W("08/11/2026", 125.0, 560.0),
    ]
    return [page1, page2]


def summary_line(top, grupo, unidade, valor):
    return [W(grupo, 77.0, top), W(unidade, 92.0, top), W(valor, 520.0, top)]


@pytest.fixture()
def summary_pages() -> list[list[dict]]:
    """Relatório de Inadimplência Resumido fictício: 3 unidades."""
    return [[
        W("ALPHA", 574.2, 13.2),
        W("RELATÓRIO", 300.0, 62.0), W("DE", 350.0, 62.0), W("INADIMPLÊNCIA", 365.0, 62.0),
        W("RESUMIDO", 440.0, 62.0),
        W("Condomínio", 77.0, 96.0), W("CONDOMINIO", 150.0, 96.0), W("TESTE", 210.0, 96.0),
        W("FICTICIO", 245.0, 96.0), W("Ignorar", 400.0, 96.0), W("em", 430.0, 96.0),
        W("Não", 450.0, 96.0),
        W("Boletos", 77.0, 111.0), W("em", 110.0, 111.0), W("08/11/2026", 150.0, 111.0),
        W("CLIENTE", 77.0, 160.0), W("/", 110.0, 160.0), W("UNIDADE", 120.0, 160.0),
        W("VALOR", 520.0, 160.0),
        *summary_line(180.0, "01", "101", "7.623,93"),
        *summary_line(190.0, "02", "202", "159,66"),
        *summary_line(200.0, "03", "303", "1.204,19"),
        W("TOTAL", 77.0, 220.0), W("8.987,78", 520.0, 220.0),
        W("3", 77.0, 235.0), W("Unidade(s)", 84.0, 235.0), W("Inadimplente(s)", 130.0, 235.0),
        W("(12,89", 200.0, 235.0), W("%)", 230.0, 235.0),
    ]]


def _days_ago(days: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d/%m/%Y")


def detail_header(top: float = 184.8) -> list[dict]:
    return [
        W("CLI/UNID", 45.0, top), W("DOC", 86.0, top), W("COMPTO.", 117.0, top),
        W("VENC", 188.0, top), W("VL", 244.0, top), W("ORIG", 258.0, top),
        W("VL", 294.0, top), W("MULTA", 321.0, top), W("JUROS", 366.0, top),
        W("CORREÇÃO", 397.0, top), W("VL", 446.0, top), W("TOTAL", 459.0, top),
        W("ACORDO", 487.0, top), W("STATUS", 521.0, top),
        W("PAGTO", 280.0, top + 8.2), W("MONETÁRIA", 393.0, top + 8.2),
    ]


_MONEY_X = (250.0, 287.0, 325.0, 365.0, 410.0, 455.0)


def detail_bill(top, grupo, unidade, doc, nn, compto, venc, valores, status):
    """
    Um boleto do relatório detalhado, em 4 linhas como no PDF real:
    BL/DOC/NN:/nosso número na coluna DOC; COMPTO. e STATUS quebrando em linhas.
    """
    compto_lines = compto + [""] * (3 - len(compto))
    status_lines = status + [""] * (2 - len(status))
    words = [W(grupo, 45.0, top), W(unidade, 57.0, top), W("BL", 86.0, top), W(venc, 188.0, top)]
    words += [W(v, x, top) for v, x in zip(valores, _MONEY_X)]
    for i, (t, extra) in enumerate([(top, None), (top + 8.7, doc), (top + 18.7, "NN:"), (top + 27.5, nn)]):
        if extra:
            words.append(W(extra, 86.0, t))
        if i < 3 and compto_lines[i]:
            x = 118.0  # espaçamento do PDF real: "PARCELA" 118, "20/30" 148, "-" 171
            for palavra in compto_lines[i].split():
                words.append(W(palavra, x, t))
                x += 4 * len(palavra) + 2
        if i < 2 and status_lines[i]:
            words.append(W(status_lines[i], 519.0, t))
    return words


def detail_subtotal(top, count, valores):
    return [W(str(count), 205.0, top)] + [W(v, x - 5, top) for v, x in zip(valores, _MONEY_X)]


def detail_footer(page, pages):
    return [W("Emitido", 43.0, 825.8), W("em", 76.0, 825.8), W("08/11/2026", 89.0, 825.8),
            W(str(page), 518.0, 825.8), W("de", 531.0, 825.8), W(str(pages), 544.0, 825.8)]


@pytest.fixture()
def detail_pages() -> list[list[dict]]:
    """
    'Relatório de Inadimplência' (detalhado) fictício, 2 páginas, 4 boletos:
      01 101: 1 boleto antigo (Advogado) + 1 recente (10 dias)
      02 202: 1 boleto recente (30 dias) — após a quebra de página
      09 999: 1 boleto de unidade que não existe no relatório de unidades
    """
    page1 = [
        W("ALPHA", 327.0, 13.2), W("ASSESSORIA", 359.0, 13.2),
        W("RELATÓRIO", 205.0, 61.6), W("DE", 276.0, 61.6), W("INADIMPLÊNCIA", 297.0, 61.6),
        W("Condomínio", 76.0, 92.1), W("CONDOMINIO", 126.0, 92.1), W("TESTE", 168.0, 92.1),
        W("FICTICIO", 200.0, 92.1), W("Ignorar", 339.0, 92.1), W("Não", 375.0, 92.1),
        W("Boletos", 76.0, 115.0), W("em", 111.0, 115.0), W("08/11/2026", 126.0, 115.0),
        W("Ignorar", 304.0, 115.0), W("Acordos", 339.0, 115.0), W("Não", 375.0, 115.0),
        W("Atraso", 76.0, 123.7), W("Até", 107.0, 123.7),
        *detail_header(),
        *detail_bill(207.3, "01", "101", "000101", "90000001",
                     ["PARCELA 01/10 -", "CONFORME PLANILHA", "DA IDEAL"], "15/01/2023",
                     ["100,00", "0,00", "2,00", "10,00", "3,00", "115,00"], ["Vencido", "Advogado"]),
        *detail_bill(248.2, "01", "101", "000102", "90000002",
                     ["Parc: 01/01"], _days_ago(10),
                     ["200,00", "0,00", "4,00", "0,00", "1,00", "205,00"], ["Vencido"]),
        *detail_subtotal(289.0, 2, ["300,00", "0,00", "6,00", "10,00", "4,00", "320,00"]),
        *detail_footer(1, 2),
    ]
    page2 = [
        W("ALPHA", 327.0, 13.2), W("ASSESSORIA", 359.0, 13.2), W("45.242.917/0001-30", 473.0, 24.8),
        *detail_bill(60.4, "02", "202", "000201", "90000003",
                     ["PARCELA 03/10 -", "ACORDO"], _days_ago(30),
                     ["50,00", "0,00", "1,00", "5,00", "0,50", "56,50"], ["Vencido", "Acordo"]),
        *detail_subtotal(101.4, 1, ["50,00", "0,00", "1,00", "5,00", "0,50", "56,50"]),
        *detail_bill(116.0, "09", "999", "000901", "90000004",
                     ["Parc: 01/01"], "10/05/2022",
                     ["10,00", "0,00", "0,20", "1,00", "0,10", "11,30"], ["Vencido"]),
        *detail_subtotal(157.0, 1, ["10,00", "0,00", "0,20", "1,00", "0,10", "11,30"]),
        W("TOTAL", 196.0, 171.0), W("360,00", 231.0, 171.0), W("0,00", 285.0, 171.0),
        W("7,20", 308.0, 171.0), W("16,00", 348.0, 171.0), W("4,60", 393.0, 171.0),
        W("387,80", 438.0, 171.0),
        W("4registro(s)", 43.0, 183.0), W("encontrado(s)", 103.0, 183.0),
        W("3", 43.0, 191.8), W("Unidade(s)", 54.0, 191.8), W("Inadimplente(s)", 96.0, 191.8),
        *detail_footer(2, 2),
    ]
    return [page1, page2]


@pytest.fixture()
def condos() -> list[dict]:
    return [
        {"id": "999", "name": "TESTE FICTICIO", "adm": "alpha"},
        {"id": "998", "name": "OUTRO CONDOMINIO", "adm": "expresso"},
    ]
