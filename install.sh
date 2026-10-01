#!/data/data/com.termux/files/usr/bin/bash

set -e

cd "$(dirname "$0")"

echo "========================================"
echo "        DEATHSEC — SI-PNI BOT"
echo "             INSTALAÇÃO"
echo "========================================"
echo

echo "[1/4] Verificando Python..."

if ! command -v python >/dev/null 2>&1; then
    echo "Python não encontrado. Instalando..."
    pkg install -y python
fi

python --version
echo

echo "[2/4] Verificando FIGlet..."

if ! command -v figlet >/dev/null 2>&1; then
    echo "FIGlet não encontrado. Instalando..."
    pkg install -y figlet
fi

figlet -f big "DEATHSEC"
echo

echo "[3/4] Instalando dependências Python..."
python -m pip install -r requirements.txt
echo

echo "[4/4] Preparando inicialização..."
chmod +x start.sh

echo
echo "========================================"
echo "        INSTALAÇÃO CONCLUÍDA"
echo "========================================"
echo
echo "Para iniciar:"
echo "  ./start.sh"
echo
