"""
Iniciar com o Windows: o leitor.exe entra em
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run como
"leitor.exe" --ao-ligar. Ao entrar no Windows, o painel abre minimizado e
inicia as máquinas marcadas com "Iniciar ao ligar".

HKCU vale para o usuário atual e não precisa de permissão de administrador do
Windows. Só funciona no leitor.exe no Windows (em desenvolvimento, não há o que
registrar).
"""

import logging
import os
import sys
from typing import Any, Dict, Optional

CHAVE = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME = "LeitorHL7"
ARGUMENTO = "--ao-ligar"


def comando(exe: str) -> str:
    return f'"{exe}" {ARGUMENTO}'


class RegistroWindows:
    """Leitura e escrita do valor em HKCU\\...\\Run."""

    def ler(self) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE) as chave:
                valor, _ = winreg.QueryValueEx(chave, NOME)
                return str(valor)
        except FileNotFoundError:
            return None

    def gravar(self, valor: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE) as chave:
            winreg.SetValueEx(chave, NOME, 0, winreg.REG_SZ, valor)

    def apagar(self) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE, 0, winreg.KEY_SET_VALUE) as chave:
                winreg.DeleteValue(chave, NOME)
        except FileNotFoundError:
            pass


def suportado() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def estado(exe: str, registro: Any = None) -> Dict[str, Any]:
    """{"suportado", "ativo", "outro_exe"}: ativo só se o valor aponta para este .exe."""
    if registro is None:
        if not suportado():
            return {"suportado": False, "ativo": False, "outro_exe": ""}
        registro = RegistroWindows()
    try:
        valor = registro.ler()
    except OSError as e:
        logging.error(f"✗ Não foi possível ler a inicialização com o Windows: {e}")
        valor = None
    ativo = bool(valor) and os.path.normcase(exe) in os.path.normcase(valor)
    return {"suportado": True, "ativo": ativo, "outro_exe": valor if valor and not ativo else ""}


def definir(ativar: bool, exe: str, registro: Any = None) -> Dict[str, Any]:
    if registro is None:
        if not suportado():
            return {"ok": False, "mensagem": "Só o leitor.exe no Windows inicia com o computador."}
        registro = RegistroWindows()
    try:
        if ativar:
            registro.gravar(comando(exe))
            logging.info(f"✓ Iniciar com o Windows ativado ({comando(exe)}).")
        else:
            registro.apagar()
            logging.info("✓ Iniciar com o Windows desativado.")
        return {"ok": True}
    except OSError as e:
        logging.error(f"✗ Não foi possível {'ativar' if ativar else 'desativar'} o início com o Windows: {e}")
        return {"ok": False, "mensagem": f"O Windows recusou a alteração: {e}"}
