"""
Painel do leitor universal: janela própria em Qt (PySide6, Qt Widgets).

O painel não lê máquina nenhuma: cada máquina roda no seu próprio processo
("leitor.exe --maquina <id>"), com o mesmo comportamento do .exe da máquina
(log, gerados/, watchdog e popup de queda). O painel só configura, inicia, para
e mostra o estado (pelo GET /health de cada uma) e o log.

- nucleo.py: o que a janela mostra e faz, sem interface (testável sem Qt);
- janela.py e tema.py: a janela e o visual;
- config.py, processos.py, portas.py: config, processos e portas seriais.
"""
