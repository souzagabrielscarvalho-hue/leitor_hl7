"""
Envio das imagens de exame para o S3 via URLs pré-assinadas do lab-apoio.

Fluxo (o mesmo do Univida, adaptado para o programa da máquina):
  1. POST /integration/images/initiate  -> URL de PUT por imagem
  2. PUT direto no S3 com os bytes
  3. POST /integration/images/confirm   -> completed/missing/failed/not_found

A máquina não recebe credencial da AWS: só o token do lab-apoio.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

MAX_IMAGES_PER_REQUEST = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024
INITIATE_TIMEOUT = 30
PUT_TIMEOUT = 60
CONFIRM_TIMEOUT = 30
DEFAULT_EXAM_CODE = "HEMO"


def _nome_normalizado(nome_obx3: str) -> str:
    """Minúsculo e sem separadores, para comparar nomes de imagem."""
    return re.sub(r'[^a-z0-9]', '', (nome_obx3 or '').lower())


def codigo_imagem(nome_obx3: str) -> Optional[str]:
    """
    Traduz o OBX-3 das imagens do BH5100 para o código mostrado na tela/laudo.

    Exemplos reais: HISTO^RBCHistogram, HISTO^PLTHistogram,
    SCATT^S0_S10DIFFScattergram, SCATT^S90_S90DDIFFScattergram.

    O BH5100 também manda os nomes curtos WBCHisto, RBCHisto, PLTHisto,
    S0Histo e S0_S90Scattergram. Os dois formatos precisam ser aceitos: o
    mesmo equipamento muda o OBX-3 conforme a versão/firmware.

    O WBC pode vir no histograma e no scattergram de diferencial. Se vier
    nos dois, o histograma tem preferência (ver _prioridade_imagem).
    """
    nome = _nome_normalizado(nome_obx3)

    # Nomes curtos e longos (wbchisto cobre wbchistogram).
    if 'wbchisto' in nome:
        return "WBC"
    if 'rbchisto' in nome:
        return "RBC"
    if 'plthisto' in nome:
        return "PLT"
    if 's0histo' in nome:
        return "WBC"
    if 's0s10' in nome:
        return "WBC"
    if 's90s90' in nome:
        return "WBC90"
    if 's0s90' in nome:
        return "WBC90"

    return None


def _prioridade_imagem(imagem: Dict[str, Any]) -> int:
    """
    Quando a máquina manda mais de uma imagem para o mesmo código, a de menor
    prioridade vence; as outras são descartadas como duplicadas em _preparar.

    WBC:   WBCHisto (0) > S0_S10DIFFScattergram (1) > S0Histo (2)
    WBC90: S90_S90DDIFFScattergram (0) > S0_S90Scattergram (1)
    """
    nome = _nome_normalizado(str(imagem.get("nome", "")))

    if 'wbchisto' in nome:
        return 0
    if 's90s90' in nome:
        return 0
    if 's0s10' in nome:
        return 1
    if 's0s90' in nome:
        return 1
    if 's0histo' in nome:
        return 2

    return 3



def converter_para_png(caminho: str) -> Optional[str]:
    """Converte BMP (formato do BH5100) para PNG; devolve o caminho do PNG."""
    extensao = os.path.splitext(caminho)[1].lower()

    if extensao == ".png":
        return caminho
    if extensao in (".jpg", ".jpeg"):
        return caminho
    if extensao != ".bmp":
        return None

    try:
        from PIL import Image, ImageFile

        # O equipamento às vezes manda BMP truncado; o PIL consegue abrir.
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        destino = os.path.splitext(caminho)[0] + ".png"
        with Image.open(caminho) as imagem:
            imagem.load()
            imagem.save(destino, "PNG")

        return destino
    except Exception as erro:
        logging.warning(f"Erro ao converter imagem BMP para PNG ({caminho}): {erro}")
        return None


def mime_type_do_arquivo(caminho: str) -> str:
    extensao = os.path.splitext(caminho)[1].lower()
    return "image/jpeg" if extensao in (".jpg", ".jpeg") else "image/png"


class ExamImageUploader:
    """Cliente das rotas de imagem do lab-apoio."""

    def __init__(
        self,
        api_url: str,
        token: str,
        franchise_credential_id: str,
        machine: str = "bh5100",
    ) -> None:
        self.api_url = (api_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.franchise_credential_id = franchise_credential_id or ""
        self.machine = machine
        self.session = requests.Session()

    def enabled(self) -> bool:
        return bool(self.api_url and self.token and self.franchise_credential_id)

    def send(
        self,
        barcode: str,
        exam_code: str,
        imagens: List[Dict[str, Any]],
    ) -> None:
        """
        Envia as imagens extraídas do HL7. Nunca levanta exceção: imagem é
        complemento do resultado e não pode impedir o envio do hemograma.
        """
        if not self.enabled():
            logging.info(
                "Envio de imagens desligado: configure exam_images_url e "
                "exam_images_machine_token no config_bh5100.json."
            )
            return

        preparadas = self._preparar(imagens)

        if not preparadas:
            return

        for inicio in range(0, len(preparadas), MAX_IMAGES_PER_REQUEST):
            lote = preparadas[inicio:inicio + MAX_IMAGES_PER_REQUEST]
            self._enviar_lote(barcode, exam_code or DEFAULT_EXAM_CODE, lote)

    def _preparar(self, imagens: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        preparadas: List[Dict[str, str]] = []
        codigos_usados = set()

        for imagem in sorted(imagens or [], key=_prioridade_imagem):
            codigo = codigo_imagem(str(imagem.get("nome", "")))

            if not codigo:
                logging.warning(
                    f"Imagem sem mapeamento de código ignorada: {imagem.get('nome')}"
                )
                continue

            if codigo in codigos_usados:
                logging.warning(f"Imagem duplicada para o código {codigo} ignorada.")
                continue

            caminho = converter_para_png(str(imagem.get("arquivo", "")))

            if not caminho or not os.path.isfile(caminho):
                logging.warning(f"Imagem {codigo} não encontrada/convertida: {caminho}")
                continue

            tamanho = os.path.getsize(caminho)

            if tamanho <= 0:
                logging.warning(f"Imagem {codigo} vazia; ignorada.")
                continue

            if tamanho > MAX_IMAGE_BYTES:
                logging.warning(
                    f"Imagem {codigo} com {tamanho} bytes passou do limite de "
                    f"{MAX_IMAGE_BYTES}; ignorada."
                )
                continue

            codigos_usados.add(codigo)
            preparadas.append({
                "code": codigo,
                "caminho": caminho,
                "mime_type": mime_type_do_arquivo(caminho),
            })

        return preparadas

    def _enviar_lote(
        self,
        barcode: str,
        exam_code: str,
        lote: List[Dict[str, str]],
    ) -> None:
        payload = {
            "franchise_credential_id": self.franchise_credential_id,
            "machine": self.machine,
            "FileName": barcode,
            "ExamCode": exam_code,
            "images": [
                {"code": item["code"], "mime_type": item["mime_type"]}
                for item in lote
            ],
        }

        resposta = self._post("/initiate", payload, INITIATE_TIMEOUT)

        if resposta is None:
            return

        try:
            imagens = (resposta.json().get("data") or {}).get("images") or []
        except ValueError:
            logging.error("Resposta inválida do initiate de imagens.")
            return

        por_codigo = {str(item.get("code")): item for item in imagens}
        enviadas: List[str] = []

        for item in lote:
            info = por_codigo.get(item["code"])

            if not info:
                logging.warning(f"O lab-apoio não devolveu URL para a imagem {item['code']}.")
                continue

            if self._put(info, item["caminho"], item["code"]):
                enviadas.append(str(info.get("image_id")))

        if not enviadas:
            logging.warning(f"Nenhuma imagem de {barcode} chegou ao S3.")
            return

        resultado = self._confirm(enviadas)

        if resultado is None:
            return

        # Uma segunda chance para o PUT que não chegou (URL ainda dentro dos 15 min).
        faltando = [i for i in resultado.get("missing", []) if i in enviadas]

        if faltando:
            for image_id in faltando:
                info = next(
                    (i for i in imagens if str(i.get("image_id")) == image_id),
                    None,
                )
                item = next(
                    (i for i in lote if info and i["code"] == str(info.get("code"))),
                    None,
                )

                if info and item:
                    self._put(info, item["caminho"], item["code"])

            resultado = self._confirm(enviadas)

            if resultado is None:
                return

        self._log_resultado(barcode, resultado)

    def _put(self, info: Dict[str, Any], caminho: str, codigo: str) -> bool:
        url = info.get("upload_url")
        headers = info.get("headers") or {}

        if not url:
            logging.error(f"URL de upload ausente para a imagem {codigo}.")
            return False

        try:
            with open(caminho, "rb") as arquivo:
                resposta = self.session.put(
                    url,
                    data=arquivo,
                    headers=headers,
                    timeout=PUT_TIMEOUT,
                )
        except requests.RequestException as erro:
            logging.error(f"Falha no PUT da imagem {codigo}: {type(erro).__name__}: {erro}")
            return False

        if 200 <= resposta.status_code < 300:
            logging.info(f"Imagem {codigo} enviada ao S3 ({resposta.status_code}).")
            return True

        logging.error(
            f"PUT da imagem {codigo} recusado: {resposta.status_code}. "
            f"URL vencida/alterada? Recomece pelo initiate."
        )
        return False

    def _post(
        self,
        caminho: str,
        payload: Dict[str, Any],
        timeout: int,
    ) -> Optional[requests.Response]:
        headers = {
            "X-Machine-Token": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            resposta = self.session.post(
                self.api_url + caminho,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as erro:
            logging.error(
                f"Falha ao chamar {caminho} de imagens: "
                f"{type(erro).__name__}: {erro}"
            )
            return None

        if resposta.status_code in (200, 201):
            return resposta

        if resposta.status_code == 401:
            logging.critical(
                "Token da máquina inválido no lab-apoio (X-Machine-Token). "
                "Confira exam_images_machine_token."
            )
        elif resposta.status_code == 503:
            logging.critical(
                "Envio de imagens desligado no lab-apoio "
                "(nenhuma máquina cadastrada)."
            )
        else:
            logging.error(
                f"{caminho} de imagens retornou {resposta.status_code}: "
                f"{resposta.text[:300]}"
            )

        return None

    def _confirm(self, image_ids: List[str]) -> Optional[Dict[str, Any]]:
        resposta = self._post(
            "/confirm",
            {
                "franchise_credential_id": self.franchise_credential_id,
                "image_ids": image_ids,
            },
            CONFIRM_TIMEOUT,
        )

        if resposta is None:
            return None

        try:
            return resposta.json().get("data") or {}
        except ValueError:
            logging.error("Resposta inválida do confirm de imagens.")
            return None

    def _log_resultado(self, barcode: str, resultado: Dict[str, Any]) -> None:
        logging.info(
            f"Imagens de {barcode}: "
            f"completed={resultado.get('completed', [])} "
            f"missing={resultado.get('missing', [])} "
            f"failed={resultado.get('failed', [])} "
            f"not_found={resultado.get('not_found', [])}"
        )

        if resultado.get("failed"):
            logging.warning(
                f"Imagens de {barcode} acima de 5 MB foram apagadas pelo lab-apoio; "
                f"reduza a imagem e reenvie."
            )

        if resultado.get("missing"):
            logging.warning(
                f"PUT de imagens de {barcode} não chegou ao S3; "
                f"a URL pode ter vencido."
            )
