# =============================================================================
# enviar_pessoal_empresa.ps1 - envia todas as mudancas para o repositorio
# PESSOAL e, em seguida, sincroniza e envia para o da EMPRESA (HL7_integrator).
# Somente ASCII.
#
# Usa o git do WSL/Ubuntu, porque no PowerShell do Windows nao ha git.
# Se o envio pessoal falhar, a empresa NAO e atualizada.
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\enviar_pessoal_empresa.ps1
#   powershell -ExecutionPolicy Bypass -File .\enviar_pessoal_empresa.ps1 "mensagem"
# =============================================================================
param([string]$Mensagem = "")

$ErrorActionPreference = "Stop"
$Distro = "Ubuntu"

$posix = (& wsl -d $Distro wslpath -a "$PSScriptRoot").Trim()

& wsl -d $Distro bash "$posix/_enviar_pessoal.sh" "$Mensagem"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Falha no envio PESSOAL; a EMPRESA nao foi atualizada." -ForegroundColor Red
    exit 1
}

& wsl -d $Distro bash "$posix/_sincronizar_e_enviar_empresa.sh" "$Mensagem"
exit $LASTEXITCODE
