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

# Só existem no config da máquina que as declara nos defaults: máquina sem
# imagem não ganha chave de token e só o PKL tem pkl_machine_id. As chaves de
# conexão TCP (modo, tcp_host, tcp_port) valem para todas desde a 7.3.0.
CHAVES_OPCIONAIS = (
    "pkl_machine_id", "exam_images_url", "exam_images_machine_token",
    "modo", "tcp_host", "tcp_port", "consult_url",
)


VARIAVEL_PASTA_CONFIG = "LEITOR_HL7_CONFIG_DIR"
# Pasta raiz escolhida no painel do leitor universal: as pastas de trabalho
# (Analisador<MAQUINA>) ficam nela em vez da Área de Trabalho.
VARIAVEL_PASTA_DADOS = "LEITOR_HL7_DADOS_DIR"


def pasta_dados() -> str:
    """Onde ficam as pastas Analisador<MAQUINA>: a pasta raiz, se definida; senão a Área de Trabalho."""
    return os.environ.get(VARIAVEL_PASTA_DADOS) or os.path.join(os.path.expanduser("~"), "Desktop")


def _get_app_dir(analisador_nome: str) -> str:
    """
    Retorna o diretório onde o executável/script está localizado.
    Quando congelado (PyInstaller), usa sys.executable.
    Em desenvolvimento, usa __file__ do script chamador.
    """
    # Leitor universal: o painel abre cada máquina com a pasta do config dela
    # (escolhida em "Adicionar equipamento"). Sem a variável, nada muda.
    pasta_painel = os.environ.get(VARIAVEL_PASTA_CONFIG)
    if pasta_painel:
        return pasta_painel
    if getattr(sys, 'frozen', False):
        if not sys.platform.startswith('win'):
            # Linux empacotado (AppImage é só leitura; /opt não é do usuário):
            # configs em ~/.config/leitor-hl7. No Windows nada muda.
            base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
            pasta = os.path.join(base, 'leitor-hl7')
            os.makedirs(pasta, exist_ok=True)
            return pasta
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
                  - CHAVES_OPCIONAIS: entram no config só se presentes aqui

    Returns:
        Tuple[dict, str]: (config_dict, status_message)
        - config_dict: dicionário com as chaves com_port, baud_rate,
          franchise_credential_id, webhook_url, config_file_path e as
          CHAVES_OPCIONAIS declaradas nos defaults
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
        # De-para de códigos de exame, comum a todas as máquinas:
        # {código da máquina: código do Vida}, ex.: {"HDLE": "HDL"}.
        "depara_codigos": defaults.get("depara_codigos", {}),
    }
    for chave in CHAVES_OPCIONAIS:
        if chave in defaults:
            config[chave] = defaults[chave]

    status_msg = ""

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = json.load(f)

            # Mescla apenas chaves conhecidas (ignora chaves estranhas)
            for key in config:
                if key in file_config:
                    config[key] = file_config[key]

            # Versões novas podem acrescentar chaves (ex.: envio de imagens).
            # Grava as que faltavam mantendo as chaves desconhecidas do arquivo.
            faltantes = {key: value for key, value in config.items() if key not in file_config}

            if faltantes:
                file_config.update(faltantes)
                try:
                    with open(config_path, 'w', encoding='utf-8') as f:
                        json.dump(file_config, f, indent=4, ensure_ascii=False)
                    status_msg = (
                        f"[Config] Arquivo de configuracao carregado: {config_path} "
                        f"(chaves novas: {', '.join(faltantes)})"
                    )
                except IOError as e:
                    status_msg = (
                        f"[Config] Arquivo de configuracao carregado: {config_path} "
                        f"(nao foi possivel gravar chaves novas: {e})"
                    )
            else:
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
