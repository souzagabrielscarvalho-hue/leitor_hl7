# =============================================================================
# build_all.ps1 - Compila todos os .exe e prepara para deploy
# =============================================================================
# Uso:
#   .\build_all.ps1            compila tudo e, sem nenhum erro, faz o commit
#   .\build_all.ps1 -SkipGit   so compila, sem mexer no git
#
# O envio ao GitHub e separado, um comando por repositorio (ver enviar.ps1).
# Sai com codigo 1 se algo falhar; nesse caso nada e commitado.
#
# Mantenha este arquivo somente em ASCII: o PowerShell 5.1 le script UTF-8 sem
# BOM como ANSI e corrompe os acentos.
# =============================================================================

param([switch]$SkipGit)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $ProjectRoot "build_logs"
$Resultados = New-Object System.Collections.ArrayList

# Token preenchido em arquivo versionado. O padrao nao usa aspas porque o
# PowerShell 5.1 nao as repassa corretamente para programas nativos.
$PadraoToken = 'exam_images_machine_token.{1,6}[A-Za-z0-9]{20,}'

# Nome da pasta do spec, nome do spec e nome do exe.
$Maquinas = @(
    @{ Nome = "bh5100";     SpecDir = "bh5100" },
    @{ Nome = "coagmaster"; SpecDir = "Coagmaster" },
    @{ Nome = "mek7300";    SpecDir = "mek7300" },
    @{ Nome = "pkl";        SpecDir = "pkl" },
    @{ Nome = "vidas1600";  SpecDir = "vidas1600" }
)

function Abortar {
    param([string]$Mensagem)
    Write-Host ""
    Write-Host "BUILD INTERROMPIDO: $Mensagem" -ForegroundColor Red
    exit 1
}

function Invoke-Nativo {
    param(
        [string]$Exe,
        [string[]]$Argumentos,
        [string]$LogPath = ""
    )

    # O PowerShell 5.1 transforma o stderr de programas nativos em erro e, com
    # "Stop", aborta o script mesmo quando o programa funcionou. O PyInstaller
    # escreve todo o progresso no stderr. Vale so dentro desta funcao.
    $ErrorActionPreference = "Continue"

    $codigo = 1
    $saida = @()
    try {
        $saida = @(& $Exe @Argumentos 2>&1 | ForEach-Object { "$_" })
        $codigo = $LASTEXITCODE
    } catch {
        # Programa nao encontrado: $LASTEXITCODE ficaria com o valor anterior.
        $saida = @("$_")
    }

    if ($LogPath) {
        $saida | Out-File -FilePath $LogPath -Encoding UTF8
    }

    return [pscustomobject]@{ Codigo = $codigo; Saida = $saida }
}

function Mostrar-Saida {
    param([string[]]$Saida, [int]$Linhas = 15)
    $Saida | Select-Object -Last $Linhas | ForEach-Object {
        Write-Host "       $_" -ForegroundColor DarkGray
    }
}

function Registrar {
    param([string]$Nome, [string]$Status, [string]$Detalhe = "")
    [void]$Resultados.Add([pscustomobject]@{
        Nome = $Nome; Status = $Status; Detalhe = $Detalhe
    })
}

function Build-Exe {
    param([string]$Nome, [string]$SpecDir, [string]$DestDir)

    $specPath = Join-Path $SpecDir "$Nome.spec"
    $exeDist  = Join-Path $SpecDir "dist\$Nome.exe"
    $logPath  = Join-Path $LogDir "$Nome.log"

    Write-Host "  -> Compilando $Nome..." -ForegroundColor White -NoNewline

    if (-not (Test-Path $specPath)) {
        Write-Host " ERRO" -ForegroundColor Red
        Registrar $Nome "ERRO" "spec nao encontrado: $specPath"
        return
    }

    # Exe de um build anterior em dist\ nao pode passar por exe novo.
    Remove-Item -Force -ErrorAction SilentlyContinue $exeDist

    # Roda a partir da pasta do spec, para o pathex=['..'] funcionar.
    Push-Location $SpecDir
    try {
        $r = Invoke-Nativo $PythonExe @("-m", "PyInstaller", "--noconfirm", "--clean", $specPath) $logPath
    } finally {
        Pop-Location
    }

    if ($r.Codigo -ne 0) {
        Write-Host " ERRO" -ForegroundColor Red
        Mostrar-Saida $r.Saida
        Registrar $Nome "ERRO" "PyInstaller saiu com codigo $($r.Codigo); ver $logPath"
        return
    }

    if (-not (Test-Path $exeDist)) {
        Write-Host " ERRO" -ForegroundColor Red
        Registrar $Nome "ERRO" "exe nao encontrado apos o build; ver $logPath"
        return
    }

    try {
        if (-not (Test-Path $DestDir)) {
            New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
        }
        Copy-Item -Path $exeDist -Destination $DestDir -Force
    } catch {
        Write-Host " ERRO" -ForegroundColor Red
        Registrar $Nome "ERRO" "nao foi possivel copiar para $DestDir (o exe esta aberto?): $_"
        return
    }

    Write-Host " OK" -ForegroundColor Green
    Registrar $Nome "OK" (Join-Path $DestDir "$Nome.exe")
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Build All - Leitor HL7" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------------
# Etapa 1: Verificacao previa (falha aqui nao gera nenhum exe)
# -------------------------------------------------------------------------
Write-Host "[1/3] Verificacao previa..." -ForegroundColor Yellow

Push-Location $ProjectRoot
$versao = (Invoke-Nativo "git" @("log", "-1", "--format=%h %s")).Saida
$branch = (Invoke-Nativo "git" @("rev-parse", "--abbrev-ref", "HEAD")).Saida
Pop-Location
Write-Host "  Codigo: $branch | $versao" -ForegroundColor White

$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $PythonExe = $venvPython
} else {
    $pythonSistema = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonSistema) {
        Abortar "Python nao encontrado (nem $venvPython, nem no PATH)."
    }
    $PythonExe = $pythonSistema.Source
    Write-Host "  AVISO: venv nao encontrado; usando $PythonExe" -ForegroundColor Yellow
}
Write-Host "  Python: $PythonExe" -ForegroundColor White

# Sem Pillow o BH5100 compila, mas ignora as imagens em runtime.
$importaDependencias = "import serial, requests, PIL, PyInstaller"
if ((Invoke-Nativo $PythonExe @("-c", $importaDependencias)).Codigo -ne 0) {
    Write-Host "  Instalando dependencias de requirements.txt..." -ForegroundColor White
    $pip = Invoke-Nativo $PythonExe @("-m", "pip", "install", "-r", (Join-Path $ProjectRoot "requirements.txt"))
    if ($pip.Codigo -ne 0) {
        Mostrar-Saida $pip.Saida
        Abortar "nao foi possivel instalar as dependencias."
    }
    if ((Invoke-Nativo $PythonExe @("-c", $importaDependencias)).Codigo -ne 0) {
        Abortar "dependencias ausentes mesmo apos o pip install."
    }
}
Write-Host "  Dependencias: OK" -ForegroundColor White

# O PyInstaller compila mesmo faltando um modulo, e o exe so quebra ao abrir.
foreach ($m in $Maquinas) {
    Push-Location (Join-Path $ProjectRoot $m.SpecDir)
    try {
        $r = Invoke-Nativo $PythonExe @("-c", "import $($m.Nome)")
    } finally {
        Pop-Location
    }
    if ($r.Codigo -ne 0) {
        Mostrar-Saida $r.Saida 5
        Abortar "$($m.Nome) nao importa: o exe quebraria ao abrir."
    }
}
Write-Host "  Analisadores: OK" -ForegroundColor White
Write-Host ""

# -------------------------------------------------------------------------
# Etapa 2: Compilar
# -------------------------------------------------------------------------
Write-Host "[2/3] Compilando executaveis com PyInstaller..." -ForegroundColor Yellow

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

foreach ($m in $Maquinas) {
    Build-Exe $m.Nome (Join-Path $ProjectRoot $m.SpecDir) (Join-Path $ProjectRoot "build\$($m.Nome)")
}

# test_get_only: mesma pasta do pkl, mesmo destino.
Build-Exe "test_get_only" (Join-Path $ProjectRoot "pkl") (Join-Path $ProjectRoot "build\pkl")

# envio_com: ferramenta de teste do BH5100, fora deste repositorio (..\read).
$envioDir  = Join-Path (Split-Path -Parent $ProjectRoot) "read"
$envioDest = Join-Path $ProjectRoot "build\bh5100"

if (Test-Path (Join-Path $envioDir "envio_com.spec")) {
    Build-Exe "envio_com" $envioDir $envioDest

    # Nao sobrescreve a configuracao ja ajustada no PC.
    $envioConfigSrc = Join-Path $envioDir "config_envio_com.json"
    $envioConfigDst = Join-Path $envioDest "config_envio_com.json"
    if ((Test-Path $envioConfigSrc) -and -not (Test-Path $envioConfigDst)) {
        Copy-Item -Path $envioConfigSrc -Destination $envioConfigDst -Force
    }
} else {
    Write-Host "  -> envio_com PULADO" -ForegroundColor Yellow
    Registrar "envio_com" "PULADO" "$envioDir\envio_com.spec nao encontrado"
}

# Artefatos temporarios do PyInstaller.
foreach ($m in $Maquinas) {
    foreach ($temporario in @("dist", "build")) {
        $caminho = Join-Path $ProjectRoot "$($m.SpecDir)\$temporario"
        if (Test-Path $caminho) {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $caminho
        }
    }
}

Write-Host ""
Write-Host "  Resultado:" -ForegroundColor White
foreach ($r in $Resultados) {
    $cor = switch ($r.Status) { "OK" { "Green" } "PULADO" { "Yellow" } default { "Red" } }
    Write-Host ("    {0,-14} {1,-7} {2}" -f $r.Nome, $r.Status, $r.Detalhe) -ForegroundColor $cor
}
Write-Host ""

$erros = @($Resultados | Where-Object { $_.Status -eq "ERRO" })
if ($erros.Count -gt 0) {
    Abortar "$($erros.Count) executavel(is) com erro. Nada foi commitado; os exes com erro NAO foram atualizados."
}

# -------------------------------------------------------------------------
# Etapa 3: Commit
# -------------------------------------------------------------------------
if ($SkipGit) {
    Write-Host "[3/3] Commit ignorado (-SkipGit)." -ForegroundColor Yellow
} else {
    Write-Host "[3/3] Commit..." -ForegroundColor Yellow

    Push-Location $ProjectRoot

    $add = Invoke-Nativo "git" @("add", "-A")
    if ($add.Codigo -ne 0) {
        Mostrar-Saida $add.Saida
        Abortar "git add falhou."
    }

    # git grep sai com 0 quando encontra.
    $vazamento = Invoke-Nativo "git" @("grep", "--cached", "-I", "-l", "-E", $PadraoToken)
    if ($vazamento.Codigo -eq 0) {
        Invoke-Nativo "git" @("reset", "--quiet") | Out-Null
        Abortar "token preenchido em arquivo versionado, commit cancelado: $($vazamento.Saida -join ', ')"
    }

    $status = (Invoke-Nativo "git" @("status", "--porcelain")).Saida
    if (-not (($status -join "").Trim())) {
        Write-Host "  Nenhuma mudanca detectada. Nada para commit." -ForegroundColor Yellow
    } else {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $commit = Invoke-Nativo "git" @("commit", "-m", "Build automatico - $timestamp")
        if ($commit.Codigo -ne 0) {
            Mostrar-Saida $commit.Saida
            Abortar "git commit falhou."
        }
        Write-Host "  Commit realizado com sucesso!" -ForegroundColor Green
    }

    Pop-Location

    Write-Host ""
    Write-Host "  Para enviar, um comando por repositorio:" -ForegroundColor White
    Write-Host "    powershell -ExecutionPolicy Bypass -File .\enviar.ps1 empresa" -ForegroundColor White
    Write-Host "    powershell -ExecutionPolicy Bypass -File .\enviar.ps1 pessoal" -ForegroundColor White
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Processo concluido com sucesso!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
exit 0
