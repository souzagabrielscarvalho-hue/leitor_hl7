# =============================================================================
# enviar_pessoal.ps1 - envia todas as mudancas para o repositorio PESSOAL
# (leitor_hl7, publico). Somente ASCII.
#
# Usa o git do WSL/Ubuntu, porque no PowerShell do Windows nao ha git.
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\enviar_pessoal.ps1
#   powershell -ExecutionPolicy Bypass -File .\enviar_pessoal.ps1 "mensagem do commit"
# =============================================================================
param([string]$Mensagem = "")

$ErrorActionPreference = "Stop"
$Distro = "Ubuntu"

$posix = (& wsl -d $Distro wslpath -a "$PSScriptRoot").Trim()
& wsl -d $Distro bash "$posix/_enviar_pessoal.sh" "$Mensagem"
exit $LASTEXITCODE
