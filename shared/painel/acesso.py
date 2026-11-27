"""
Acesso de administrador do painel: toda alteração de configuração (salvar,
adicionar e remover equipamento, trocar a porta sugerida) exige a senha mestra.

A senha mestra é a mesma em todas as unidades e fica embutida no .exe só como
hash PBKDF2-SHA256 (HASH_SENHA_MESTRA). O repositório é público: nunca grave a
senha em texto em lugar nenhum, só o hash. Use uma senha longa (14+ caracteres),
porque o hash publicado pode ser atacado por força bruta fora do programa.

Para definir ou trocar a senha (atualiza a linha HASH_SENHA_MESTRA abaixo):

    python3 -m shared.painel.acesso

Depois, recompile o leitor.exe. Sem hash definido, a edição fica bloqueada.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

# Gerado por "python3 -m shared.painel.acesso". Vazio = edição bloqueada.
HASH_SENHA_MESTRA = "pbkdf2_sha256$600000$51b55579e0db4d4d2381348ab4c3518a$e9bb45986111b04387575dfa9e4ef09089fe565dc59c469b57eee5bce9f918aa"

ALGORITMO = "pbkdf2_sha256"
ITERACOES = 600_000
TAMANHO_MINIMO = 10
# Sessão de administrador: termina após este tempo sem alterar nada.
TEMPO_SESSAO = 15 * 60
# Senha errada várias vezes seguidas: espera antes de tentar de novo.
MAX_TENTATIVAS = 5
BLOQUEIO = 60


def gerar_hash(senha: str, iteracoes: int = ITERACOES, sal: Optional[bytes] = None) -> str:
    sal = sal or secrets.token_bytes(16)
    resumo = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, iteracoes)
    return f"{ALGORITMO}${iteracoes}${sal.hex()}${resumo.hex()}"


def conferir(senha: str, guardado: str) -> bool:
    try:
        algoritmo, iteracoes, sal, resumo = guardado.split("$")
        if algoritmo != ALGORITMO:
            return False
        calculado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(sal), int(iteracoes))
        return hmac.compare_digest(calculado.hex(), resumo)
    except (ValueError, AttributeError):
        return False


class Sessao:
    """Quem está no painel agora: operador (só leitura) ou administrador."""

    def __init__(
        self,
        hash_senha: Optional[str] = None,
        duracao: float = TEMPO_SESSAO,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hash_senha = HASH_SENHA_MESTRA if hash_senha is None else hash_senha
        self.duracao = duracao
        self._relogio = relogio
        self._lock = threading.Lock()
        self._ultimo_uso: Optional[float] = None
        self._erros = 0
        self._bloqueado_ate = 0.0

    @property
    def definida(self) -> bool:
        return bool(self.hash_senha)

    def entrar(self, senha: str) -> Dict[str, Any]:
        with self._lock:
            agora = self._relogio()
            if not self.definida:
                logging.error("✗ Login de administrador recusado: este build não tem senha mestra definida.")
                return {"ok": False, "mensagem": "Este leitor.exe foi gerado sem senha mestra. "
                                                 "A edição fica bloqueada até um build com a senha."}
            if agora < self._bloqueado_ate:
                espera = int(self._bloqueado_ate - agora) + 1
                logging.warning(f"✗ Login de administrador bloqueado por mais {espera}s (tentativas erradas).")
                return {"ok": False, "mensagem": f"Muitas tentativas erradas. Tente de novo em {espera}s."}
            if not conferir(senha, self.hash_senha):
                self._erros += 1
                logging.warning(f"✗ Login de administrador: senha incorreta ({self._erros}/{MAX_TENTATIVAS}).")
                if self._erros >= MAX_TENTATIVAS:
                    self._erros = 0
                    self._bloqueado_ate = agora + BLOQUEIO
                    return {"ok": False, "mensagem": f"Senha incorreta. Novas tentativas só daqui a {BLOQUEIO}s."}
                return {"ok": False, "mensagem": "Senha incorreta."}
            self._erros = 0
            self._ultimo_uso = agora
            logging.info("✓ Administrador entrou no painel (configuração liberada para edição).")
            return {"ok": True}

    def sair(self, motivo: str = "saiu") -> None:
        with self._lock:
            if self._ultimo_uso is None:
                return
            self._ultimo_uso = None
        logging.info(f"Administrador {motivo}: configuração bloqueada para edição.")

    def ativa(self) -> bool:
        with self._lock:
            if self._ultimo_uso is None:
                return False
            if self._relogio() - self._ultimo_uso <= self.duracao:
                return True
            self._ultimo_uso = None
        logging.info(f"Sessão de administrador encerrada após {int(self.duracao // 60)} min sem uso.")
        return False

    def renovar(self) -> bool:
        """Chamado a cada alteração liberada: conta o tempo de sessão do zero."""
        if not self.ativa():
            return False
        with self._lock:
            self._ultimo_uso = self._relogio()
        return True


def _gravar_hash(novo: str, arquivo: str = __file__) -> None:
    with open(arquivo, "r", encoding="utf-8") as f:
        texto = f.read()
    texto, n = re.subn(r'^HASH_SENHA_MESTRA = ".*"$', f'HASH_SENHA_MESTRA = "{novo}"', texto, count=1, flags=re.M)
    if n != 1:
        raise RuntimeError("linha HASH_SENHA_MESTRA não encontrada")
    with open(arquivo, "w", encoding="utf-8") as f:
        f.write(texto)


def main() -> int:
    """Define a senha mestra: pede duas vezes e grava só o hash neste arquivo."""
    import getpass

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    senha = getpass.getpass("Nova senha mestra: ")
    if len(senha) < TAMANHO_MINIMO:
        logging.error(f"✗ Use ao menos {TAMANHO_MINIMO} caracteres (recomendado: 14 ou mais).")
        return 1
    if getpass.getpass("Repita a senha: ") != senha:
        logging.error("✗ As senhas não conferem. Nada foi alterado.")
        return 1
    _gravar_hash(gerar_hash(senha))
    logging.info(f"✓ Hash da senha mestra gravado em {os.path.relpath(__file__)}. Recompile o leitor.exe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
