#!/usr/bin/env bash
# =============================================================================
# _sincronizar_e_enviar_empresa.sh - sincroniza o codigo do leitor_hl7 (pessoal)
# para o clone do HL7_integrator (empresa), commita e envia.
#
# A empresa e um espelho do pessoal MENOS os arquivos pessoais. O script:
#   - NAO copia CLAUDE.md, enviar.ps1, build_all.ps1, codigo, a pasta Comandos/ nem
#     .github/ (as releases do leitor.exe saem so do repositorio pessoal)
#   - NAO toca em .git, venv, logs e configs reais da empresa
#   - remove da empresa os arquivos versionados que sumiram do pessoal
#
# Chamado pelos .ps1 via WSL; roda com o git do Ubuntu (ja autenticado).
# Uso: _sincronizar_e_enviar_empresa.sh ["mensagem do commit"]
# O caminho do clone da empresa pode ser sobrescrito por LEITOR_HL7_EMPRESA.
# =============================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="${LEITOR_HL7_EMPRESA:-/home/gabriel/Vida-Exame/HL7_integrator}"

URL_EMPRESA="https://github.com/Vida-Exames/HL7_integrator.git"
PADRAO_TOKEN='exam_images_machine_token.{1,6}[A-Za-z0-9]{20,}'

# Arquivos pessoais que nunca vao para a empresa. O .gitignore fica de fora para
# cada repo manter o seu (o da empresa ignora os arquivos pessoais).
EXCLUIR='^(CLAUDE\.md|enviar\.ps1|build_all\.ps1|codigo|Comandos/|\.github/|\.gitignore)'

MSG="${1:-}"
[ -z "$MSG" ] && MSG="Sincroniza com leitor_hl7 - $(date '+%Y-%m-%d %H:%M:%S')"

[ -d "$DST/.git" ] || { echo "ENVIO CANCELADO: clone da empresa nao encontrado em $DST"; exit 1; }

echo "EMPRESA: sincronizando codigo de $SRC para $DST ..."

# 1) Remove da empresa os arquivos versionados que sumiram do pessoal.
while IFS= read -r arq; do
    [ -n "$arq" ] || continue
    git -C "$DST" rm -q --ignore-unmatch -- "$arq" >/dev/null 2>&1 || true
done < <(comm -13 \
            <(git -C "$SRC" ls-files | grep -vE "$EXCLUIR" | sort) \
            <(git -C "$DST" ls-files | sort))

# 2) Copia os arquivos versionados do pessoal (menos os pessoais) para a empresa.
git -C "$SRC" ls-files -z \
    | grep -zvE "$EXCLUIR" \
    | rsync -a --from0 --files-from=- "$SRC/" "$DST/"

cd "$DST"
BRANCH="$(git symbolic-ref --short -q HEAD)" || { echo "ENVIO CANCELADO: HEAD destacado na empresa."; exit 1; }

git add -A

# git grep sai com 0 quando encontra o token.
if git grep -I -l -E "$PADRAO_TOKEN" --cached >/dev/null 2>&1; then
    echo "ENVIO CANCELADO (empresa): token real em arquivo versionado."
    git reset -q
    exit 1
fi

if git diff --cached --quiet; then
    echo "EMPRESA: ja estava sincronizada; nada para commitar."
else
    git commit -q -m "$MSG"
    echo "EMPRESA: commit \"$MSG\" criado."
fi

echo "EMPRESA: enviando $BRANCH para o HL7_integrator..."
git push "$URL_EMPRESA" "HEAD:refs/heads/$BRANCH"
echo ">>> EMPRESA enviada com sucesso."
