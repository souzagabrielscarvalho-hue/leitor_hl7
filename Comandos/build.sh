#!/usr/bin/env bash
# =============================================================================
# build.sh - gera o .exe de uma maquina (ou de todas) a partir do terminal do
# VSCode (WSL). O PyInstaller roda no Windows via powershell.exe (interop), que
# e o unico jeito de gerar .exe do Windows.
#
# Uso: ./build.sh <maquina|todas>
#   maquinas: bh5100 ca150 coagmaster mek7300 pkl urit8021 vidas1600 todas
# =============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALVO="${1:-todas}"
PS1="$DIR/build_${ALVO}.ps1"

if [ ! -f "$PS1" ]; then
    echo "Alvo invalido: '$ALVO'."
    echo "Use um destes: bh5100 ca150 coagmaster mek7300 pkl urit8021 vidas1600 todas"
    exit 1
fi

if ! command -v powershell.exe >/dev/null 2>&1; then
    echo "powershell.exe nao esta acessivel (interop do WSL desabilitado)."
    echo "Rode o build pelo PowerShell do Windows."
    exit 1
fi

WINPS1="$(wslpath -w "$PS1")"
echo "Compilando ($ALVO) com o PyInstaller no Windows..."
powershell.exe -ExecutionPolicy Bypass -File "$WINPS1"
