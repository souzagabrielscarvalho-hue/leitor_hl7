# =============================================================================
# _lib_build.ps1 - Biblioteca comum de build (dot-source pelos build_*.ps1)
# =============================================================================
# Nao execute este arquivo direto. Os scripts build_<maquina>.ps1 fazem:
#   . "$PSScriptRoot\_lib_build.ps1"
#   Build-Maquina "<maquina>"
#
# Compila no Windows com o PyInstaller e copia o .exe para build\<maquina>\.
# Velocidade: cada exe tem o proprio cache em build_cache\<nome> (fora do git),
# reaproveitado no build seguinte (sem --clean). Para compilar do zero, defina
# LEITOR_BUILD_LIMPO=1 antes (ex.: depois de trocar a versao do Python).
# Mantenha SOMENTE ASCII: o PowerShell 5.1 le UTF-8 sem BOM como ANSI e
# corrompe os acentos.
# =============================================================================

$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    # Raiz do leitor_hl7 (pai da pasta Comandos onde este arquivo esta).
    return (Split-Path -Parent $PSScriptRoot)
}

function Resolver-Python {
    $venv = Join-Path (Get-ProjectRoot) "venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $sis = Get-Command python -ErrorAction SilentlyContinue
    if (-not $sis) { throw "Python nao encontrado (nem venv, nem no PATH)." }
    Write-Host "  AVISO: venv nao encontrado; usando $($sis.Source)" -ForegroundColor Yellow
    return $sis.Source
}

function Invoke-Nativo {
    param([string]$Exe, [string[]]$Argumentos, [string]$LogPath = "")

    # PyInstaller escreve todo o progresso no stderr; com "Stop" o PowerShell 5.1
    # abortaria mesmo em caso de sucesso. Vale so dentro desta funcao.
    $ErrorActionPreference = "Continue"

    $codigo = 1
    $saida = @()
    try {
        $saida = @(& $Exe @Argumentos 2>&1 | ForEach-Object { "$_" })
        $codigo = $LASTEXITCODE
    } catch {
        $saida = @("$_")
    }
    if ($LogPath) { $saida | Out-File -FilePath $LogPath -Encoding UTF8 }
    return [pscustomobject]@{ Codigo = $codigo; Saida = $saida }
}

function Compilar {
    param([string]$Nome, [string]$SpecDir, [string]$DestDir, [bool]$VerificarImport = $true)

    $python  = Resolver-Python
    $logDir  = Join-Path (Get-ProjectRoot) "build_logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    $specPath = Join-Path $SpecDir "$Nome.spec"
    $cache    = Join-Path (Get-ProjectRoot) "build_cache\$Nome"
    $workDir  = Join-Path $cache "work"
    $distDir  = Join-Path $cache "dist"
    $exeDist  = Join-Path $distDir "$Nome.exe"
    $logPath  = Join-Path $logDir "$Nome.log"
    $inicio   = Get-Date

    Write-Host "  -> Compilando $Nome..." -ForegroundColor White -NoNewline

    if (-not (Test-Path $specPath)) {
        Write-Host " ERRO" -ForegroundColor Red
        throw "spec nao encontrado: $specPath"
    }

    # O PyInstaller compila mesmo faltando um modulo, e o exe quebra ao abrir.
    # Pulada em scripts que executam codigo ao importar (ex.: test_get_only, que
    # abre config_pkl.json no nivel do modulo).
    if ($VerificarImport) {
        Push-Location $SpecDir
        try {
            $imp = Invoke-Nativo $python @("-c", "import $Nome")
        } finally { Pop-Location }
        if ($imp.Codigo -ne 0) {
            Write-Host " ERRO" -ForegroundColor Red
            $imp.Saida | Select-Object -Last 5 | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
            throw "$Nome nao importa: o exe quebraria ao abrir."
        }
    }

    # Exe de um build anterior em dist\ nao pode passar por exe novo.
    Remove-Item -Force -ErrorAction SilentlyContinue $exeDist

    $argumentos = @("-m", "PyInstaller", "--noconfirm", "--workpath", $workDir, "--distpath", $distDir, $specPath)
    if ($env:LEITOR_BUILD_LIMPO -eq "1") { $argumentos += "--clean" }

    # Roda a partir da pasta do spec, para o pathex=['..'] funcionar. Cache de
    # binarios proprio por exe: builds em paralelo nao disputam o mesmo.
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $cache "config"
    Push-Location $SpecDir
    try {
        $r = Invoke-Nativo $python $argumentos $logPath
    } finally {
        Pop-Location
        Remove-Item Env:\PYINSTALLER_CONFIG_DIR -ErrorAction SilentlyContinue
    }

    if ($r.Codigo -ne 0) {
        Write-Host " ERRO" -ForegroundColor Red
        $r.Saida | Select-Object -Last 15 | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
        throw "PyInstaller saiu com codigo $($r.Codigo); ver $logPath"
    }
    if (-not (Test-Path $exeDist)) {
        Write-Host " ERRO" -ForegroundColor Red
        throw "exe nao encontrado apos o build; ver $logPath"
    }

    if (-not (Test-Path $DestDir)) { New-Item -ItemType Directory -Path $DestDir -Force | Out-Null }
    Copy-Item -Path $exeDist -Destination $DestDir -Force

    # Sobras de builds antigos (antes do build_cache) na pasta do spec.
    foreach ($temp in @("dist", "build")) {
        $c = Join-Path $SpecDir $temp
        if (Test-Path $c) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $c }
    }

    $segundos = [int]((Get-Date) - $inicio).TotalSeconds
    Write-Host " OK (${segundos}s)" -ForegroundColor Green
    Write-Host "       $(Join-Path $DestDir "$Nome.exe")" -ForegroundColor DarkGray
}

function Build-Maquina {
    param([Parameter(Mandatory = $true)][string]$Nome)

    $root = Get-ProjectRoot

    # Nome do analisador -> pasta do spec. "leitor" e o leitor universal
    # (painel + todas as maquinas num exe so), em universal\ -> build\leitor.
    $mapa = @{
        "leitor"     = "universal"
        "bh5100"     = "bh5100"
        "ca150"      = "ca150"
        "coagmaster" = "Coagmaster"
        "mek7300"    = "mek7300"
        "pkl"        = "pkl"
        "urit8021"   = "urit-8021"
        "vidas1600"  = "vidas1600"
    }

    $nome = $Nome.ToLower()

    # test_get_only: compila na pasta do pkl e vai para build\pkl. Sem verificar
    # o import, pois o script abre config_pkl.json ao ser importado.
    if ($nome -eq "test_get_only") {
        Compilar "test_get_only" (Join-Path $root "pkl") (Join-Path $root "build\pkl") -VerificarImport:$false
        return
    }

    if (-not $mapa.ContainsKey($nome)) {
        throw "Maquina desconhecida: '$Nome'. Validas: $(($mapa.Keys | Sort-Object) -join ', '), test_get_only"
    }

    Compilar $nome (Join-Path $root $mapa[$nome]) (Join-Path $root "build\$nome")
}
