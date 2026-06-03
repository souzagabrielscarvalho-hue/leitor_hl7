"""
Módulo compartilhado de limpeza de arquivos para os analisadores HL7.

Funcionalidades:
- Rotação de arquivos de log (por tamanho)
- Limpeza de arquivos antigos (por idade)
- Remoção de diretórios vazios
- Thread de limpeza periódica em background
"""

import os
import time
import logging
import datetime
import glob
from threading import Thread
from typing import Optional


# ============================================================
# CONFIGURAÇÕES PADRÃO (podem ser sobrescritas por analisador)
# ============================================================

# Tamanho máximo do arquivo de log antes da rotação (bytes)
# 5 MB = 5 * 1024 * 1024
DEFAULT_MAX_LOG_SIZE = 5 * 1024 * 1024

# Número máximo de rotações de log mantidas
DEFAULT_MAX_LOG_ROTATIONS = 5

# Idade máxima dos arquivos nas pastas de trabalho (dias)
DEFAULT_MAX_FILE_AGE_DAYS = 30

# Intervalo entre execuções da limpeza (segundos)
# 1 hora = 3600
DEFAULT_CLEANUP_INTERVAL = 3600


def rotate_log_file(log_file_path: str, max_size: int = DEFAULT_MAX_LOG_SIZE,
                    max_rotations: int = DEFAULT_MAX_LOG_ROTATIONS) -> None:
    """
    Verifica se o arquivo de log excedeu o tamanho máximo e, se sim,
    rotaciona (renomeia com timestamp) e remove rotações excedentes.

    Args:
        log_file_path: Caminho absoluto do arquivo de log
        max_size: Tamanho máximo em bytes antes da rotação
        max_rotations: Número máximo de arquivos rotacionados mantidos
    """
    if not os.path.exists(log_file_path):
        return

    try:
        size = os.path.getsize(log_file_path)
    except OSError:
        return

    if size < max_size:
        return

    # Gera nome com timestamp para a rotação
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_name = os.path.dirname(log_file_path)
    base_name = os.path.basename(log_file_path)
    name, ext = os.path.splitext(base_name)
    rotated_name = os.path.join(dir_name, f"{name}.{timestamp}{ext}")

    try:
        os.rename(log_file_path, rotated_name)
        logging.info(f"[Cleanup] Log rotacionado: {base_name} → {os.path.basename(rotated_name)} "
                     f"({size / (1024*1024):.1f} MB)")
    except OSError as e:
        logging.warning(f"[Cleanup] Falha ao rotacionar log {base_name}: {e}")
        return

    # Remove rotações antigas excedentes
    _prune_rotated_logs(log_file_path, max_rotations)


def _prune_rotated_logs(log_file_path: str, max_rotations: int) -> None:
    """
    Remove arquivos de log rotacionados que excedem o limite máximo.

    Args:
        log_file_path: Caminho do arquivo de log original
        max_rotations: Número máximo de rotações a manter
    """
    dir_name = os.path.dirname(log_file_path)
    base_name = os.path.basename(log_file_path)
    name, ext = os.path.splitext(base_name)

    # Padrão: nome_do_log.YYYYMMDD_HHMMSS.ext
    pattern = os.path.join(dir_name, f"{name}.*{ext}")
    rotated_files = glob.glob(pattern)

    # Ordena por data de modificação (mais antigos primeiro)
    rotated_files.sort(key=os.path.getmtime)

    # Remove os mais antigos se exceder o limite
    excess = len(rotated_files) - max_rotations
    for i in range(excess):
        try:
            os.remove(rotated_files[i])
            logging.info(f"[Cleanup] Rotação antiga removida: {os.path.basename(rotated_files[i])}")
        except OSError as e:
            logging.warning(f"[Cleanup] Falha ao remover rotação antiga {rotated_files[i]}: {e}")


def cleanup_old_files(directory: str, max_age_days: int = DEFAULT_MAX_FILE_AGE_DAYS,
                      recursive: bool = True, file_pattern: str = "*") -> int:
    """
    Remove arquivos de um diretório que são mais antigos que max_age_days.

    Args:
        directory: Caminho do diretório a limpar
        max_age_days: Idade máxima em dias (arquivos mais velhos são removidos)
        recursive: Se True, percorre subdiretórios também
        file_pattern: Padrão glob para filtrar arquivos (ex: "*.log", "*.json")

    Returns:
        Número de arquivos removidos
    """
    if not os.path.isdir(directory):
        return 0

    cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)
    removed_count = 0

    if recursive:
        pattern = os.path.join(directory, "**", file_pattern)
        files = glob.glob(pattern, recursive=True)
    else:
        pattern = os.path.join(directory, file_pattern)
        files = glob.glob(pattern)

    for file_path in files:
        # Não remove diretórios
        if os.path.isdir(file_path):
            continue

        try:
            mtime = os.path.getmtime(file_path)
            if mtime < cutoff_time:
                os.remove(file_path)
                removed_count += 1
                logging.debug(f"[Cleanup] Arquivo antigo removido: {os.path.basename(file_path)}")
        except OSError as e:
            logging.warning(f"[Cleanup] Falha ao remover {file_path}: {e}")

    return removed_count


def cleanup_empty_directories(root_directory: str) -> int:
    """
    Remove subdiretórios vazios dentro de root_directory.

    Args:
        root_directory: Diretório raiz para buscar subdiretórios vazios

    Returns:
        Número de diretórios removidos
    """
    if not os.path.isdir(root_directory):
        return 0

    removed_count = 0

    # Percorre de baixo para cima (bottom-up) para remover diretórios filhos primeiro
    for dirpath, dirnames, filenames in os.walk(root_directory, topdown=False):
        # Não remove o diretório raiz
        if dirpath == root_directory:
            continue

        if not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
                removed_count += 1
                logging.debug(f"[Cleanup] Diretório vazio removido: {dirpath}")
            except OSError as e:
                logging.debug(f"[Cleanup] Não foi possível remover diretório {dirpath}: {e}")

    return removed_count


class FileCleanupConfig:
    """Configuração de limpeza para um analisador específico."""

    def __init__(self):
        self.log_file_path: Optional[str] = None
        self.max_log_size: int = DEFAULT_MAX_LOG_SIZE
        self.max_log_rotations: int = DEFAULT_MAX_LOG_ROTATIONS
        self.max_file_age_days: int = DEFAULT_MAX_FILE_AGE_DAYS
        self.cleanup_interval: int = DEFAULT_CLEANUP_INTERVAL
        # Lista de diretórios para limpeza por idade
        self.cleanup_directories: list[str] = []


def start_cleanup_thread(config: FileCleanupConfig) -> Thread:
    """
    Inicia uma thread daemon que executa limpeza periódica de arquivos.

    Args:
        config: Configuração de limpeza (FileCleanupConfig)

    Returns:
        A Thread iniciada
    """

    def _cleanup_loop():
        logging.info(f"[Cleanup] Thread de limpeza iniciada. "
                     f"Intervalo: {config.cleanup_interval}s | "
                     f"Idade máxima: {config.max_file_age_days} dias | "
                     f"Tamanho máximo de log: {config.max_log_size / (1024*1024):.1f} MB")

        while True:
            try:
                # 1. Rotacionar log se necessário
                if config.log_file_path:
                    rotate_log_file(
                        config.log_file_path,
                        max_size=config.max_log_size,
                        max_rotations=config.max_log_rotations
                    )

                # 2. Limpar arquivos antigos em cada diretório configurado
                total_removed = 0
                for directory in config.cleanup_directories:
                    if os.path.isdir(directory):
                        removed = cleanup_old_files(directory, config.max_file_age_days)
                        total_removed += removed

                        # Remove diretórios vazios resultantes
                        cleanup_empty_directories(directory)

                if total_removed > 0:
                    logging.info(f"[Cleanup] {total_removed} arquivo(s) antigo(s) removido(s) nesta execução.")

            except Exception as e:
                logging.error(f"[Cleanup] Erro durante limpeza: {type(e).__name__}: {e}")

            time.sleep(config.cleanup_interval)

    thread = Thread(target=_cleanup_loop, daemon=True, name="file-cleanup")
    thread.start()
    return thread
