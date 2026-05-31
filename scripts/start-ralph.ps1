#Requires -Version 5.1
<#
.SYNOPSIS
    Ralph startup script.

.DESCRIPTION
    Verifies prerequisites (uv, gh, claude, ~/.ralph/config.toml), syncs the
    venv, and launches ralph-executor. The queue PBI's target_repo
    frontmatter is the single source of truth for which target repo the
    executor works on; no -Workspace parameter is needed.

.PARAMETER Watch
    Pass --watch to ralph-executor (daemon mode; survives idle).

.PARAMETER Once
    Pass --once to ralph-executor (single iteration).

.PARAMETER LogLevel
    Log level forwarded to ralph-executor (DEBUG, INFO, WARNING, ERROR).

.EXAMPLE
    .\scripts\start-ralph.ps1
    .\scripts\start-ralph.ps1 -Watch
    .\scripts\start-ralph.ps1 -Once
#>
[CmdletBinding()]
param(
    [switch]$Watch,
    [switch]$Once,
    [ValidateSet('DEBUG', 'INFO', 'WARNING', 'ERROR')]
    [string]$LogLevel = 'INFO'
)

$ErrorActionPreference = 'Stop'

function Require-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing prerequisite: '$Name' not on PATH. $Hint"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $repoRoot
Write-Host "[start-ralph] repo:      $repoRoot"

Require-Command uv     'Install from https://docs.astral.sh/uv/'
Require-Command git    'Install git for Windows.'
Require-Command gh     'Install GitHub CLI: https://cli.github.com/'
Require-Command claude 'Install Claude Code CLI: https://docs.claude.com/claude-code'

$configPath = Join-Path $HOME '.ralph/config.toml'
if (-not (Test-Path $configPath)) {
    Write-Host "[start-ralph] missing $configPath -- running 'uv run ralph-executor init'"
    & uv run ralph-executor init
    if ($LASTEXITCODE -ne 0) { throw "ralph-executor init failed (exit $LASTEXITCODE)" }
}

$ghStatus = & gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warning "gh auth status failed. Run: gh auth login"
    Write-Host $ghStatus
}

Write-Host "[start-ralph] syncing venv (uv sync)..."
& uv sync
if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit $LASTEXITCODE)" }

$cmdArgs = @('run', 'ralph-executor', '--log-level', $LogLevel)
if ($Watch) { $cmdArgs += '--watch' }
if ($Once)  { $cmdArgs += '--once' }

Write-Host "[start-ralph] launching: uv $($cmdArgs -join ' ')"
& uv @cmdArgs
exit $LASTEXITCODE
