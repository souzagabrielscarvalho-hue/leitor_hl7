"""
Confere se a tag da release bate com a VERSAO e grava as notas do CHANGELOG.

Usado pelo release.sh (antes de criar a tag) e pelo workflow
.github/workflows/release.yml (antes de compilar). Só biblioteca padrão.

Uso: python3 Comandos/notas_release.py <tag|atual> [arquivo_de_saida]
     ex.: python3 Comandos/notas_release.py v7.4.0 notas.md
     "atual" (teste em branch): só confere se o CHANGELOG tem a seção da VERSAO.
Sai com 1 (e a mensagem no stderr) se algo não confere.
"""

import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def versao_do_codigo() -> str:
    with open(os.path.join(RAIZ, "shared", "versao.py"), encoding="utf-8") as f:
        m = re.search(r'^VERSAO = "(\d+\.\d+\.\d+)"$', f.read(), re.M)
    if not m:
        raise ValueError("VERSAO não encontrada em shared/versao.py")
    return m.group(1)


def notas(versao: str) -> str:
    with open(os.path.join(RAIZ, "CHANGELOG.md"), encoding="utf-8") as f:
        texto = f.read()
    m = re.search(rf"^## {re.escape(versao)}\b.*?$(.*?)(?=^## |\Z)", texto, re.M | re.S)
    return m.group(1).strip() if m else ""


def main(argv: list) -> int:
    if not argv:
        sys.stderr.write(__doc__)
        return 1
    tag = argv[0].strip()
    try:
        versao = versao_do_codigo()
    except (OSError, ValueError) as e:
        sys.stderr.write(f"✗ {e}\n")
        return 1
    if tag == "atual":
        tag = f"v{versao}"
    if tag != f"v{versao}":
        sys.stderr.write(f"✗ A tag {tag} não bate com a VERSAO {versao} (shared/versao.py): use v{versao}.\n")
        return 1
    texto = notas(versao)
    if not texto:
        sys.stderr.write(f"✗ CHANGELOG.md não tem a seção '## {versao}'.\n")
        return 1
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as f:
            f.write(texto + "\n")
    sys.stderr.write(f"✓ Tag {tag} confere com a VERSAO; notas com {len(texto.splitlines())} linha(s).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
