# =============================================================================
# build_all.ps1 - Script para compilar todos os .exe e preparar para deploy
# =============================================================================
# Uso: .\build_all.ps1
# Este script:
#   1. Compila todos os .exe usando PyInstaller com os arquivos .spec
#   2. Copia os .exe para as pastas em build/
#   3. Faz commit e push para o repositório
# =============================================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Ativar o ambiente virtual
$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$venvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"

if (Test-Path $venvActivate) {
    Write-Host "  Ativando ambiente virtual..." -ForegroundColor Gray
    . $venvActivate
} else {
    Write-Host "  AVISO: Ambiente virtual nao encontrado em $venvActivate" -ForegroundColor Yellow
    Write-Host "  Continuando com Python do sistema..." -ForegroundColor Yellow
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Build All - Leitor HL7" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------------
# Definir as máquinas (nome da pasta = nome do spec = nome do exe)
# -------------------------------------------------------------------------
$maquinas = @(
    @{ Nome = "bh5100";     SpecDir = "bh5100" },
    @{ Nome = "coagmaster"; SpecDir = "Coagmaster" },
    @{ Nome = "mek7300";    SpecDir = "mek7300" },
    @{ Nome = "pkl";        SpecDir = "pkl" },
    @{ Nome = "vidas1600";  SpecDir = "vidas1600" }
)

# -------------------------------------------------------------------------
# Etapa 1: Compilar cada .exe
# -------------------------------------------------------------------------
Write-Host "[1/3] Compilando executáveis com PyInstaller..." -ForegroundColor Yellow
Write-Host ""

foreach ($m in $maquinas) {
    $nome     = $m.Nome
    $specDir  = $m.SpecDir
    $specPath = Join-Path $ProjectRoot "$specDir\$nome.spec"
    $destDir  = Join-Path $ProjectRoot "build\$nome"

    Write-Host "  -> Compilando $nome..." -ForegroundColor White -NoNewline

    if (-not (Test-Path $specPath)) {
        Write-Host " SPEC NAO ENCONTRADO ($specPath)" -ForegroundColor Red
        continue
    }

    # Executar PyInstaller a partir do diretório do spec (para pathex funcionar)
    Push-Location (Join-Path $ProjectRoot $specDir)
    try {
        pyinstaller --noconfirm --clean $specPath 2>&1 | Out-Null
        Pop-Location
    }
    catch {
        Pop-Location
        Write-Host " ERRO" -ForegroundColor Red
        Write-Host "    Detalhes: $_" -ForegroundColor Red
        continue
    }

    # O exe gerado fica em dist/<nome>.exe (dentro do diretório do spec)
    $exeSource = Join-Path $ProjectRoot "$specDir\dist\$nome.exe"

    if (-not (Test-Path $exeSource)) {
        # Tentar caminho alternativo: dist/<nome>.exe na raiz
        $exeSource = Join-Path $ProjectRoot "dist\$nome.exe"
    }

    if (-not (Test-Path $exeSource)) {
        Write-Host " EXE NAO ENCONTRADO apos build" -ForegroundColor Red
        continue
    }

    # Criar pasta de destino se nao existir
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    # Copiar o exe para build/<nome>/
    Copy-Item -Path $exeSource -Destination $destDir -Force
    Write-Host " OK" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Compilacao concluida!" -ForegroundColor Green
Write-Host ""

# -------------------------------------------------------------------------
# Etapa 2: Limpar artefatos temporarios do PyInstaller
# -------------------------------------------------------------------------
Write-Host "[2/3] Limpando artefatos temporarios..." -ForegroundColor Yellow

# Remover pastas dist/ e arquivos temporarios em cada diretorio de spec
foreach ($m in $maquinas) {
    $specDir = $m.SpecDir
    $distDir = Join-Path $ProjectRoot "$specDir\dist"
    $buildDir = Join-Path $ProjectRoot "$specDir\build"

    if (Test-Path $distDir)  { Remove-Item -Recurse -Force $distDir }
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
}

# Remover dist/ e build/ na raiz (se existirem)
$rootDist  = Join-Path $ProjectRoot "dist"
$rootBuild = Join-Path $ProjectRoot "build_temp"
if (Test-Path $rootDist) { Remove-Item -Recurse -Force $rootDist }

Write-Host "  Limpeza concluida!" -ForegroundColor Green
Write-Host ""

# -------------------------------------------------------------------------
# Etapa 3: Commit e Push
# -------------------------------------------------------------------------
Write-Host "[3/3] Commit e Push para o repositorio..." -ForegroundColor Yellow
Write-Host ""

Push-Location $ProjectRoot

# Adicionar remote 'personal' se nao existir
$remotes = git remote
if ($remotes -notcontains "personal") {
    Write-Host "  Adicionando remote 'personal'..." -ForegroundColor White
    git remote add personal https://github.com/souzagabrielscarvalho-hue/leitor_hl7.git
}

# Adicionar arquivos
Write-Host "  Adicionando arquivos ao git..." -ForegroundColor White
git add -A

# Verificar se ha mudancas para commit
$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-Host "  Nenhuma mudanca detectada. Nada para commit." -ForegroundColor Yellow
} else {
    # Commit
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    git commit -m "Build automatico - $timestamp"
    Write-Host "  Commit realizado com sucesso!" -ForegroundColor Green
}

# Push
Write-Host "  Enviando para o repositorio..." -ForegroundColor White
git push personal main

Pop-Location

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Processo concluido com sucesso!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan