#!/usr/bin/env bash
# =============================================================================
# enviar.sh - envia as mudancas a partir do terminal do VSCode (WSL), usando o
# git do Ubuntu (ja autenticado).
#
# Uso: ./enviar.sh pessoal ["mensagem do commit"]   -> so o repositorio pessoal
#      ./enviar.sh ambos   ["mensagem do commit"]   -> pessoal e tambem a empresa
# =============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALVO="${1:-}"
MSG="${2:-}"

case "$ALVO" in
    pessoal)
        bash "$DIR/_enviar_pessoal.sh" "$MSG"
        ;;
    ambos|empresa)
        bash "$DIR/_enviar_pessoal.sh" "$MSG"
        bash "$DIR/_sincronizar_e_enviar_empresa.sh" "$MSG"
        ;;
    *)
        echo "Uso: ./enviar.sh pessoal|ambos [\"mensagem do commit\"]"
        echo "  pessoal -> envia so para o repositorio pessoal"
        echo "  ambos   -> envia para o pessoal e tambem para a empresa"
        exit 1
        ;;
esac
