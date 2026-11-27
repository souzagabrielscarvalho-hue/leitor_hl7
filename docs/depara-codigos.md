# De-para de códigos de exame (todas as máquinas)

## O problema

Cada máquina identifica um exame por um **código de item** próprio, que nem
sempre é igual ao código que o Vida usa. O caso que motivou isto é a CA-150: os
itens de bioquímica da URIT são de **"Sistema Fechado"** e, pelo manual
operacional (seção 4.3.1), o código deles **pode ser visto, mas não alterado
nem excluído**. A máquina chama o HDL de `HDLE`, a glicose de `GLIE`, e assim
por diante.

O Vida grava o resultado casando o código que vem da máquina com o
**`attribute_code`** do atributo com origem *Interface*. Ou seja, espera `HDL`,
não `HDLE`. Como os códigos diferem e o da máquina não pode ser alterado, a
integração quebra **nos dois sentidos**:

1. **Consulta (o que a máquina busca):** o leitor consulta o Vida, recebe `HDL`
   e precisa mandar o código que a máquina entende (`HDLE`) no DSR. Senão a
   máquina **não reconhece** o exame.
2. **Envio (o que a máquina manda):** a máquina devolve o resultado com o código
   dela (`HDLE`). O Vida **não grava**, porque `HDLE` não bate com nenhum
   atributo (`HDL`).

## A solução: `depara_codigos` no config (na base, para todas)

O de-para fica na **base** (`shared/base_analisador.py`), então **qualquer
máquina** pode usar. A tabela fica na chave **`depara_codigos`** do
`config_<maquina>.json`, no formato **`{"código da máquina": "código do Vida"}`**:

```json
"depara_codigos": {
    "HDLE": "HDL",
    "GLIE": "GLI"
}
```

- **Envio (todas as máquinas):** antes de mandar o resultado ao lab-apoio, a
  base troca o código da máquina pelo do Vida.
- **Consulta (só quem busca por etiqueta — CA-150 e URIT-8021):** usa a tabela
  ao contrário, traduzindo o código do Vida para o da máquina antes do DSR.
- Códigos iguais nos dois lados (ex.: `URE`, `CRE`) **não precisam entrar**.

## O que verificar em cada máquina (formato `{"code": ...}`)

O de-para no **envio** age sobre a lista `results` do payload, trocando o código
do atributo. Mas **o formato do payload muda de máquina para máquina** — e é isso
que precisa ser verificado antes de confiar no de-para em cada uma:

| Máquina | Formato do payload | Onde fica o código do atributo | De-para no envio |
| --- | --- | --- | --- |
| CA-150 | `results: [{code, value, unit}]` | **`code`** | ✅ cobre |
| URIT-8021 | `results: [{code, value, unit}]` | **`code`** | ✅ cobre |
| PKL | `results: [{exam_code, test, value}]` | **`test`** (o `exam_code` é o exame) | ✅ cobre (`test`) |
| BH5100 | `{FileName, ExamCode, **campos}` | chaves de `campos` (WBC, RBC, HGB…) | ⚠️ não age |
| MEK7300 | `{FileName, ExamCode, **campos}` | chaves de `campos` | ⚠️ não age |
| VIDAS1600 | `{FileName, ExamCode, **exam_tests}` | chaves de `exam_tests` | ⚠️ não age |

**O que verificar:**

- **CA-150 e URIT-8021** (bioquímica): usam `results[*].code`. O de-para cobre os
  dois sentidos. São as máquinas que realmente têm o problema de código fechado.
- **PKL** (coagulação, ASTM): o código do atributo é o campo **`test`**, não
  `code`. A base também traduz `test`, então o de-para funciona — mas confirme,
  ao configurar, que você está mapeando o valor de `test` (não o `exam_code`).
- **BH5100, MEK7300, VIDAS1600** (hemograma): o payload é **plano**
  (`ExamCode` + um campo por parâmetro), não tem `results` com código por item.
  O de-para atual **não age** nessas máquinas. Na prática os códigos de hemograma
  (WBC, RBC, HGB…) costumam já bater com o Vida; se um dia precisarem de
  tradução, será preciso **estender** a base para remapear as chaves do payload
  (com cuidado para não tocar chaves reservadas como `FileName`/`ExamCode`).
- Os códigos de **exame** (`exam_code`, `ExamCode`) **nunca** são tocados — o
  de-para é só para o código do **atributo**.

## Como editar (por unidade)

1. Abra o `config_<maquina>.json` ao lado do `.exe` (se a chave não existir, o
   programa a cria vazia na primeira execução).
2. Em `depara_codigos`, adicione uma linha por exame cujo código **difere** entre
   a máquina e o Vida.
3. Salve e **reinicie o `.exe`** (a config é lida na abertura).

Para descobrir o código da máquina: no painel dela (na CA-150: Reagentes → Item
Normal) ou imprimindo o relatório de parâmetro do item.

## Regras e cuidados

- Mapeamento **1:1** (um código da máquina por código do Vida). Dois códigos de
  máquina para o mesmo código do Vida deixam a consulta ambígua — o log avisa.
- Comparação **exata** (inclusive maiúsculas/minúsculas): escreva os códigos como
  aparecem na máquina (OBX.4) e no Vida (`attribute_code`).
- Na abertura, o log mostra o de-para ativo; a cada resultado traduzido, uma
  linha mostra `HDLE→HDL`.
- É mudança de código do leitor: versão **7.3.0** (`shared/versao.py` +
  `CHANGELOG.md`); os `.exe` afetados precisam ser **recompilados**.

## De-para x clonar o item (CA-150)

- **De-para:** mantém reagentes e itens da URIT como estão; só traduz o código no
  leitor. Mais leve, centralizado no config, zero alteração na máquina.
- **Clonar o item (Sistema Aberto):** necessário só se a unidade for **trocar o
  reagente** (ex.: para Ebram), porque aí os parâmetros do exame mudam.
