# Como Verificar Problemas nas Integrações — MEK7300, BH5100, Coagmaster, VIDAS 1600 e PKL125

## 1. Verificando na plataforma (Atividade de Integração)

Acessando **"Atividade de Integração"** na franquia, é possível ver os logs das integrações. O erro mais comum é:

> **`Procedure with tag_identifier: not found`**

Isso indica que a etiqueta do exame não foi encontrada no sistema. Normalmente ocorre quando:
- A unidade realizou um **exame de teste** (controle, calibração etc.)
- A etiqueta foi **digitada incorretamente**

**Como identificar exames de teste:** se os campos do exame apresentam caracteres `?` ou valores muito diferentes dos habituais (ex.: WBC com valor `0`, resultados com sufixos estranhos), é muito provável que seja um exame de teste ou controle de qualidade da máquina, e não um exame de paciente.

---

## 2. Quando a integração não envia nenhum exame

Caso a unidade relate que o **interfaceamento não está ocorrendo** e a aba **"Atividade de Integração"** não mostre nenhum exame, o problema está no **computador conectado à máquina**. Siga os passos abaixo:

### 2.1. Verificar se o .exe está em execução

No computador conectado à máquina, abra o **Gerenciador de Tarefas** (`Ctrl+Shift+Esc`) e verifique se o processo do analisador está rodando:

| Analisador | Processo (.exe) | Porta Health Check |
|---|---|---|
| **BH5100** | `bh5100.exe` | `http://localhost:8080/health` |
| **Coagmaster** | `coagmaster.exe` | `http://localhost:8081/health` |
| **MEK7300** | `mek7300.exe` | `http://localhost:8082/health` |
| **PKL125** | `pkl.exe` | `http://localhost:8083/health` |
| **VIDAS 1600** | `vidas1600.exe` | `http://localhost:8084/health` |

Se o processo **não estiver rodando**, reinicie-o (veja seção 2.4).

### 2.2. Verificar o Health Check

Abra o navegador no próprio computador e acesse a URL de health check da máquina correspondente (tabela acima). O retorno deve ser um JSON com status como:

```json
{
  "machine": "BH5100",
  "com_port": "COM3",
  "baud_rate": 9600,
  "port_open": true,
  "bytes_received": 12345,
  "messages_processed": 89,
  "errors_consecutive": 0,
  "uptime_seconds": 3600
}
```

**Campos importantes:**
- **`port_open`**: se `false`, a porta serial não está conectada — verifique o cabo USB/serial e a porta COM.
- **`errors_consecutive`**: se for maior que zero, há erros consecutivos no envio ao webhook.
- **`messages_processed`**: se for `0` e o sistema está há muito tempo no ar, a máquina não está enviando dados.

### 2.3. Verificar os arquivos e logs do interfaceamento

Na **Área de Trabalho** do computador, haverá uma pasta chamada **`Analisador [Nome da Máquina]`** (ex.: `Analisador BH5100`, `Analisador MEK7300`). Dentro dessa pasta existem:

| Subpasta | Conteúdo |
|---|---|
| `gerados/` | Arquivos de exames recebidos da máquina, aguardando envio ao webhook |
| `enviados/` | Arquivos enviados com sucesso ao webhook |
| `requisições não enviadas/` | Arquivos que falharam após todas as tentativas de reenvio |
| `logs/` | Rotações de log antigas |
| `analisador_[nome].log` | Log principal do serviço |

**Diagnóstico rápido pelas pastas:**
- Se `gerados/` **está cheio** e `enviados/` está vazio → o serviço não está conseguindo enviar ao webhook (problema de rede ou webhook indisponível).
- Se `gerados/` está vazio e `requisições não enviadas/` está vazio → a máquina não está enviando dados pela porta serial (problema de cabo, porta COM ou configuração).
- Se `requisições não enviadas/` tem arquivos → houve falha de envio. Verifique a conexão com a internet e o log para detalhes.

### 2.4. Localizar e reiniciar o executável

O `.exe` e seu arquivo JSON de configuração ficam na pasta de inicialização do Windows:

```
C:\Users\<usuário>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
```

**Acesso rápido:** pressione `Win+R` e digite `shell:startup`.

Nessa pasta você encontrará:
- O executável do analisador (ex.: `bh5100.exe`, `mek7300.exe`)
- O arquivo `config_<máquina>.json` com as configurações

**Para reiniciar o serviço:**
1. Feche o processo pelo Gerenciador de Tarefas
2. Execute o `.exe` novamente a partir da pasta de Startup

### 2.5. Verificar a configuração da porta COM

O arquivo `config_<máquina>.json` contém a configuração da porta serial. Exemplo:

```json
{
    "com_port": "COM3",
    "baud_rate": 9600,
    "franchise_credential_id": "f47d9a16-...",
    "webhook_url": "https://apoio.internal.vidaexame.com/api/integration/bh5100?franchise_credential_id=..."
}
```

**Verifique:**
- A `com_port` corresponde à porta onde o cabo serial/USB está conectado fisicamente
- No Windows, confira no **Gerenciador de Dispositivos** (`Painel de Controle → Gerenciador de Dispositivos → Portas (COM e LPT)`) qual porta COM está atribuída ao conversor USB-RS232
- Se a máquina foi conectada em outra porta USB, a porta COM pode ter mudado — atualize o JSON e reinicie o serviço

**Portas padrão por analisador:**

| Analisador | Porta COM Padrão | Baud Rate |
|---|---|---|
| **BH5100** | COM3 | 9600 |
| **Coagmaster** | COM4 | 115200 |
| **MEK7300** | COM5 | 9600 |
| **PKL125** | COM3 | 19200 |
| **VIDAS 1600** | COM5 | 9600 |

> ⚠️ **Atenção:** A porta COM padrão é apenas um valor inicial. A porta real depende de onde o cabo foi conectado. Sempre confira no Gerenciador de Dispositivos.

---

## 3. Problemas comuns e soluções

| Problema | Causa provável | Solução |
|---|---|---|
| `Procedure with tag_identifier: not found` | Exame de teste ou etiqueta incorreta | Verifique se os campos contêm `?` ou valores atípicos; se sim, é exame de teste — pode ser ignorado |
| Nenhum exame chega na plataforma | .exe não está rodando | Reinicie o executável pela pasta de Startup |
| `port_open: false` no health check | Cabo desconectado ou porta COM errada | Verifique o cabo e confira a porta no Gerenciador de Dispositivos e no `config_*.json` |
| Arquivos acumulados em `gerados/` | Webhook inacessível (sem internet ou servidor fora) | Verifique a conexão de internet; acesse a URL do webhook no navegador para testar |
| Arquivos em `requisições não enviadas/` | Falha definitiva de envio (5 tentativas esgotadas) | Verifique o log para o erro específico; corrija e reinicie o serviço |
| Erro `ClearCommError` no log | Problema transitório no driver serial do Windows | O sistema tenta recuperar automaticamente; se persistir, reconecte o cabo |
| Erro 401/403 no webhook | `franchise_credential_id` inválido | Verifique o `config_*.json` e confirme o credential ID com o suporte |
| Log com "NÃO FOI POSSÍVEL CONECTAR à porta COM" | Porta COM inexistente ou em uso por outro programa | Confira a porta no Gerenciador de Dispositivos; feche outros programas que usem a porta serial |

---

## 4. Checklist de verificação rápida

1. ☐ O `.exe` do analisador está rodando? (Gerenciador de Tarefas)
2. ☐ O health check responde? (`http://localhost:80XX/health`)
3. ☐ `port_open` está `true` no health check?
4. ☐ A pasta `gerados/` tem arquivos novos? (Se sim, o problema é no envio)
5. ☐ A pasta `gerados/` está vazia há muito tempo? (Se sim, o problema é na comunicação serial)
6. ☐ O cabo serial/USB está conectado firmemente?
7. ☐ A porta COM no `config_*.json` corresponde à porta no Gerenciador de Dispositivos?
8. ☐ O computador tem acesso à internet? (Teste acessando a URL do webhook no navegador)
9. ☐ O log (`analisador_*.log`) mostra erros recentes?