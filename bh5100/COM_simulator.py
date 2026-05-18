import serial

def enviar_hl7_serial(porta_com: str, mensagem_raw: str) -> None:
    try:
        # Configuração da porta (ajuste o baudrate conforme seu sistema)
        ser = serial.Serial(porta_com, 9600, timeout=1)
        
        # HL7 via Serial geralmente usa o protocolo MLLP (Minimal Lower Layer Protocol)
        # Início: <SB> (0x0b), Fim: <EB> (0x1c),  (0x0d)
        mllp_msg = b'\x0b' + mensagem_raw.encode('utf-8') + b'\x1c\x0d'
        
        print(f"Enviando para {porta_com}...")
        ser.write(mllp_msg)
        
        ser.close()
        print("Mensagem enviada com sucesso!")
    except Exception as e:
        print(f"Erro: {e}")

msg_exemplo = (
    "MSH|^~\\&|E-LAB|ES-480|||20260428120000||ORU^R01|1|P|2.3.1||||0||UNICODE||\r"
    "PID|1||123456||SILVA^MARIA||19850315|F||||||||||||||||||||||\r"
    "OBR|1|123456789012345|1001|E-LAB^ES-480||20260428110000|20260428120000|||||||Serum||||||||||||||||||||||\r"
    "OBX|1|NM|WBC^White Blood Cell||7.5|10^9/L|4.00-10.00|N|||F\r"
    "OBX|2|NM|LYM#^Lymphocyte Count||2.1|10^9/L|0.80-4.00|N|||F\r"
    "OBX|3|NM|MON#^Monocyte Count||0.54|10^9/L|0.10-0.80|N|||F\r"
    "OBX|4|NM|NEU#^Neutrophil Count||4.3|10^9/L|2.00-7.00|N|||F\r"
    "OBX|5|NM|EOS#^Eosinophil Count||0.3|10^9/L|0.00-0.50|N|||F\r"
    "OBX|6|NM|BASO#^Basophil Count||0.04|10^9/L|0.00-0.10|N|||F\r"
    "OBX|7|NM|LYM^Lymphocyte Percent||28.3|%|20.00-40.00|N|||F\r"
    "OBX|8|NM|MON^Monocyte Percent||7.2|%|3.00-8.00|N|||F\r"
    "OBX|9|NM|NEU^Neutrophil Percent||62.5|%|50.00-70.00|N|||F\r"
    "OBX|10|NM|EOS^Eosinophil Percent||1.5|%|0.50-5.00|N|||F\r"
    "OBX|11|NM|BASO^Basophil Percent||0.5|%|0.00-1.00|N|||F\r"
    "OBX|12|NM|RBC^Red Blood Cells||4.49|10^12/L|3.50-5.50|N|||F\r"
    "OBX|13|NM|HGB^Hemoglobin||135|g/L|115-155|N|||F\r"
    "OBX|14|NM|HCT^Hematocrit||42.1|%|37.0-50.0|N|||F\r"
    "OBX|15|NM|MCV^Mean Cell Volume||87.3|fL|80.0-100.0|N|||F\r"
    "OBX|16|NM|MCH^Mean Cell Hemoglobin||29.5|pg|27.0-31.0|N|||F\r"
    "OBX|17|NM|MCHC^Mean Cell HGB Conc||34.2|g/L|320-360|N|||F\r"
    "OBX|18|NM|RDW_CV^RDW-CV||12.9|%|11.5-14.5|N|||F\r"
    "OBX|19|NM|RDW_SD^RDW-SD||40.1|fL|35.0-56.0|N|||F\r"
    "OBX|20|NM|PLT^Platelets||250|10^9/L|150-400|N|||F\r"
    "OBX|21|NM|MPV^Mean Platelet Volume||10.1|fL|7.0-11.0|N|||F\r"
    "OBX|22|NM|PDW^Platelet Dist. Width||15.9|fL|15.0-17.0|N|||F\r"
    "OBX|23|NM|P-LCR^Platelet Large Cell Ratio||1.37|%|0.50-1.80|N|||F\r"
    "OBX|24|NM|PCT^Plateletcrit||0.41|%|0.10-0.28|H|||F\r"
    # Imagens (ED) – histogramas e scattergrams
    "OBX|25|ED|HISTO^RBCHistogram||ES-480^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD8AADAAAAAwAAAPwAAAP8AAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//wAA//8AAP//AAD//wAA\r"
    "OBX|26|ED|HISTO^PLTHistogram||ES-480^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//wAA//8AAP//AAD//wAA\r"
    "OBX|27|ED|SCATT^S0_S10DIFFScattergram||ES-480^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD8AADAAAD\r"
    "OBX|28|ED|SCATT^S90_S90DDIFFScattergram||ES-480^Image^BMP^Base64^Qk02AAAAAAAAAD4AAAAoAAAAEAAAABAAAAABAAEAAAAAACgAAAAAAAAAAAAAAAAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP\r"
)

if __name__ == "__main__":
    # Substitua 'COM1' pela porta de saída do seu par virtual
    enviar_hl7_serial('COM1', msg_exemplo)