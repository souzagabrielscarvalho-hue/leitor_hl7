"""
Módulo compartilhado de carregamento de configuração para os analisadores HL7.

Cada analisador chama load_config() com seus valores padrão.
O arquivo JSON de configuração fica AO LADO do executável (ou script em dev),
permitindo editar COM_PORT e FRANCHISE_CREDENTIAL_ID sem recompilar o .exe.

Se o arquivo não existir, é criado automaticamente com os defaults fornecidos.

IMPORTANTE: Este módulo NÃO usa logging, pois é chamado antes do
logging.basicConfig() nos scripts principais. Usar logging aqui faria o
Python configurar o root logger implicitamente, ignorando o basicConfig()
posterior e impedindo que logs fossem escritos no arquivo.
"""

import json
import os
import sys
from typing import Any, Dict, Tuple


def _get_app_dir(analisador_nome: str) -> str:
    """
    Retorna o diretório onde o executável/script está localizado.
    Quando congelado (PyInstaller), usa sys.executable.
    Em desenvolvimento, usa __file__ do script chamador.
    """
    if getattr(sys, 'frozen', False):
        # Rodando como executável PyInstaller
        return os.path.dirname(sys.executable)
    else:
        # Rodando como script Python
        return os.path.dirname(os.path.abspath(sys.argv[0]))


def load_config(
    analisador_nome: str,
    defaults: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """
    Carrega a configuração de um arquivo JSON externo ao executável.
    Se o arquivo não existir, cria-o com os valores padrão fornecidos.

    Args:
        analisador_nome: Nome do analisador (ex: 'mek7300', 'bh5100').
                         Usado para nomear o arquivo: config_<nome>.json
        defaults: Dicionário com valores padrão. As chaves esperadas são:
                  - com_port (str)
                  - baud_rate (int)
                  - franchise_credential_id (str, opcional)
                  - webhook_url (str, opcional — se não informado, é montado
                    a partir de franchise_credential_id + url_base)

    Returns:
        Tuple[dict, str]: (config_dict, status_message)
        - config_dict: dicionário com as chaves com_port, baud_rate,
          franchise_credential_id, webhook_url, config_file_path
        - status_message: string informativa sobre o que ocorreu (carregado,
          criado, erro, etc.) — pode ser usada para logging após basicConfig()
    """
    app_dir = _get_app_dir(analisador_nome)
    config_filename = f"config_{analisador_nome}.json"
    config_path = os.path.join(app_dir, config_filename)

    # Valores padrão
    config: Dict[str, Any] = {
        "com_port": defaults.get("com_port", "COM1"),
        "baud_rate": defaults.get("baud_rate", 9600),
        "franchise_credential_id": defaults.get("franchise_credential_id", ""),
        "webhook_url": defaults.get("webhook_url", ""),
    }

    status_msg = ""

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = json.load(f)

            # Mescla apenas chaves conhecidas (ignora chaves estranhas)
            for key in config:
                if key in file_config:
                    config[key] = file_config[key]

            status_msg = f"[Config] Arquivo de configuracao carregado: {config_path}"
        except (json.JSONDecodeError, IOError) as e:
            status_msg = f"[Config] Erro ao ler {config_path}: {e}. Usando valores padrao."
    else:
        # Cria o arquivo com os defaults
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            status_msg = f"[Config] Arquivo de configuracao criado: {config_path}"
        except IOError as e:
            status_msg = f"[Config] Nao foi possivel criar {config_path}: {e}. Usando defaults em memoria."

    config["config_file_path"] = config_path
    return config, status_msg
