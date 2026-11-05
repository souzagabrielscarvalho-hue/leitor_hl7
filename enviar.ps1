# =============================================================================
# enviar.ps1 - Envia a branch atual para UM repositorio
# =============================================================================
# Uso:
#   .\enviar.ps1 empresa   Vida-Exames/HL7_integrator
#   .\enviar.ps1 pessoal   souzagabrielscarvalho-hue/leitor_hl7 (PUBLICO)
#
# Usa a URL, e nao o nome do remote, porque "origin" aponta para repositorios
# diferentes conforme o clone. Somente ASCII (ver build_all.ps1).
# =============================================================================

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("empresa", "pessoal")]
    [string]$Repositorio
)

# git escreve o progresso no stderr; com "Stop" o PowerShell 5.1 poderia abortar.
$ErrorActionPreference = "Continue"

$Destinos = @{
    empresa = @{ Url = "https://github.com/Vida-Exames/HL7_integrator"; Publico = $false }
    pessoal = @{ Url = "https://github.com/souzagabrielscarvalho-hue/leitor_hl7.git"; Publico = $true }
}
$Destino = $Destinos[$Repositorio]

function Abortar {
    param([string]$Mensagem)
    Write-Host ""
    Write-Host "ENVIO CANCELADO: $Mensagem" -ForegroundColor Red
    exit 1
}

Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

$branch = git symbolic-ref --short -q HEAD
if (-not $branch) {
    Abortar "HEAD destacado: troque para uma branch antes de enviar."
}

$pendentes = git status --porcelain
if ($pendentes) {
    Write-Host ($pendentes -join "`n")
    Abortar "ha alteracoes sem commit. Rode o build_all.ps1 ou faca o commit antes."
}

# Mesmo padrao do build_all.ps1. git grep sai com 0 quando encontra.
$vazamento = git grep -I -l -E 'exam_images_machine_token.{1,6}[A-Za-z0-9]{20,}' HEAD
if ($LASTEXITCODE -eq 0) {
    Abortar "token preenchido em arquivo versionado: $($vazamento -join ', ')"
}

Write-Host "Enviando $branch ($(git log -1 --format=%h)) para $($Destino.Url)" -ForegroundColor White
if ($Destino.Publico) {
    Write-Host "ATENCAO: este repositorio e PUBLICO." -ForegroundColor Yellow
}

git push $Destino.Url "HEAD:refs/heads/$branch"
if ($LASTEXITCODE -ne 0) {
    Abortar "git push falhou."
}

Write-Host "Envio concluido." -ForegroundColor Green
exit 0
