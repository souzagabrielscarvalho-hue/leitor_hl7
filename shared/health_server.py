"""
Servidor HTTP de health check para os analisadores HL7.

Fornece um endpoint GET /health que expõe métricas e status em JSON,
permitindo monitoramento remoto sem acessar o arquivo de log.

Uso:
    from shared.health_server import HealthServer

    health = HealthServer(port=8080, machine_name="MEK7300")
    health.start()

    # Atualizar métricas periodicamente:
    health.update_stats(
        port_open=True,
        bytes_received=12345,
        messages_processed=89,
        errors_consecutive=0,
        buffer_size=0,
        files_pending=3,
        files_sent_today=87,
        last_activity_seconds_ago=12,
        last_webhook_status=200,
        bidirectional_enabled=False,
    )

    # Parar:
    health.stop()
"""

import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional


class _HealthHandler(BaseHTTPRequestHandler):
    """Handler interno que serve o JSON de health check."""

    # Referência para o dicionário de stats (definido pelo HealthServer)
    stats_ref: Dict[str, Any] = {}
    machine_name: str = "Unknown"
    start_time: float = 0.0

    def do_GET(self) -> None:
        if self.path == "/health":
            stats = dict(self.stats_ref)  # cópia thread-safe
            stats["machine"] = self.machine_name
            stats["uptime_seconds"] = int(time.time() - self.start_time)

            body = json.dumps(stats, ensure_ascii=False, indent=2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format: str, *args: Any) -> None:
        """Suprime logs do servidor HTTP para não poluir o console."""
        pass


class HealthServer:
    """
    Servidor HTTP minimalista para health check.

    Args:
        port: Porta TCP para escutar (default 8080).
        machine_name: Nome do analisador (ex: "MEK7300").
    """

    def __init__(self, port: int = 8080, machine_name: str = "Unknown") -> None:
        self._port = port
        self._machine_name = machine_name
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stats: Dict[str, Any] = {
            "port_open": False,
            "com_port": "",
            "baud_rate": 0,
            "bytes_received": 0,
            "messages_processed": 0,
            "errors_consecutive": 0,
            "buffer_size": 0,
            "files_pending": 0,
            "files_sent_today": 0,
            "last_activity_seconds_ago": -1,
            "last_webhook_status": -1,
            "webhook_url": "",
            "bidirectional_enabled": False,
        }

    def update_stats(self, **kwargs: Any) -> None:
        """
        Atualiza uma ou mais métricas. Thread-safe.

        Exemplo:
            health.update_stats(port_open=True, bytes_received=12345)
        """
        with self._lock:
            for key, value in kwargs.items():
                if key in self._stats:
                    self._stats[key] = value

    def _get_stats_snapshot(self) -> Dict[str, Any]:
        """Retorna cópia thread-safe do dicionário de stats."""
        with self._lock:
            return dict(self._stats)

    def start(self) -> None:
        """Inicia o servidor HTTP em uma thread daemon."""
        # Injeta referências no handler
        _HealthHandler.stats_ref = self._stats  # type: ignore[attr-defined]
        _HealthHandler.machine_name = self._machine_name  # type: ignore[attr-defined]
        _HealthHandler.start_time = time.time()  # type: ignore[attr-defined]

        self._server = HTTPServer(("0.0.0.0", self._port), _HealthHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="health-server",
        )
        self._thread.start()

    def stop(self) -> None:
        """Para o servidor HTTP."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
