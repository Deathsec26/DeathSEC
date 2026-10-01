# DEATHSEC — SI-PNI Bot

Bot local para automação de consultas no SI-PNI, executado diretamente pelo Termux.

## Requisitos

- Android com Termux
- Python 3
- FIGlet
- Acesso autorizado ao SI-PNI
- Credenciais válidas

## Instalação

    ./install.sh
    cp .env.example .env
    nano .env
    ./start.sh

## Uso

- `restart` — reinicia o preenchimento
- `P` — próxima página
- `A` — página anterior
- `C` — copiar página
- `S` — sair

## Debug

Use `SIPNI_DEBUG=1` no `.env` para ativar mensagens técnicas.

## Segurança

Utilize o projeto somente com autorização.

Nunca publique ou compartilhe:
- `.env`
- credenciais
- cookies ou tokens
- dados de pacientes
- informações pessoais ou de saúde

## Estrutura

- `consulta_sipni.py` — programa principal
- `start.sh` — inicialização
- `install.sh` — instalação
- `requirements.txt` — dependências Python
- `.env.example` — modelo de configuração
- `.gitignore` — arquivos ignorados pelo Git
- `fontes_figlet/` — fontes do banner
