# Gera os .exe do PKL (pkl e test_get_only) em build\pkl\. Somente ASCII.
. "$PSScriptRoot\_lib_build.ps1"
Build-Maquina "pkl"
Build-Maquina "test_get_only"
