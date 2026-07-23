# install_skill.ps1 — Instala el skill cv_automatizacion en los directorios
# globales de Claude Code y Opencode para Windows.
#
# Uso:
#   .\scripts\install_skill.ps1              # instalar
#   .\scripts\install_skill.ps1 -Uninstall   # desinstalar
#
# Ejecutar desde la raiz del repo.

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$RepoSkill = ".claude\skills\cv_automatizacion.md"

if (-not (Test-Path $RepoSkill)) {
    Write-Error "ERROR: $RepoSkill no encontrado. Ejecuta este script desde la raiz del repo."
    exit 1
}

$ClaudeGlobalDir = Join-Path $env:USERPROFILE ".claude\skills"
$ClaudeGlobalFile = Join-Path $ClaudeGlobalDir "cv_automatizacion.md"

$OpencodeGlobalDir = Join-Path $env:USERPROFILE ".config\opencode\skills\cv_automatizacion"
$OpencodeGlobalFile = Join-Path $OpencodeGlobalDir "SKILL.md"

if ($Uninstall) {
    Remove-Item -Force $ClaudeGlobalFile -ErrorAction SilentlyContinue
    Remove-Item -Force $OpencodeGlobalFile -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $OpencodeGlobalDir -ErrorAction SilentlyContinue
    Write-Host "Desinstalado cv_automatizacion skill de:"
    Write-Host "  - $ClaudeGlobalFile"
    Write-Host "  - $OpencodeGlobalFile"
    exit 0
}

New-Item -ItemType Directory -Force -Path $ClaudeGlobalDir | Out-Null
New-Item -ItemType Directory -Force -Path $OpencodeGlobalDir | Out-Null

Copy-Item -Force $RepoSkill $ClaudeGlobalFile
Copy-Item -Force $RepoSkill $OpencodeGlobalFile

Write-Host "Instalado cv_automatizacion skill:"
Write-Host "  Claude Code  -> $ClaudeGlobalFile"
Write-Host "  Opencode     -> $OpencodeGlobalFile"
Write-Host ""
Write-Host "El skill ahora esta disponible desde cualquier directorio."
Write-Host "En Claude Code u Opencode, decile: 'genera el CV para la oferta <url>'"
Write-Host "y el asistente correra el pipeline por vos."
