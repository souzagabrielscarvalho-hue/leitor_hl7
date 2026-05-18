from PIL import Image, ImageFile
import os

# Permitir imagens truncadas
ImageFile.LOAD_TRUNCATED_IMAGES = True

pasta_imagens = r"C:\Users\Gabriel\Desktop\AnalisadorBH5100\enviados\imagens"
convertidos = 0
erros = 0

for arquivo in os.listdir(pasta_imagens):
    if arquivo.endswith('.bmp'):
        caminho = os.path.join(pasta_imagens, arquivo)
        try:
            img = Image.open(caminho)
            img.load()  # Força carregar a imagem
            novo_caminho = caminho.replace('.bmp', '.png')
            img.save(novo_caminho)
            print(f"✓ Convertido: {arquivo} ({os.path.getsize(caminho)} bytes)")
            convertidos += 1
        except Exception as e:
            print(f"✗ Erro em {arquivo}: {e}")
            erros += 1

print(f"\nTotal: {convertidos} convertidos, {erros} erros")