#!/usr/bin/env bash
# =============================================================================
# release.sh - publica a versao atual do leitor universal (leitor.exe) nas
# releases do repositorio PESSOAL (souzagabrielscarvalho-hue/leitor_hl7), de
# onde o painel das unidades busca atualizacao (Configuracoes > Verificar
# atualizacao).
#
# So cria e envia a tag vX.Y.Z. Quem compila e publica e o GitHub Actions
# (.github/workflows/release.yml): testes, leitor.exe no Windows, SHA-256 e a
# release com as notas do CHANGELOG.
#
# Antes: suba a VERSAO (shared/versao.py), escreva a secao no CHANGELOG.md e
# envie o codigo com ./enviar.sh. Uso: ./release.sh
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

URL_PESSOAL="https://github.com/souzagabrielscarvalho-hue/leitor_hl7.git"
PAGINA="https://github.com/souzagabrielscarvalho-hue/leitor_hl7"

VERSAO="$(sed -n 's/^VERSAO = "\(.*\)"$/\1/p' shared/versao.py)"
TAG="v$VERSAO"

# Tag x VERSAO x CHANGELOG (o mesmo script que o workflow usa).
python3 Comandos/notas_release.py "$TAG"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "RELEASE CANCELADA: ha alteracoes sem commit. Envie antes com ./enviar.sh."
    exit 1
fi

BRANCH="$(git symbolic-ref --short -q HEAD)" || { echo "RELEASE CANCELADA: HEAD destacado."; exit 1; }
HEAD_LOCAL="$(git rev-parse HEAD)"
HEAD_REMOTO="$(git ls-remote "$URL_PESSOAL" "refs/heads/$BRANCH" | cut -f1)"
if [ "$HEAD_LOCAL" != "$HEAD_REMOTO" ]; then
    echo "RELEASE CANCELADA: o commit atual ainda nao esta em $BRANCH no repositorio pessoal."
    echo "Envie antes com ./enviar.sh pessoal."
    exit 1
fi
if [ "$BRANCH" != "main" ]; then
    echo "AVISO: a release sai da branch '$BRANCH', nao da main."
fi

if git ls-remote --tags "$URL_PESSOAL" "refs/tags/$TAG" | grep -q .; then
    echo "RELEASE CANCELADA: a tag $TAG ja existe no repositorio pessoal. Suba a VERSAO."
    exit 1
fi

echo "Vai publicar $TAG a partir de $BRANCH ($HEAD_LOCAL)."
read -r -p "Confirma? [s/N] " RESP
[ "$RESP" = "s" ] || [ "$RESP" = "S" ] || { echo "Cancelado."; exit 1; }

git tag -a "$TAG" -m "Leitor HL7 $VERSAO"
git push "$URL_PESSOAL" "refs/tags/$TAG"
echo ">>> Tag $TAG enviada. O GitHub Actions compila e publica a release:"
echo "    $PAGINA/actions"
echo "    $PAGINA/releases/tag/$TAG"
