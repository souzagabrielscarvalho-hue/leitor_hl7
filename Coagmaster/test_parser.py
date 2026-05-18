"""
Script de teste para validar o parsing do Coagmaster.
Execute: python test_parser.py
"""

import sys
import os

# Adiciona o diretório pai ao path para importar coagmaster
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coagmaster import split_exams_from_log, parse_coagmaster_exam
import json

# Caminho para o arquivo de log existente
LOG_FILE = os.path.join(os.path.dirname(__file__), '..', 'Coagmaster', 'coagmaster.log.txt')

def test_parser():
    print("=" * 60)
    print("TESTE DO PARSER DO COAGMASTER")
    print("=" * 60)
    
    # Lê o arquivo de log
    if not os.path.exists(LOG_FILE):
        print(f"ERRO: Arquivo não encontrado: {LOG_FILE}")
        return
    
    with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    print(f"\nConteúdo do arquivo ({len(content)} bytes):")
    print("-" * 40)
    print(content[:500] + "..." if len(content) > 500 else content)
    print("-" * 40)
    
    # Separa os exames
    print("\nSeparando exames...")
    exames = split_exams_from_log(content)
    print(f"Encontrados {len(exames)} exame(s)")
    
    # Parseia cada exame
    for i, exame_texto in enumerate(exames, 1):
        print(f"\n{'=' * 60}")
        print(f"EXAME {i}")
        print("=" * 60)
        print("\nTexto original:")
        print("-" * 40)
        print(exame_texto)
        print("-" * 40)
        
        print("\nResultado do parsing:")
        print("-" * 40)
        resultado = parse_coagmaster_exam(exame_texto)
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
        print("-" * 40)
        
        # Verifica campos obrigatórios
        campos_obrigatorios = ['ExamCode', 'Status']
        campos_faltando = [c for c in campos_obrigatorios if c not in resultado]
        if campos_faltando:
            print(f"⚠️ Campos obrigatórios faltando: {campos_faltando}")
        else:
            print("✓ Campos obrigatórios presentes")
    
    # Teste com exemplo bem-sucedido fornecido pelo usuário
    print("\n" + "=" * 60)
    print("TESTE COM EXEMPLO BEM-SUCEDIDO")
    print("=" * 60)
    
    exemplo_sucesso = """NOME DO LAB
(0001)
18/01/2018
CANAL 1
14:45:12
NOME: Joao Pedro
Exame: TP
Tempo de Protrombina
TEMPO: 16,6 s
RELAÇÃO: 1.25
%
81,4%
INR 1,28
CONTROLE 100%: 14,2s
ID(201801210001)
OPERADOR (CARLOS)"""
    
    print("\nTexto de entrada:")
    print("-" * 40)
    print(exemplo_sucesso)
    print("-" * 40)
    
    print("\nResultado do parsing:")
    print("-" * 40)
    resultado = parse_coagmaster_exam(exemplo_sucesso)
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
    print("-" * 40)
    
    # Verifica campos esperados
    campos_esperados = {
        'ExamNumber': '0001',
        'Date': '18/01/2018',
        'Channel': '1',
        'Time': '14:45:12',
        'PatientName': 'Joao Pedro',
        'ExamType': 'TP',
        'ExamDescription': 'Tempo de Protrombina',
        'TimeValue': '16,6 s',
        'Relation': '1.25',
        'Percentage': '81.4%',
        'INR': '1.28',
        'Control': '14,2s',
        'PatientID': '201801210001',
        'Operator': 'CARLOS',
        'Laboratory': 'NOME DO LAB',
        'ExamCode': 'COAG',
        'Status': 'SUCCESS'
    }
    
    erros = []
    for campo, valor_esperado in campos_esperados.items():
        valor_real = resultado.get(campo, '')
        if valor_real != valor_esperado:
            erros.append(f"  {campo}: esperado '{valor_esperado}', obtido '{valor_real}'")
    
    if erros:
        print("⚠️ Diferenças encontradas:")
        for erro in erros:
            print(erro)
    else:
        print("✓ Todos os campos extraídos corretamente!")


if __name__ == "__main__":
    test_parser()