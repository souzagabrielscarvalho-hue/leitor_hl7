# Gera o .exe de TODAS as maquinas. Somente ASCII.
. "$PSScriptRoot\_lib_build.ps1"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Build de TODAS as maquinas - Leitor HL7" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$maquinas = @("bh5100", "ca150", "coagmaster", "mek7300", "pkl", "test_get_only", "urit8021", "vidas1600")
$erros = @()

foreach ($m in $maquinas) {
    try {
        Build-Maquina $m
    } catch {
        Write-Host "  FALHOU: $m" -ForegroundColor Red
        Write-Host "       $_" -ForegroundColor DarkGray
        $erros += $m
    }
}

Write-Host ""
if ($erros.Count -gt 0) {
    Write-Host "Maquinas com erro: $($erros -join ', ')" -ForegroundColor Red
    Write-Host "Os exes com erro NAO foram atualizados." -ForegroundColor Red
    exit 1
}

Write-Host "Todos os executaveis foram gerados com sucesso." -ForegroundColor Green
exit 0
