import base64
import os

def reconstruir_imagens_hl7(mensagem_hl7, diretorio_saida="imagens_reconstruidas"):
    """
    Extrai e reconstrói imagens Base64 de uma mensagem HL7.
    
    Args:
        mensagem_hl7: String contendo a mensagem HL7 completa
        diretorio_saida: Diretório onde as imagens serão salvas
    """
    
    # Criar diretório de saída se não existir
    os.makedirs(diretorio_saida, exist_ok=True)
    
    # Encontrar todos os segmentos OBX que contêm imagens
    linhas = mensagem_hl7.split('\r')
    
    imagens_reconstruidas = []
    
    for linha in linhas:
        if 'OBX' in linha and 'Base64^' in linha:
            try:
                # Extrair campos do segmento OBX
                campos = linha.split('|')
                
                # OBX|número|ED|nome_imagem||dados_imagem
                nome_imagem = campos[3]  # Ex: RBCHistogram
                dados_imagem = campos[5]  # Ex: UT5160^Image^BMP^Base64^dados
                
                # Extrair apenas os dados Base64
                # Formato: UT5160^Image^BMP^Base64^[dados_base64]
                partes = dados_imagem.split('Base64^')
                if len(partes) > 1:
                    base64_data = partes[1]
                    
                    # Decodificar Base64 para bytes
                    try:
                        imagem_bytes = base64.b64decode(base64_data)
                        
                        # Salvar como arquivo BMP
                        nome_arquivo = f"{nome_imagem}.bmp"
                        caminho_completo = os.path.join(diretorio_saida, nome_arquivo)
                        
                        with open(caminho_completo, 'wb') as f:
                            f.write(imagem_bytes)
                        
                        imagens_reconstruidas.append({
                            'nome': nome_imagem,
                            'arquivo': nome_arquivo,
                            'tamanho_bytes': len(imagem_bytes)
                        })
                        
                        print(f"✓ Imagem reconstruída: {nome_arquivo} ({len(imagem_bytes)} bytes)")
                        
                    except Exception as e:
                        print(f"✗ Erro ao decodificar Base64 da imagem {nome_imagem}: {e}")
                        
            except Exception as e:
                print(f"✗ Erro ao processar linha: {e}")
    
    return imagens_reconstruidas


# Usar com a mensagem de exemplo corrigida
msg_exemplo = ("<SB>MSH|^~\&|URIT|UT-5160|LIS|PC|20260424121500||ORU^R01|0001|P|2.3.1||||UNICODE<CR>\r"
               "PID|1||123456||SILVA^MARIA||19850315|F|||||||||||||||||||<CR>\r"
               "PV1|1|I|ENF-301^01||||||||||||||||||||||||||||<CR>\r"
               "OBR|1||A1123145|URIT^UT-5160|||20260424120000|20260424120000|||||BIOQUIMICO|||Hipertensão||BLD||Inspector||Verificador|<CR>\r"
               "OBX|1|NM|WBC||7.5|10^9/L|4.00-10.00|N|||F<CR>\r"
               "OBX|2|NM|LYM#||2.1|10^9/L|0.80-4.00|N|||F<CR>\r"
               "OBX|3|NM|MON#||0.54|10^9/L|0.10-0.80|N|||F<CR>\r"
               "OBX|4|NM|NEU#||4.3|10^9/L|2.00-7.00|N|||F<CR>\r"
               "OBX|5|NM|EOS#||0.3|10^9/L|0.00-0.50|N|||F<CR>\r"
               "OBX|6|NM|BASO#||0.04|10^9/L|0.00-0.10|N|||F<CR>\r"
               "OBX|7|NM|LYM||28.3|%|20.00-40.00|N|||F<CR>\r"
               "OBX|8|NM|MON||7.2|%|3.00-8.00|N|||F<CR>\r"
               "OBX|9|NM|NEU||62.5|%|50.00-70.00|N|||F<CR>\r"
               "OBX|10|NM|EOS||1.5|%|0.50-5.00|N|||F<CR>\r"
               "OBX|11|NM|BASO||0.5|%|0.00-1.00|N|||F<CR>\r"
               "OBX|12|NM|RBC||4.49|10^12/L|3.50-5.50|N|||F<CR>\r"
               "OBX|13|NM|HGB||135|g/L|115-155|N|||F<CR>\r"
               "OBX|14|NM|HCT||42.1|%|37.0-50.0|N|||F<CR>\r"
               "OBX|15|NM|MCV||87.3|fL|80.0-100.0|N|||F<CR>\r"
               "OBX|16|NM|MCH||29.5|pg|27.0-31.0|N|||F<CR>\r"
               "OBX|17|NM|MCHC||34.2|g/L|320-360|N|||F<CR>\r"
               "OBX|18|NM|RDW_CV||12.9|%|11.5-14.5|N|||F<CR>\r"
               "OBX|19|NM|RDW_SD||40.1|fL|35.0-56.0|N|||F<CR>\r"
               "OBX|20|NM|PLT||250|10^9/L|150-400|N|||F<CR>\r"
               "OBX|21|NM|MPV||10.1|fL|7.0-11.0|N|||F<CR>\r"
               "OBX|22|NM|PDW||15.9|fL|15.0-17.0|N|||F<CR>\r"
               "OBX|23|NM|P-LCR||1.37|%|0.50-1.80|N|||F<CR>\r"
               "OBX|24|NM|PCT||0.41|%|0.10-0.28|H|||F<CR>\r"
               # As imagens com Base64 real começam aqui:
               "OBX|25|ED|RBCHistogram||UT5160^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD8AADAAAAAwAAAPwAAAP8AAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//wAA//8AAP//AAD//wAA<CR>\r"
               "OBX|26|ED|PLTHistogram||UT5160^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//wAA//8AAP//AAD//wAA<CR>\r"
               "OBX|27|ED|S0_S10DIFFScattergram||UT5160^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD<CR>\r"
               "OBX|28|ED|S90_S90DDIFFScattergram||UT5160^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP<CR>\r"
               "<EB><CR>\r")

# Reconstruir as imagens
print("=== Reconstruindo imagens das mensagens HL7 ===\n")
imagens = reconstruir_imagens_hl7(msg_exemplo, "imagens_hemograma")

print(f"\n=== Resumo ===")
print(f"Total de imagens reconstruídas: {len(imagens)}")
for img in imagens:
    print(f"  • {img['nome']} → {img['arquivo']} ({img['tamanho_bytes']} bytes)")