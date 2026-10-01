from pathlib import Path
import os
import re
import hashlib
import requests

import threading
import time
import sys

SIPNI_DEBUG = os.getenv("SIPNI_DEBUG", "").strip() == "1"

def debug_print(*args, **kwargs):
    if SIPNI_DEBUG:
        print(*args, **kwargs)

def executar_com_carregamento(funcao, *args, **kwargs):
    executando = True

    def animar():
        blocos = 20
        posicao = 0
        direcao = 1

        while executando:
            barra = ["░"] * blocos
            for i in range(5):
                indice = posicao + i
                if 0 <= indice < blocos:
                    barra[indice] = "█"

            sys.stdout.write(
                "\rCarregando Dados\n[" +
                "".join(barra) +
                "]"
            )
            sys.stdout.flush()

            posicao += direcao

            if posicao >= blocos - 5:
                direcao = -1
            elif posicao <= 0:
                direcao = 1

            time.sleep(0.08)

    thread = threading.Thread(target=animar, daemon=True)
    thread.start()

    try:
        return funcao(*args, **kwargs)
    finally:
        executando = False
        thread.join(timeout=1)

        sys.stdout.write("\r" + " " * 50 + "\r")
        sys.stdout.flush()

from bs4 import BeautifulSoup
from dotenv import load_dotenv

import warnings
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings(
        "ignore",
        category=XMLParsedAsHTMLWarning,
    )
except ImportError:
    pass

load_dotenv(".env")

USUARIO = os.getenv("SIPNI_USUARIO")
SENHA = os.getenv("SIPNI_SENHA")

if not USUARIO or not SENHA:
    print("ERRO: SIPNI_USUARIO ou SIPNI_SENHA não encontrado no .env")
    raise SystemExit(1)

BASE = "https://sipni.datasus.gov.br"
INICIO = BASE + "/si-pni-web/faces/inicio.jsf"

# O Termux está com problema de resolução DNS pelo libc.
# Forçamos o IP oficial atual apenas para esta conexão.
IP_SIPNI = "189.28.143.141"

_real_getaddrinfo = __import__("socket").getaddrinfo

def resolver_forcado(host, port, *args, **kwargs):
    if host == "sipni.datasus.gov.br":
        return _real_getaddrinfo(IP_SIPNI, port, *args, **kwargs)
    return _real_getaddrinfo(host, port, *args, **kwargs)

import socket
socket.getaddrinfo = resolver_forcado


def sha512(texto):
    return hashlib.sha512(texto.encode("ascii")).hexdigest()


def form_com_login(html):
    soup = BeautifulSoup(html, "html.parser")

    # Procura o formulário que contém os campos de usuário e senha.
    for form in soup.find_all("form"):
        usuario = form.find(
            "input",
            attrs={"name": re.compile(r":usuario$")}
        )
        senha = form.find(
            "input",
            attrs={"name": re.compile(r":senha$")}
        )

        if usuario and senha:
            return form

    return None

def fazer_login(session):

    r = session.get(
        INICIO,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )


    form = form_com_login(r.text)

    if not form:
        print("ERRO: formulário de login não encontrado.")
        return False

    form_id = form.get("id")
    action = form.get("action") or "/si-pni-web/faces/inicio.jsf"

    if action.startswith("/"):
        action = BASE + action

    usuario_field = form.find("input", attrs={"name": re.compile(r":usuario$")})
    senha_field = form.find("input", attrs={"name": re.compile(r":senha$")})

    if not usuario_field or not senha_field:
        print("ERRO: campos de login não encontrados.")
        return False

    usuario_name = usuario_field["name"]
    senha_name = senha_field["name"]

    # Descobre o botão Logar dinamicamente.
    botao = None

    for b in form.find_all(["button", "input"]):
        texto = b.get_text(" ", strip=True)
        valor = b.get("value", "")
        if "Logar" in texto or "Logar" in valor:
            botao = b
            break

    if not botao:
        print("ERRO: botão Logar não encontrado.")
        return False

    botao_name = botao.get("name")

    dados = {
        form_id: form_id,
        "javax.faces.ViewState": (
            form.find("input", attrs={"name": "javax.faces.ViewState"})
            or {}
        ).get("value", ""),
        usuario_name: USUARIO,
        senha_name: sha512(SENHA),
    }

    if botao_name:
        dados[botao_name] = botao.get("value", "")


    # IMPORTANTE:
    # O SI-PNI responde com redirect HTTP.
    # Não seguimos esse redirect automaticamente, pois isso destrói
    # a sessão HTTPS.
    login = session.post(
        action,
        data=dados,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": INICIO,
        },
        allow_redirects=False,
        timeout=30,
    )

    if login.status_code not in (200, 302, 303):
        print("ERRO HTTP no login:", login.status_code)
        return False

    # Reabre pelo HTTPS usando a mesma sessão.
    pagina = session.get(
        INICIO,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )

    if "Registro de Vacinação Individualizado" not in pagina.text:
        print("ERRO: login não foi confirmado.")
        return False

    return True


def abrir_registro(session):

    pagina = session.get(
        INICIO,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )

    soup = BeautifulSoup(pagina.text, "html.parser")

    link = None

    for a in soup.find_all("a"):
        if a.get_text(" ", strip=True) == "Registro de Vacinação Individualizado":
            link = a
            break

    if not link:
        print("ERRO: menu Registro não encontrado.")
        return None

    onclick = link.get("onclick", "")

    source = re.search(r"source:'([^']+)'", onclick)
    form_id = re.search(r"formId:'([^']+)'", onclick)
    param = re.search(
        r"name:'([^']+)',value:'([^']+)'",
        onclick,
    )

    if not source or not form_id or not param:
        print("ERRO: não foi possível interpretar o menu.")
        return None

    source = source.group(1)
    form_id = form_id.group(1)
    param_name = param.group(1)
    param_value = param.group(2)

    form = soup.find("form", id=form_id)

    if not form:
        print("ERRO: formulário do menu não encontrado.")
        return None

    action = form.get("action") or "/si-pni-web/faces/inicio.jsf"

    if action.startswith("/"):
        action = BASE + action

    dados = {}

    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            dados[name] = inp.get("value", "")

    dados.update({
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": source,
        "javax.faces.partial.execute": "@all",
        param_name: param_value,
        source: source,
    })

    r = session.post(
        action,
        data=dados,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Faces-Request": "partial/ajax",
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "Referer": INICIO,
        },
        timeout=30,
    )

    m = re.search(r'<redirect[^>]*url="([^"]+)"', r.text)
    if not m:
        m = re.search(r"<redirect[^>]*url='([^']+)'", r.text)

    redirect_url = m.group(1) if m else None

    if not redirect_url:
        print("ERRO: SI-PNI não retornou o redirecionamento.")
        return None

    destino = redirect_url

    if destino.startswith("/"):
        destino = BASE + destino

    # Força HTTPS.
    destino = destino.replace(
        "http://sipni.datasus.gov.br",
        "https://sipni.datasus.gov.br",
    )

    registro = session.get(
        destino,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": INICIO,
        },
        timeout=30,
    )

    if "pacienteForm:nomePaciente" not in registro.text:
        print("ERRO: página de pesquisa não abriu.")
        return None


    return registro.text




def normalizar_municipio(texto):
    import unicodedata

    texto = texto or ""

    texto = unicodedata.normalize(
        "NFD",
        texto
    )

    texto = "".join(
        caractere
        for caractere in texto
        if unicodedata.category(caractere) != "Mn"
    )

    return " ".join(texto.upper().split())


def municipio_corresponde(municipio_retornado, municipio_solicitado):
    return (
        normalizar_municipio(municipio_retornado)
        ==
        normalizar_municipio(municipio_solicitado)
    )


def carregar_municipios_por_uf(session, html, uf_codigo):
    """
    Simula o change do componente PrimeFaces pacienteForm:uf
    e retorna os municípios carregados pelo SI-PNI.
    """

    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="pacienteForm")

    if not form:
        print("ERRO: pacienteForm não encontrado no AJAX da UF.")
        return None

    dados = []

    # Serializa o formulário atual.
    for elemento in form.find_all(["input", "select", "textarea"]):
        name = elemento.get("name")

        if not name:
            continue

        if elemento.has_attr("disabled"):
            continue

        tipo = (elemento.get("type") or "").lower()

        if tipo in ("checkbox", "radio"):
            if not elemento.has_attr("checked"):
                continue

        # UF escolhida.
        if name == "pacienteForm:uf_input":
            dados.append((name, uf_codigo))
            continue

        if elemento.name == "select":
            selecionados = elemento.find_all("option", selected=True)

            for option in selecionados:
                dados.append(
                    (name, option.get("value", ""))
                )

            continue

        value = elemento.get("value")

        if value is not None:
            dados.append((name, value))

    # PrimeFaces AJAX exatamente conforme o JavaScript encontrado.
    dados.append(
        ("javax.faces.partial.ajax", "true")
    )

    dados.append(
        ("javax.faces.source", "pacienteForm:uf")
    )
    dados.append(
        ("javax.faces.behavior.event", "change")
    )

    dados.append(
        ("javax.faces.partial.execute", "pacienteForm:uf")
    )

    dados.append(
        (
            "javax.faces.partial.render",
            "pacienteForm:labelMunicipio pacienteForm:municipio",
        )
    )

    dados.append(
        ("pacienteForm:uf", uf_codigo)
    )

    action = form.get("action") or ""

    if action.startswith("/"):
        url = "https://sipni.datasus.gov.br" + action
    elif action.startswith("http"):
        url = action
    else:
        url = "https://sipni.datasus.gov.br/si-pni-web/faces/" + action

    debug_print()
    debug_print("=" * 60)
    debug_print("        AJAX REAL DA UF")
    debug_print("=" * 60)
    debug_print("UF enviada:", uf_codigo)
    debug_print("URL:", url)
    debug_print("SOURCE: pacienteForm:uf")
    debug_print("EVENT: change")
    debug_print("PROCESS: pacienteForm:uf")
    debug_print("UPDATE: pacienteForm:labelMunicipio pacienteForm:municipio")

    resposta = session.post(
        url,
        data=dados,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": url,
            "Accept": "application/xml, text/xml, */*",
        },
        timeout=30,
    )

    debug_print()
    debug_print("HTTP:", resposta.status_code)
    debug_print("Content-Type:", resposta.headers.get("Content-Type"))
    debug_print("TAMANHO:", len(resposta.text))

    debug_print()
    debug_print("--- RESPOSTA AJAX DA UF ---")
    # Mostra somente referências ao componente de município.
    debug_print()
    debug_print("--- ESTRUTURA DO MUNICÍPIO ---")

    import re as _re

    referencias = _re.findall(
        r".{0,300}(?:municipio|Municipio).{0,1000}",
        resposta.text,
        flags=_re.IGNORECASE | _re.DOTALL,
    )

    if referencias:
        for trecho in referencias[:20]:
            trecho = _re.sub(r"\\s+", " ", trecho).strip()
            debug_print(trecho[:1500])
            debug_print("---")
    else:
        debug_print("Nenhuma referência a municipio encontrada.")

    debug_print("=" * 60)


    # DEBUG: extrair somente o update do município
    import re as _re

    debug_print()
    debug_print("--- UPDATE DO MUNICÍPIO ---")

    for _m in _re.finditer(
        r'<update\b([^>]*)>(.*?)</update>',
        resposta.text,
        flags=_re.IGNORECASE | _re.DOTALL,
    ):
        _attrs = _m.group(1)
        _conteudo = _m.group(2)

        _id = _re.search(
            r'id\s*=\s*["\']([^"\']+)["\']',
            _attrs,
            flags=_re.IGNORECASE,
        )

        if _id and "municipio" in _id.group(1).lower():
            _limpo = _re.sub(
                r'<!\[CDATA\[(.*?)\]\]>',
                r'\1',
                _conteudo,
                flags=_re.DOTALL,
            )

            debug_print("UPDATE ID:", _id.group(1))
            debug_print(_limpo[:20000])

    debug_print("--- FIM UPDATE DO MUNICÍPIO ---")

    return resposta.text





def extrair_viewstate_ajax(resposta_xml):
    """
    Extrai o javax.faces.ViewState atualizado de uma resposta
    AJAX do PrimeFaces.
    """

    matches = re.finditer(
        r'<update\b([^>]*)>(.*?)</update>',
        resposta_xml,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in matches:
        atributos = match.group(1)
        conteudo = match.group(2)

        id_match = re.search(
            r'id\s*=\s*["\']([^"\']+)["\']',
            atributos,
            flags=re.IGNORECASE,
        )

        if not id_match:
            continue

        if id_match.group(1) != "javax.faces.ViewState":
            continue

        return re.sub(
            r"<!\[CDATA\[(.*?)\]\]>",
            r"\1",
            conteudo,
            flags=re.DOTALL,
        ).strip()

    return ""

def extrair_resultados_pagina(update_paciente):
    """
    Extrai os registros de uma resposta do DataTable do SI-PNI.

    A tabela retorna 9 células (<td>) por registro:
    1 - Cartão SUS
    2 - Nome
    3 - Nome da mãe
    4 - Endereço
    5 - Bairro
    6 - Município
    7 - Estado
    8 - Nascimento
    9 - coluna de ações
    """

    resultados = []

    linhas = re.findall(
        r"<tr\b[^>]*>(.*?)</tr>",
        update_paciente,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for linha in linhas:

        celulas = re.findall(
            r"<td\b[^>]*>(.*?)</td>",
            linha,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if len(celulas) < 8:
            continue

        dados = []

        for celula in celulas[:8]:

            # Remove tags HTML
            valor = re.sub(
                r"<[^>]+>",
                " ",
                celula,
                flags=re.DOTALL,
            )

            # Decodifica entidades HTML
            try:
                from html import unescape
                valor = unescape(valor)
            except Exception:
                pass

            # Normaliza espaços
            valor = " ".join(valor.split())

            dados.append(valor)

        resultados.append({
            "cartao_sus": dados[0],
            "nome": dados[1],
            "nome_mae": dados[2],
            "endereco": dados[3],
            "bairro": dados[4],
            "municipio": dados[5],
            "estado": dados[6],
            "nascimento": dados[7],
        })

    return resultados

def paginar_resultados(session, url, update_paciente, viewstate, pagina, rows):
    """
    Solicita uma página específica do DataTable do SI-PNI.

    Faz uma única requisição por página, mantendo a paginação
    igual ao comportamento do SI-PNI.
    """

    primeiro = pagina * rows

    dados = [
        ("javax.faces.partial.ajax", "true"),
        ("javax.faces.source", "pacienteForm:listaPacienteTable"),
        ("javax.faces.partial.execute", "pacienteForm:listaPacienteTable"),
        ("javax.faces.behavior.event", "page"),
        ("javax.faces.partial.render", "pacienteForm:listaPacienteTable"),
        ("pacienteForm:listaPacienteTable", "pacienteForm:listaPacienteTable"),
        ("pacienteForm:listaPacienteTable_pagination", "true"),
        ("pacienteForm:listaPacienteTable_first", str(primeiro)),
        ("pacienteForm:listaPacienteTable_rows", str(rows)),
        ("pacienteForm:listaPacienteTable_encodeFeature", "true"),
        ("javax.faces.ViewState", viewstate),
    ]

    try:

        resposta = session.post(
            url,
            data=dados,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Faces-Request": "partial/ajax",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": url,
                "Accept": "application/xml, text/xml, */*",
            },
            timeout=(15, 90),
        )

    except requests.exceptions.ReadTimeout:

        print()
        debug_print("=" * 60)
        print("          RESUMO DA CONSULTA")
        debug_print("=" * 60)
        print("STATUS: NÃO FOI POSSÍVEL CARREGAR A PÁGINA")
        print()
        print(
            "O SI-PNI demorou mais de 90 segundos "
            "para responder."
        )
        print("Página solicitada:", pagina + 1)
        debug_print("=" * 60)

        return None, viewstate

    except requests.exceptions.RequestException as erro:

        print()
        debug_print("=" * 60)
        print("          RESUMO DA CONSULTA")
        debug_print("=" * 60)
        print("STATUS: ERRO DE CONEXÃO")
        print()
        print("Não foi possível carregar a página.")
        print("Detalhes:", str(erro))
        debug_print("=" * 60)

        return None, viewstate

    if resposta.status_code != 200:

        print()
        debug_print("=" * 60)
        print("          RESUMO DA CONSULTA")
        debug_print("=" * 60)
        print("STATUS: ERRO DO SI-PNI")
        print()
        print("Código HTTP:", resposta.status_code)
        print("Página solicitada:", pagina + 1)
        debug_print("=" * 60)

        return None, viewstate

    updates = list(
        re.finditer(
            r"<update\b([^>]*)>(.*?)</update>",
            resposta.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    novo_update = None
    novo_viewstate = viewstate

    for match in updates:

        atributos = match.group(1)
        conteudo = match.group(2)

        id_match = re.search(
            r'id\s*=\s*["\']([^"\']+)["\']',
            atributos,
            flags=re.IGNORECASE,
        )

        if not id_match:
            continue

        update_id = id_match.group(1)

        if update_id == "pacienteForm:listaPacienteTable":
            novo_update = conteudo

        elif update_id == "javax.faces.ViewState":

            novo_viewstate = re.sub(
                r"<!\[CDATA\[(.*?)\]\]>",
                r"\1",
                conteudo,
                flags=re.DOTALL,
            ).strip()

    if not novo_update:

        print()
        debug_print("=" * 60)
        print("          RESUMO DA CONSULTA")
        debug_print("=" * 60)
        print("STATUS: RESPOSTA INESPERADA")
        print()
        print(
            "O SI-PNI respondeu, mas não enviou "
            "a tabela solicitada."
        )
        print("Página solicitada:", pagina + 1)
        debug_print("=" * 60)

        return None, novo_viewstate

    return novo_update, novo_viewstate

def post_com_spinner(session, *args, **kwargs):
    import threading
    import time
    import sys

    terminado = False

    def animar():
        simbolos = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        indice = 0

        while not terminado:
            sys.stdout.write(
                "\r\033[90m" + simbolos[indice] + " Carregando dados\033[0m"
            )
            sys.stdout.flush()

            indice = (indice + 1) % len(simbolos)
            time.sleep(0.08)

    thread = threading.Thread(target=animar, daemon=True)
    thread.start()

    try:
        return session.post(*args, **kwargs)
    finally:
        terminado = True
        thread.join(timeout=1)

        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()


def pesquisar(session, html):
    import subprocess

    VERMELHO = "\033[91m"
    VERMELHO_ESCURO = "\033[31m"
    CIANO = "\033[96m"
    CINZA_ESCURO = "\033[90m"
    CINZA = "\033[37m"
    RESET = "\033[0m"
    NEGRITO = "\033[1m"

    print("\033[2J\033[H", end="")

    print("\033[38;5;160m" + subprocess.run(
        ["figlet", "-f", "big", "DEATHSEC"],
        capture_output=True,
        text=True
    ).stdout.rstrip() + RESET)

    print()
    print(
        "\033[38;5;160m" +
        NEGRITO +
        "Consulta de dados - feito por eofelix" +
        RESET
    )
    print()
    print(
        CINZA_ESCURO +
        'Escreva "restart" para reiniciar o preenchimento.' +
        RESET
    )
    print()

    campos = [
        ("nome", "Nome", True),
        ("nome_mae", "Nome da mãe", False),
        ("nome_social", "Nome social", False),
        ("nascimento", "Data de nascimento", False),
        ("cartao", "Cartão SUS", False),
        ("documento", "Documento", False),
        ("uf", "UF de moradia", True),
        ("municipio", "Município (opcional)", False),
        ("uf_nascimento", "UF de nascimento", False),
        ("municipio_nascimento", "Município de nascimento", False),
    ]

    valores = {
        "nome": "",
        "nome_mae": "",
        "nome_social": "",
        "nascimento": "",
        "cartao": "",
        "documento": "",
        "uf": "",
        "municipio": "",
        "uf_nascimento": "",
        "municipio_nascimento": "",
    }

    indice = 0

    while indice < len(campos):
        chave, rotulo, obrigatorio = campos[indice]

        entrada = input(
            VERMELHO_ESCURO +
            rotulo + ": " +
            RESET
        ).strip()

        if entrada.lower() == "restart":
            valores = {
                "nome": "",
                "nome_mae": "",
                "nome_social": "",
                "nascimento": "",
                "cartao": "",
                "documento": "",
                "uf": "",
                "municipio": "",
                "uf_nascimento": "",
                "municipio_nascimento": "",
            }

            indice = 0

            print("\033[2J\033[H")

            print(
                "\033[38;5;160m" +
                subprocess.run(
                    ["figlet", "-f", "big", "DEATHSEC"],
                    capture_output=True,
                    text=True
                ).stdout.rstrip() +
                RESET
            )
            print()
            print(
                CINZA_ESCURO +
                'Escreva "restart" para reiniciar o preenchimento.' +
                RESET
            )
            print()

            continue

        if obrigatorio and not entrada:
            while True:
                # O aviso fica na linha abaixo do mesmo campo.
                print(
                    VERMELHO_ESCURO +
                    "⚠ " + rotulo + " é obrigatório." +
                    RESET
                )

                # Volta duas linhas:
                # 1 = linha depois do campo
                # 2 = linha do próprio campo
                print("\033[2A", end="")

                # Limpa somente a linha do campo.
                print("\r\033[2K", end="")

                entrada = input(
                    VERMELHO_ESCURO +
                    rotulo + ": " +
                    RESET
                ).strip()

                if entrada.lower() == "restart":
                    valores = {
                        "nome": "",
                        "nome_mae": "",
                        "nome_social": "",
                        "nascimento": "",
                        "cartao": "",
                        "documento": "",
                        "uf": "",
                        "municipio": "",
                        "uf_nascimento": "",
                        "municipio_nascimento": "",
                    }

                    indice = 0

                    print("\033[2J\033[H")

                    print("\033[38;5;160m" + subprocess.run(
                        ["figlet", "-f", "big", "DEATHSEC"],
                        capture_output=True,
                        text=True
                    ).stdout.rstrip() + RESET)
                    print()
                    print(
                        CINZA_ESCURO +
                        'Escreva "restart" para reiniciar o preenchimento.' +
                        RESET
                    )
                    print()

                    break

                if entrada:
                    # O cursor ficou na linha do aviso.
                    # Limpa o aviso antes de continuar.
                    print("\r\033[2K", end="")
                    break

            if not entrada:
                continue

        valores[chave] = entrada

        if chave in ("uf", "uf_nascimento"):
            valores[chave] = entrada.upper()

        indice += 1

    nome = valores["nome"]
    nome_mae = valores["nome_mae"]
    nome_social = valores["nome_social"]
    nascimento = valores["nascimento"]
    cartao = valores["cartao"]
    documento = valores["documento"]
    uf = valores["uf"]
    municipio = valores["municipio"]
    uf_nascimento = valores["uf_nascimento"]
    municipio_nascimento = valores["municipio_nascimento"]

    soup = BeautifulSoup(html, "html.parser")

    # ============================================================
    # ARQUIVOS JAVASCRIPT CARREGADOS PELO SI-PNI
    # ============================================================
    debug_print()
    debug_print("=" * 60)
    debug_print("        JAVASCRIPT EXTERNO DO SI-PNI")
    debug_print("=" * 60)

    scripts_src = re.findall(
        r'<script[^>]+src=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE
    )

    if scripts_src:
        vistos_js = set()

        for src in scripts_src:
            if src in vistos_js:
                continue

            vistos_js.add(src)

            src_lower = src.lower()

            if any(x in src_lower for x in [
                "primefaces",
                "jquery",
                "jsf",
                "javax",
                "datatable",
                "theme"
            ]):
                debug_print(src)
    else:
        debug_print("NENHUM SCRIPT EXTERNO ENCONTRADO.")

    debug_print("=" * 60)

    form = soup.find("form", id="pacienteForm")

    if not form:
        debug_print("ERRO: pacienteForm não encontrado.")
        return

    # ============================================================
    # ============================================================
    # DIAGNÓSTICO TEMPORÁRIO — COMPONENTE PRIMEFACES DA UF
    # ============================================================

    debug_print()
    debug_print("=" * 60)
    debug_print("        COMPONENTE PRIMEFACES DA UF")
    debug_print("=" * 60)

    uf_elemento = soup.find(
        id="pacienteForm:uf_input"
    )

    if not uf_elemento:
        debug_print("uf_input não encontrado.")
    else:
        atual = uf_elemento

        for nivel in range(1, 7):
            atual = atual.parent

            if not atual:
                break

            texto = str(atual)

            debug_print()
            debug_print(f"--- NIVEL {nivel} ---")
            debug_print(texto[:12000])

            if len(texto) > 30000:
                break

    debug_print()
    debug_print("--- REFERÊNCIAS PRIMEFACES ---")

    encontrados = set()

    for match in re.finditer(
        r".{0,1000}(?:uf_input|ufNascimento_input|municipio_input).{0,3000}",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        trecho = re.sub(r"\s+", " ", match.group(0)).strip()

        if trecho not in encontrados:
            encontrados.add(trecho)
            debug_print(trecho[:5000])

    if not encontrados:
        debug_print("Nenhuma referência encontrada.")

    debug_print("=" * 60)

    # ============================================================
    # DIAGNÓSTICO: JAVASCRIPT EXATO DO COMPONENTE UF
    # ============================================================

    debug_print()
    debug_print("=" * 60)
    debug_print("        JAVASCRIPT EXATO DA UF")
    debug_print("=" * 60)

    script_uf = soup.find(
        "script",
        id="pacienteForm:uf_s",
    )

    if script_uf:
        debug_print(script_uf.get_text(strip=False))
    else:
        debug_print("SCRIPT pacienteForm:uf_s NÃO ENCONTRADO.")

    debug_print("=" * 60)

    botao = None

    for b in form.find_all("button"):
        if b.get_text(" ", strip=True).strip() == "Pesquisar":
            botao = b
            break

    if not botao:
        print("ERRO: botão Pesquisar não encontrado.")
        return

    source = botao.get("name") or botao.get("id")
    onclick = botao.get("onclick", "")

    process_match = re.search(
        r"process\s*:\s*['\"]([^'\"]+)['\"]",
        onclick
    )

    update_match = re.search(
        r"update\s*:\s*['\"]([^'\"]+)['\"]",
        onclick
    )

    process = (
        process_match.group(1)
        if process_match
        else "pacienteForm"
    )

    update = (
        update_match.group(1)
        if update_match
        else "pacienteForm errorMessage"
    )

    action = form.get("action")

    if not action:
        print("ERRO: action do pacienteForm não encontrada.")
        return

    if action.startswith("/"):
        action = BASE + action

    print()

    # ------------------------------------------------------------
    # SERIALIZAÇÃO DO FORMULÁRIO
    # Equivalente aproximado ao form.serializeArray() do jQuery
    # ------------------------------------------------------------

    dados = []

    print()
    
    # Códigos de UF usados pelo SI-PNI
    codigos_uf = {
        "AC": "12",
        "AL": "27",
        "AP": "16",
        "AM": "13",
        "BA": "29",
        "CE": "23",
        "DF": "53",
        "ES": "32",
        "GO": "52",
        "MA": "21",
        "MT": "51",
        "MS": "50",
        "MG": "31",
        "PA": "15",
        "PB": "25",
        "PR": "41",
        "PE": "26",
        "PI": "22",
        "RJ": "33",
        "RN": "24",
        "RS": "43",
        "RO": "11",
        "RR": "14",
        "SC": "42",
        "SP": "35",
        "SE": "28",
        "TO": "17",
    }

    uf_codigo = codigos_uf.get(uf)
    uf_nascimento_codigo = codigos_uf.get(uf_nascimento)

    if not uf_codigo:
        print(f"ERRO: UF inválida: {uf}")
        return

    # ============================================================
    # CARREGAR MUNICÍPIOS DA UF DE MORADIA
    # ============================================================

    debug_print()

    debug_print("=" * 60)

    debug_print("        OPÇÕES REAIS DA UF")

    debug_print("=" * 60)


    from bs4 import BeautifulSoup as _BeautifulSoup


    _soup_uf = _BeautifulSoup(html, "html.parser")

    _select_uf = _soup_uf.find("select", id="pacienteForm:uf_input")


    if _select_uf:

        for _op in _select_uf.find_all("option"):

            debug_print(

                "VALUE:",

                repr(_op.get("value")),

                "| TEXTO:",

                repr(_op.get_text(strip=True)),

                "| SELECTED:",

                _op.has_attr("selected")

            )

    else:

        print("SELECT pacienteForm:uf_input NÃO ENCONTRADO.")


    debug_print("=" * 60)

    resposta_uf = carregar_municipios_por_uf(
        session,
        html,
        uf_codigo,
    )

    if not resposta_uf:
        print("ERRO: não foi possível carregar municípios da UF.")
        return

    # Localiza automaticamente o código do município escolhido.
    # O HTML do município vem dentro do <update> do AJAX.
    import re as _re

    conteudo_municipio = None

    for _m in _re.finditer(
        r'<update\\b([^>]*)>(.*?)</update>',
        resposta_uf,
        flags=_re.IGNORECASE | _re.DOTALL,
    ):
        _attrs = _m.group(1)
        _conteudo = _m.group(2)

        _id = _re.search(
            r'id\\s*=\\s*["\\\']([^"\\\']+)["\\\']',
            _attrs,
            flags=_re.IGNORECASE,
        )

        if _id and _id.group(1) == "pacienteForm:municipio":
            conteudo_municipio = _conteudo
            break

    municipio_codigo = None

    # O AJAX da UF já confirmou que a resposta contém
    # <option value="230550">IGUATU</option>.
    # Procuramos diretamente na resposta recebida.

    import re as _re

    _municipio_normalizado = normalizar_municipio(municipio)

    _opcoes_municipio = _re.findall(
        r'<option\s+value=["\\\']([^"\\\']*)["\\\'][^>]*>(.*?)</option>',
        resposta_uf,
        flags=_re.IGNORECASE | _re.DOTALL,
    )

    for _codigo, _nome_municipio in _opcoes_municipio:
        _nome_municipio = re.sub(r"<[^>]+>", "", _nome_municipio).strip()

        if normalizar_municipio(_nome_municipio) == _municipio_normalizado:
            municipio_codigo = _codigo
            break

    if municipio and not municipio_codigo:
        debug_print()
        debug_print("ERRO: município não encontrado na UF informada.")
        debug_print("Município solicitado:", municipio)
        return

    debug_print()
    debug_print("=" * 60)
    debug_print("        MUNICÍPIO LOCALIZADO")
    debug_print("=" * 60)
    debug_print("Município:", municipio)
    debug_print("Código:", municipio_codigo)
    debug_print("=" * 60)

    debug_print()
    debug_print("AJAX DA UF DE MORADIA RECEBIDO.")
    debug_print("Agora vamos analisar os municípios retornados.")

    campos_pesquisa = {
        "pacienteForm:nomePaciente": nome,
        "pacienteForm:nomeMaePaciente": nome_mae,
        "pacienteForm:nomeSocial": nome_social,
        "pacienteForm:dataNascimento_input": nascimento,
        "pacienteForm:numeroCartao": cartao,
        "pacienteForm:documento": documento,
        "pacienteForm:uf_input": uf_codigo,
        "pacienteForm:ufNascimento_input": uf_nascimento_codigo,
    }

    for elemento in form.find_all(["input", "select", "textarea"]):

        name = elemento.get("name")

        if not name:
            continue

        tipo = (elemento.get("type") or "").lower()

        # Elementos desabilitados não participam da serialização.
        if elemento.has_attr("disabled"):
            continue

        # Checkbox/radio só entra se estiver marcado.
        if tipo in ("checkbox", "radio"):
            if not elemento.has_attr("checked"):
                continue

        # Campos de pesquisa recebem os valores digitados.
        if name in campos_pesquisa:
            dados.append(
                (name, campos_pesquisa[name])
            )
            continue

        # SELECT:
        # só enviar opções explicitamente selecionadas.
        # Não inventar a primeira opção quando nada estiver selecionado.
        if elemento.name == "select":
            selecionados = elemento.find_all(
                "option",
                selected=True
            )

            for option in selecionados:
                dados.append(
                    (
                        name,
                        option.get("value", "")
                    )
                )

            continue

        # Inputs normais.
        value = elemento.get("value")

        if value is not None:
            dados.append(
                (name, value)
            )

    # O componente de município estava desabilitado no HTML
    # original, então adicionamos explicitamente o código carregado
    # pelo AJAX da UF.
    dados.append(
        ("pacienteForm:municipio_input", municipio_codigo)
    )

    # ------------------------------------------------------------
    # CAMPOS AJAX DO PRIMEFACES
    # ------------------------------------------------------------

    dados.append(
        ("javax.faces.partial.ajax", "true")
    )

    dados.append(
        ("javax.faces.source", source)
    )

    dados.append(
        ("javax.faces.partial.execute", process)
    )

    dados.append(
        ("javax.faces.partial.render", update)
    )

    # O botão submit também participa do serializeArray().
    dados.append(
        (source, source)
    )

    debug_print()
    debug_print("=" * 60)
    debug_print("      CAMPOS DE LOCALIZAÇÃO NO PAYLOAD")
    debug_print("=" * 60)

    for _nome_campo, _valor_campo in dados:
        if any(
            termo in _nome_campo.lower()
            for termo in ("uf", "municipio", "cidade")
        ):
            if _nome_campo in (
                "pacienteForm:uf_input",
                "pacienteForm:municipio_input",
                "pacienteForm:uf",
                "pacienteForm:municipio",
            ):
                debug_print(
                    _nome_campo,
                    "=>",
                    repr(_valor_campo)
                )

    debug_print("=" * 60)

    print()

    print()

    print()

    # ------------------------------------------------------------
    # CONSULTA PRINCIPAL
    # O SI-PNI pode demorar em pesquisas amplas.
    # Faz até 3 tentativas antes de abortar.
    # ------------------------------------------------------------

    r = None

    for tentativa in range(1, 4):

        try:

            r = post_com_spinner(session,
                action,
                data=dados,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Faces-Request": "partial/ajax",
                    "Accept": "application/xml, text/xml, */*; q=0.01",
                    "Referer": action,
                },
                timeout=(15, 90),
            )

            break

        except requests.exceptions.ReadTimeout:

            print(
                f"SI-PNI demorou mais de 90 segundos "
                f"na tentativa {tentativa}."
            )

            if tentativa < 3:
                pass
            else:
                print()
                print(
                    "ERRO: o SI-PNI não respondeu após "
                    "3 tentativas."
                )
                return

        except requests.exceptions.RequestException as erro:

            print()
            print(
                "ERRO de conexão com o SI-PNI:",
                erro
            )
            return

    # ============================================================
    # DEBUG OPCIONAL
    # ============================================================
    # Por padrão, a resposta bruta do SI-PNI não é salva.
    # Para ativar temporariamente:
    #
    #     SIPNI_DEBUG=1
    #
    # Isso evita deixar dados pessoais da consulta em arquivo.

    sipni_debug = os.getenv("SIPNI_DEBUG", "").strip() == "1"

    if sipni_debug:
        debug_print()
        debug_print("========================================")
        debug_print("       DIAGNÓSTICO DA RESPOSTA")
        debug_print("========================================")
        debug_print("HTTP:", r.status_code)
        debug_print("Content-Type:", r.headers.get("Content-Type"))
        debug_print("Tamanho:", len(r.text), "bytes")

        Path("sipni_debug_response.xml").write_text(
            r.text,
            encoding="utf-8",
            errors="replace",
        )
        debug_print("DEBUG: resposta salva em sipni_debug_response.xml")
    else:
        debug_print("DEBUG: resposta bruta não salva")
        
    debug_print()

    erro = re.search(
        r"<error\b[^>]*>(.*?)</error>",
        r.text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if erro:
        erro_texto = BeautifulSoup(
            erro.group(1),
            "html.parser",
        ).get_text(" ", strip=True)

        debug_print("ERRO JSF DETECTADO")
        debug_print("Tipo de conteúdo do erro:", len(erro_texto), "caracteres")
        debug_print()
        return

    redirect = re.search(
        r"<redirect\b[^>]*url=[\"']([^\"']+)[\"']",
        r.text,
        flags=re.IGNORECASE,
    )

    debug_print(
        "REDIRECT:",
        "SIM" if redirect else "NÃO"
    )

    updates = list(
        re.finditer(
            r"<update\b([^>]*)>(.*?)</update>",
            r.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    debug_print(
        "UPDATES ENCONTRADOS:",
        len(updates)
    )
    debug_print()

    # ------------------------------------------------------------
    # CLASSIFICAÇÃO DO RESULTADO
    # ------------------------------------------------------------

    update_paciente = None

    for match in updates:
        atributos = match.group(1)
        conteudo = match.group(2)

        id_match = re.search(
            r'id=[\"\']([^\"\']+)[\"\']',
            atributos,
            flags=re.IGNORECASE,
        )

        if id_match and id_match.group(1) == "pacienteForm":
            update_paciente = conteudo

            try:
                Path("debug_sipni_update.html").write_text(
                    update_paciente,
                    encoding="utf-8"
                )
                debug_print("DEBUG: update_paciente salvo em debug_sipni_update.html")
            except Exception as e:
                debug_print("DEBUG: erro ao salvar update:", e)

            break

    # ============================================================
    # DEBUG: AJAX DA PRÓXIMA PÁGINA
    # ============================================================

    if update_paciente:
        debug_print()
        debug_print("=" * 60)
        debug_print("          AJAX DA PRÓXIMA PÁGINA")
        debug_print("=" * 60)

        # Procuramos somente elementos do paginator.
        # Nenhum conteúdo das linhas dos pacientes é exibido.

        paginator_tags = re.findall(
            r'<[^>]+class=["\'][^"\']*ui-paginator-(?:next|page|last)[^"\']*["\'][^>]*>',
            update_paciente,
            flags=re.IGNORECASE,
        )

        encontrou_next = False

        for tag in paginator_tags:
            if "ui-paginator-next" in tag.lower():
                encontrou_next = True

                debug_print("NEXT ENCONTRADO:")

                m_onclick = re.search(
                    r'onclick=["\']([^"\']+)["\']',
                    tag,
                    flags=re.IGNORECASE,
                )

                m_class = re.search(
                    r'class=["\']([^"\']+)["\']',
                    tag,
                    flags=re.IGNORECASE,
                )

                if m_class:
                    debug_print("  CLASS:", m_class.group(1))

                if m_onclick:
                    debug_print("  ONCLICK:", m_onclick.group(1))
                else:
                    debug_print("  ONCLICK: não encontrado")

        if not encontrou_next:
            debug_print("NEXT não encontrado.")

        # Também procuramos comandos setPage relacionados ao DataTable.
        comandos = re.findall(
            r'listaPacienteTable[^;]{0,500}setPage\(\s*\d+\s*\)[^;]{0,500}',
            update_paciente,
            flags=re.IGNORECASE,
        )

        if comandos:
            debug_print()
            debug_print("COMANDOS setPage ENCONTRADOS:")

            for comando in comandos[:10]:
                comando = re.sub(r'\s+', ' ', comando).strip()
                debug_print(" ", comando[:700])

        debug_print("=" * 60)

    # ============================================================
    # ============================================================
    # INVESTIGAÇÃO DO AJAX DA PAGINAÇÃO
    # ============================================================
    if update_paciente:
        debug_print()
        debug_print("=" * 60)
        debug_print("       INVESTIGAÇÃO DO AJAX DA PAGINAÇÃO")
        debug_print("=" * 60)

        scripts = re.findall(
            r"<script[^>]*>(.*?)</script>",
            update_paciente,
            flags=re.IGNORECASE | re.DOTALL
        )

        encontrados = []

        for script in scripts:
            if "listaPacienteTable" not in script:
                continue

            for linha in script.splitlines():
                low = linha.lower()

                if any(x in low for x in [
                    "paginator",
                    "paginate",
                    "primefaces.ab",
                    "setpage",
                    "page:"
                ]):
                    if any(x in low for x in [
                        "nomepaciente",
                        "nomemae",
                        "numerocartao",
                        "documento",
                        "datanascimento",
                        "endereco",
                        "bairro",
                        "municipio"
                    ]):
                        continue

                    linha = re.sub(r"\\s+", " ", linha).strip()

                    if linha:
                        encontrados.append(linha)

        if encontrados:
            vistos = set()
            debug_print("CONFIGURAÇÕES ENCONTRADAS:")

            for linha in encontrados:
                if linha in vistos:
                    continue

                vistos.add(linha)
                debug_print(" ", linha[:1500])
        else:
            debug_print("NENHUM SCRIPT DE PAGINAÇÃO ENCONTRADO.")

        debug_print("=" * 60)

    # DEBUG DO PAGINATOR — RESPOSTA ATUAL
    # ============================================================

    if update_paciente:
        debug_print()
        debug_print("=" * 60)
        debug_print("            PAGINAÇÃO ATUAL DO SI-PNI")
        debug_print("=" * 60)

        m_pag = re.search(
            r"paginator:\{(.*?)\}\s*\}\s*\);",
            update_paciente,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if m_pag:
            pag = re.sub(r"\s+", " ", m_pag.group(1)).strip()

            m_rows = re.search(r"rows\s*:\s*(\d+)", pag)
            m_count = re.search(r"rowCount\s*:\s*(\d+)", pag)
            m_page = re.search(r"page\s*:\s*(\d+)", pag)

            debug_print(
                "  rows:",
                m_rows.group(1) if m_rows else "?"
            )

            debug_print(
                "  rowCount:",
                m_count.group(1) if m_count else "?"
            )

            debug_print(
                "  page:",
                m_page.group(1) if m_page else "?"
            )

        else:
            debug_print("  Paginator não encontrado na resposta atual.")

        # Mostra apenas a informação textual da página atual.
        m_pagina = re.search(
            r"\[\s*Página:\s*([^<]+?)\s*\]",
            update_paciente,
            flags=re.IGNORECASE,
        )

        if m_pagina:
            debug_print("  Página:", m_pagina.group(1).strip())

        # Estado do botão NEXT.
        m_next = re.search(
            r'<span[^>]+class=["\'][^"\']*ui-paginator-next[^"\']*["\'][^>]*>',
            update_paciente,
            flags=re.IGNORECASE,
        )

        if m_next:
            classe_next = m_next.group(0)

            if "ui-state-disabled" in classe_next:
                debug_print("  Próxima página: DESABILITADA")
            else:
                debug_print("  Próxima página: DISPONÍVEL")
        else:
            debug_print("  Botão NEXT: não encontrado")

        debug_print("=" * 60)

    # ============================================================
    # EXTRAÇÃO + PAGINAÇÃO AUTOMÁTICA
    # ============================================================

    quantidade = 0
    resultados = []
    total_resultados = None

    # ------------------------------------------------------------
    # 1. EXTRAI O VIEWSTATE ATUAL DA PESQUISA
    # ------------------------------------------------------------

    viewstate = extrair_viewstate_ajax(r.text)

    if not viewstate:
        print()
        print("AVISO: ViewState da pesquisa não encontrado.")
        print("Apenas a página atual será processada.")

    # ------------------------------------------------------------
    # 2. DESCOBRE A CONFIGURAÇÃO DO PAGINATOR
    # ------------------------------------------------------------

    rows = 10
    row_count = 0
    pagina_atual = 0

    if update_paciente:

        m_pag = re.search(
            r"paginator:\{(.*?)\}\s*\}\s*\);",
            update_paciente,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if m_pag:
            pag = re.sub(
                r"\s+",
                " ",
                m_pag.group(1),
            ).strip()

            m_rows = re.search(
                r"rows\s*:\s*(\d+)",
                pag,
            )

            m_count = re.search(
                r"rowCount\s*:\s*(\d+)",
                pag,
            )

            m_page = re.search(
                r"page\s*:\s*(\d+)",
                pag,
            )

            if m_rows:
                rows = int(m_rows.group(1))

            if m_count:
                row_count = int(m_count.group(1))
                total_resultados = row_count

            if m_page:
                pagina_atual = int(m_page.group(1))

    # ------------------------------------------------------------
    # 3. PROCESSA A PÁGINA 1
    # ------------------------------------------------------------

    resultados_brutos = []

    if update_paciente:
        resultados_brutos.extend(
            extrair_resultados_pagina(update_paciente)
        )

    debug_print()
    debug_print("=" * 60)
    paginas_totais = (
        (row_count + rows - 1) // rows
        if row_count > 0 and rows > 0
        else 1
    )

    debug_print()
    debug_print("=" * 60)
    debug_print("          RESUMO DA CONSULTA")
    debug_print("=" * 60)
    debug_print("Nome:", "PREENCHIDO" if nome else "VAZIO")
    debug_print("Nome da mãe:", "PREENCHIDO" if nome_mae else "VAZIO")
    debug_print("Nome social:", "PREENCHIDO" if nome_social else "VAZIO")
    debug_print("Data de nascimento:", "PREENCHIDO" if nascimento else "VAZIO")
    debug_print("Cartão SUS:", "PREENCHIDO" if cartao else "VAZIO")
    debug_print("Documento:", "PREENCHIDO" if documento else "VAZIO")
    debug_print("UF:", uf)
    debug_print("Município:", municipio)
    debug_print()
    debug_print("Total encontrado:", row_count)
    debug_print("Registros por página:", rows)
    debug_print("Total de páginas:", paginas_totais)
    debug_print("=" * 60)

    pagina_atual = 0
    viewstate_atual = viewstate
    update_atual = update_paciente

    while True:

        resultados_brutos = []

        if update_atual:
            resultados_brutos = extrair_resultados_pagina(
                update_atual
            )

        print()
        print()
        print(
            VERMELHO_ESCURO + NEGRITO +
            f"             PÁGINA {pagina_atual + 1} "
            f"DE {paginas_totais}" +
            RESET
        )
        print(CINZA_ESCURO + "=" * 60 + RESET)

        print(
            VERMELHO_ESCURO + "Registros nesta página: " +
            RESET + CINZA +
            str(len(resultados_brutos)) +
            RESET
        )

        print(CINZA_ESCURO + "=" * 60 + RESET)

        for numero, resultado in enumerate(
            resultados_brutos,
            start=1
        ):
            print()
            print(
                VERMELHO_ESCURO + NEGRITO +
                f"[{numero}]" +
                RESET
            )

            print(
                VERMELHO_ESCURO + "Nome: " + RESET +
                CINZA + resultado.get("nome", "") +
                RESET
            )

            print(
                VERMELHO_ESCURO + "Mãe: " + RESET +
                CINZA + resultado.get("nome_mae", "") +
                RESET
            )

            print(
                VERMELHO_ESCURO + "Município: " + RESET +
                CINZA + resultado.get("municipio", "") +
                RESET
            )

            print(
                VERMELHO_ESCURO + "Estado: " + RESET +
                CINZA + resultado.get("estado", "") +
                RESET
            )

            print(
                VERMELHO_ESCURO + "Nascimento: " + RESET +
                CINZA + resultado.get("nascimento", "") +
                RESET
            )

            if resultado.get("cartao_sus"):
                print(
                    VERMELHO_ESCURO + "Cartão SUS: " + RESET +
                    CINZA + resultado.get("cartao_sus") +
                    RESET
                )

            if resultado.get("endereco"):
                print(
                    VERMELHO_ESCURO + "Endereço: " + RESET +
                    CINZA + resultado.get("endereco") +
                    RESET
                )

            if resultado.get("bairro"):
                print(
                    VERMELHO_ESCURO + "Bairro: " + RESET +
                    CINZA + resultado.get("bairro") +
                    RESET
                )

        print()
        print(CINZA_ESCURO + "=" * 60 + RESET)

        if paginas_totais <= 1:
            break

        print()
        sair_paginacao = False

        while True:
            print()
            print(VERMELHO_ESCURO + "[P]" + RESET + CINZA + " Próxima página" + RESET)
            print(VERMELHO_ESCURO + "[A]" + RESET + CINZA + " Página anterior" + RESET)
            print(VERMELHO_ESCURO + "[C]" + RESET + CINZA + " Copiar página" + RESET)
            print(VERMELHO_ESCURO + "[S]" + RESET + CINZA + " Sair da paginação" + RESET)

            opcao = input("Escolha: ").strip().upper()
            debug_print("DEBUG OPCÃO:", repr(opcao))

            if opcao == "S":
                sair_paginacao = True
                break

            if opcao == "C":

                texto_copiado = []

                texto_copiado.append("SI-PNI — RESULTADOS")
                texto_copiado.append("")
                texto_copiado.append(
                    f"Página: {pagina_atual + 1}/{paginas_totais}"
                )
                texto_copiado.append(
                    f"Registros nesta página: {len(resultados_brutos)}"
                )
                texto_copiado.append("")

                for numero, resultado in enumerate(
                    resultados_brutos,
                    start=1
                ):
                    texto_copiado.append(f"[{numero}]")

                    texto_copiado.append(
                        "Nome: " + resultado.get("nome", "")
                    )

                    texto_copiado.append(
                        "Mãe: " + resultado.get("nome_mae", "")
                    )

                    texto_copiado.append(
                        "Município: " + resultado.get("municipio", "")
                    )

                    texto_copiado.append(
                        "Estado: " + resultado.get("estado", "")
                    )

                    texto_copiado.append(
                        "Nascimento: " + resultado.get("nascimento", "")
                    )

                    if resultado.get("cartao_sus"):
                        texto_copiado.append(
                            "Cartão SUS: "
                            + resultado.get("cartao_sus", "")
                        )

                    if resultado.get("endereco"):
                        texto_copiado.append(
                            "Endereço: "
                            + resultado.get("endereco", "")
                        )

                    if resultado.get("bairro"):
                        texto_copiado.append(
                            "Bairro: "
                            + resultado.get("bairro", "")
                        )

                    texto_copiado.append("")

                texto_copiado = "\n".join(texto_copiado)

                try:
                    import subprocess

                    subprocess.run(
                        ["termux-clipboard-set"],
                        input=texto_copiado,
                        text=True,
                        check=True,
                        timeout=10,
                    )

                    print()
                    print("✓ Página copiada para a área de transferência.")

                except FileNotFoundError:
                    print()
                    print(
                        "ERRO: termux-clipboard-set não está disponível."
                    )
                    print(
                        "Instale o Termux:API e o pacote termux-api."
                    )

                except Exception as erro:
                    print()
                    print(
                        "ERRO ao copiar para a área de transferência:",
                        erro
                    )

                continue

            if opcao == "P":

                if pagina_atual >= paginas_totais - 1:
                    print("Você já está na última página.")
                    continue

                nova_pagina = pagina_atual + 1

            elif opcao == "A":

                if pagina_atual <= 0:
                    print("Você já está na primeira página.")
                    continue

                nova_pagina = pagina_atual - 1

            else:
                print("Opção inválida.")
                continue

            novo_update, novo_viewstate = paginar_resultados(
                session,
                action,
                update_atual,
                viewstate_atual,
                nova_pagina,
                rows,
            )

            if not novo_update:
                print()
                print(
                    "ERRO: o SI-PNI não retornou a página solicitada."
                )
                sair_paginacao = True
                break

            update_atual = novo_update

            if novo_viewstate:
                viewstate_atual = novo_viewstate

            pagina_atual = nova_pagina

            break

        if sair_paginacao:
            break

    update_paciente = update_atual
    viewstate = viewstate_atual

    resultados_brutos = []

    if update_paciente:
        resultados_brutos = extrair_resultados_pagina(
            update_paciente
        )

    print()
    print(
        "Total de registros recebidos na página atual:",
        len(resultados_brutos),
    )

    # ------------------------------------------------------------
    # 4. FILTRO LOCAL DE MUNICÍPIO
    # ------------------------------------------------------------

    if municipio:
        resultados = [
            paciente
            for paciente in resultados_brutos
            if municipio_corresponde(
                paciente.get("municipio", ""),
                municipio,
            )
        ]
    else:
        resultados = resultados_brutos

    quantidade = len(resultados)

    print(
        "  Após filtro de município:",
        quantidade,
    )

    debug_print("=" * 60)

    # rowCount é a quantidade TOTAL de registros encontrados,
    # inclusive os que estão em outras páginas.
    total_resultados = None

    if update_paciente:
        m_count = re.search(
            r"rowCount\s*:\s*(\d+)",
            update_paciente,
            flags=re.IGNORECASE,
        )

        if m_count:
            total_resultados = int(m_count.group(1))
    print()



def main():
    session = requests.Session()

    session.headers.update({
        "User-Agent": "Mozilla/5.0"
    })

    if not fazer_login(session):
        return

    registro = abrir_registro(session)

    if not registro:
        return

    pesquisar(session, registro)


if __name__ == "__main__":
    main()
