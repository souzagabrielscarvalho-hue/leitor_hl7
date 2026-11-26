#!/usr/bin/env bash
# =============================================================================
# _enviar_pessoal.sh - commita tudo e envia a branch atual para o repositorio
# PESSOAL (souzagabrielscarvalho-hue/leitor_hl7, publico).
# Chamado pelos .ps1 via WSL; roda com o git do Ubuntu (ja autenticado).
# Uso: _enviar_pessoal.sh ["mensagem do commit"]
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

URL_PESSOAL="https://github.com/souzagabrielscarvalho-hue/leitor_hl7.git"
PADRAO_TOKEN='exam_images_machine_token.{1,6}[A-Za-z0-9]{20,}'

MSG="${1:-}"
[ -z "$MSG" ] && MSG="Atualizacao - $(date '+%Y-%m-%d %H:%M:%S')"

BRANCH="$(git symbolic-ref --short -q HEAD)" || { echo "ENVIO CANCELADO: HEAD destacado."; exit 1; }

git add -A

# git grep sai com 0 quando encontra o token.
if git grep -I -l -E "$PADRAO_TOKEN" --cached >/dev/null 2>&1; then
    echo "ENVIO CANCELADO: token real em arquivo versionado."
    git reset -q
    exit 1
fi

if git diff --cached --quiet; then
    echo "PESSOAL: nenhuma mudanca para commitar."
else
    git commit -q -m "$MSG"
    echo "PESSOAL: commit \"$MSG\" criado."
fi

echo "PESSOAL: enviando $BRANCH para o repositorio publico..."
git push "$URL_PESSOAL" "HEAD:refs/heads/$BRANCH"
echo ">>> PESSOAL enviado com sucesso."
