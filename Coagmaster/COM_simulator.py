"""
Simulador de Porta COM para Coagmaster

Este script simula dados que viriam do equipamento Coagmaster real.
Útil para testar o coagmaster.py sem ter o equipamento físico.

Uso:
    python COM_simulator.py
    
Requer:
    pip install pyserial
"""

import serial
import time
import random
import logging
from datetime import datetime, timedelta

# Configurações
COM_PORT = 'COM10'  # Deve ser a mesma porta configurada no coagmaster.py
BAUD_RATE = 9600

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# ===== DADOS DE EXEMPLO =====
# Estes são exemplos reais de exames que o Coagmaster produz

EXAM_TEMPLATES = [
    # Exame 1: TP (Tempo de Protrombina) - Normal
    """VIDA EXAMES
(0001)
18/01/2026
CANAL 1
14:45:12
NOME: Joao Pedro da Silva
Exame: TP
Tempo de Protrombina
TEMPO: 16,6 s
RELAÇÃO: 1.25
%
81,4%
INR 1,28
CONTROLE 100%: 14,2s
ID(202601210001)
OPERADOR(CARLOS)

""" + ("*" * 50) + "\n\n",

    # Exame 2: TTPA (Tempo de Tromboplastina Parcial Ativado) - Normal
    """VIDA EXAMES
(0002)
18/01/2026
CANAL 2
14:50:30
NOME: Maria Santos Costa
Exame: TTPA
Tempo de Tromboplastina Parcial Ativado
TEMPO: 28,5 s
RELAÇÃO: 0.95
%
95,0%
CONTROLE 100%: 30,0s
ID(202601210002)
OPERADOR(CARLOS)

""" + ("*" * 50) + "\n\n",

    # Exame 3: Fibrinogênio - Normal
    """VIDA EXAMES
(0003)
18/01/2026
CANAL 3
14:55:45
NOME: Pedro Oliveira
Exame: FIB
Fibrinogênio
TEMPO: 18,2 s
CONCENTRAÇÃO: 298 mg/dL
%
98,5%
CONTROLE 100%: 18,5s
ID(202601210003)
OPERADOR(CARLOS)

""" + ("*" * 50) + "\n\n",

    # Exame 4: TP com Valor Anormal (elevado)
    """VIDA EXAMES
(0004)
18/01/2026
CANAL 1
15:05:20
NOME: Ana Beatriz Lima
Exame: TP
Tempo de Protrombina
TEMPO: 22,1 s
RELAÇÃO: 1.85
%
54,0%
INR 1,95
CONTROLE 100%: 12,0s
ID(202601210004)
OPERADOR(CARLOS)

""" + ("*" * 50) + "\n\n",

    # Exame 5: Exame com Falha (sem valores)
    """VIDA EXAMES
(0005)
CANAL 2
18/01/2026         15:10:00
N. SERIE(26031005)
OPERADOR(OPERADOR)
ID()
NOME: Roberto Mendes
EXAME:            TTPA
                  Tempo de Tromboplastina Parcial
TEMPO:     FALHOU!

""" + ("*" * 50) + "\n\n",

    # Exame 6: TP - Valor normal
    """VIDA EXAMES
(0006)
19/01/2026
CANAL 3
09:30:15
NOME: Lucia Ferreira
Exame: TP
Tempo de Protrombina
TEMPO: 15,2 s
RELAÇÃO: 1.14
%
88,0%
INR 1,22
CONTROLE 100%: 13,3s
ID(202601210006)
OPERADOR(MARIA)

""" + ("*" * 50) + "\n\n",

    # Exame 7: Fibrinogênio - Valor baixo
    """VIDA EXAMES
(0007)
19/01/2026
CANAL 1
10:15:45
NOME: Carlos Alberto
Exame: FIB
Fibrinogênio
TEMPO: 22,5 s
CONCENTRAÇÃO: 180 mg/dL
%
75,0%
CONTROLE 100%: 30,0s
ID(202601210007)
OPERADOR(MARIA)

""" + ("*" * 50) + "\n\n",
]


class CoagmasterSimulator:
    """Simulador de dados do Coagmaster"""
    
    def __init__(self, port: str, baudrate: int, verbose: bool = True):
        """
        Inicializa o simulador
        
        Args:
            port: Porta COM (ex: 'COM7')
            baudrate: Velocidade (padrão: 9600)
            verbose: Se True, mostra informações detalhadas
        """
        self.port = port
        self.baudrate = baudrate
        self.verbose = verbose
        self.serial = None
        self.connect()
    
    def connect(self):
        """Conecta à porta serial"""
        try:
            # Nota: No Windows, pode ser necessário usar uma biblioteca de serial virtual
            # Esta é uma abordagem de loopback para teste local
            self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
            if self.verbose:
                logging.info(f"✓ Conectado à porta {self.port} ({self.baudrate} baud)")
        except serial.SerialException as e:
            logging.error(f"✗ Erro ao conectar à porta {self.port}: {e}")
            logging.info("  DICA: Use 'com0com' ou 'null-modem-emulator' para criar um par de portas virtuais")
            self.serial = None
    
    def send_exam(self, exam_data: str, delay: float = 0.5):
        """
        Envia um exame pela porta serial
        
        Args:
            exam_data: Texto do exame
            delay: Delay entre linhas (em segundos)
        """
        if not self.serial or not self.serial.is_open:
            logging.error("Porta serial não está aberta!")
            return False
        
        try:
            # Envia linha por linha para simular um fluxo real
            lines = exam_data.split('\n')
            for line in lines:
                if line or line == '':  # Preserva linhas vazias
                    self.serial.write((line + '\n').encode('utf-8', errors='ignore'))
                    time.sleep(delay)
            
            if self.verbose:
                logging.info(f"✓ Exame enviado ({len(exam_data)} bytes)")
            return True
        
        except serial.SerialException as e:
            logging.error(f"✗ Erro ao enviar dados: {e}")
            return False
        except Exception as e:
            logging.error(f"✗ Erro inesperado: {e}")
            return False
    
    def send_random_exam(self, delay: float = 0.5):
        """Envia um exame aleatório"""
        exam = random.choice(EXAM_TEMPLATES)
        self.send_exam(exam, delay)
    
    def send_all_exams(self, delay_between_exams: float = 5.0, delay_between_lines: float = 0.1):
        """
        Envia todos os exames de teste em sequência
        
        Args:
            delay_between_exams: Delay entre exames (em segundos)
            delay_between_lines: Delay entre linhas (em segundos)
        """
        for i, exam in enumerate(EXAM_TEMPLATES, 1):
            logging.info(f"Enviando exame {i}/{len(EXAM_TEMPLATES)}...")
            self.send_exam(exam, delay_between_lines)
            if i < len(EXAM_TEMPLATES):
                logging.info(f"  Aguardando {delay_between_exams}s antes do próximo exame...")
                time.sleep(delay_between_exams)
    
    def send_putty_header(self):
        """Envia um cabeçalho PuTTY para simular nova sessão"""
        header = f"\n=~=~=~=~=~=~=~=~=~=~=~= PuTTY log {datetime.now().strftime('%Y.%m.%d %H:%M:%S')} =~=~=~=~=~=~=~=~=~=~=~=\n"
        if self.serial and self.serial.is_open:
            self.serial.write(header.encode('utf-8'))
            logging.info("Cabeçalho PuTTY enviado")
    
    def close(self):
        """Fecha a conexão"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logging.info("Porta serial fechada")


def main():
    """Menu interativo do simulador"""
    
    logging.info("=" * 60)
    logging.info("SIMULADOR DE PORTA COM - COAGMASTER")
    logging.info("=" * 60)
    logging.info(f"Porta: {COM_PORT} | Baud Rate: {BAUD_RATE}")
    logging.info("")
    logging.info("IMPORTANTE:")
    logging.info("  Este simulador precisa de um par de portas COM virtuais.")
    logging.info("  No Windows, use uma destas ferramentas:")
    logging.info("  • com0com (http://com0com.sourceforge.net)")
    logging.info("  • null-modem-emulator (VSPE)")
    logging.info("  • socat (WSL2)")
    logging.info("")
    logging.info(f"  Configure as portas para: COM4 ↔ {COM_PORT}")
    logging.info("  Este script escreve em COM4, coagmaster.py lê em COM7")
    logging.info("=" * 60)
    logging.info("")
    
    # Usa COM4 para escrever (simulador) e deixa COM7 para leitura (coagmaster.py)
    sim = CoagmasterSimulator('COM8', BAUD_RATE, verbose=True)
    
    if not sim.serial:
        logging.error("Não foi possível inicializar o simulador!")
        return
    
    try:
        while True:
            print("\n" + "=" * 60)
            print("MENU DE TESTE")
            print("=" * 60)
            print("1. Enviar um exame aleatório")
            print("2. Enviar todos os 7 exames de teste")
            print("3. Enviar exame TP normal")
            print("4. Enviar exame com falha")
            print("5. Enviar cabeçalho PuTTY (nova sessão)")
            print("6. Enviar múltiplos exames aleatórios")
            print("0. Sair")
            print("=" * 60)
            
            escolha = input("Digite sua escolha (0-6): ").strip()
            
            if escolha == '1':
                logging.info("Enviando exame aleatório...")
                sim.send_random_exam()
            
            elif escolha == '2':
                logging.info("Enviando todos os 7 exames de teste...")
                sim.send_all_exams(delay_between_exams=3.0, delay_between_lines=0.05)
                logging.info("✓ Todos os exames foram enviados!")
            
            elif escolha == '3':
                logging.info("Enviando exame TP normal...")
                sim.send_exam(EXAM_TEMPLATES[0])
            
            elif escolha == '4':
                logging.info("Enviando exame com falha...")
                sim.send_exam(EXAM_TEMPLATES[4])
            
            elif escolha == '5':
                sim.send_putty_header()
            
            elif escolha == '6':
                qtd = input("Quantos exames aleatórios? (1-10): ").strip()
                try:
                    qtd = int(qtd)
                    if 1 <= qtd <= 10:
                        for i in range(qtd):
                            logging.info(f"Enviando exame {i+1}/{qtd}...")
                            sim.send_random_exam()
                            if i < qtd - 1:
                                time.sleep(2)
                    else:
                        logging.error("Digite um número entre 1 e 10")
                except ValueError:
                    logging.error("Número inválido")
            
            elif escolha == '0':
                logging.info("Encerrando simulador...")
                break
            
            else:
                logging.error("Opção inválida!")
    
    except KeyboardInterrupt:
        logging.info("\nInterrompido pelo usuário")
    
    finally:
        sim.close()
        logging.info("Simulador encerrado")


if __name__ == "__main__":
    main()
