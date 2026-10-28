#Requires -Version 5.1
<#
.SYNOPSIS
  Scaffold a new project from dev-kit templates.

.DESCRIPTION
  Copies a stack template, vendors the devkit core package (Python stacks only),
  substitutes placeholders, and creates a local .env.

.EXAMPLE
  .\init-project.ps1 -TargetDir "F:\my-app" -ProjectName "my-app" -Stack python-fastapi

.NOTES
  Output is intentionally ASCII-only to avoid console encoding issues on Windows.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TargetDir,
    [Parameter(Mandatory = $true)][string]$ProjectName,
    [ValidateSet('python-fastapi', 'java-springboot', 'vue3-vite')]
    [string]$Stack = 'python-fastapi',
    [string]$DevKitDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

function Fail([string]$msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }
function Info([string]$msg) { Write-Host "[ OK ] $msg" -ForegroundColor Green }
function Step([string]$msg) { Write-Host "[ .. ] $msg" -ForegroundColor Cyan }

$templateDir = Join-Path $DevKitDir "TEMPLATES/$Stack"
if (-not (Test-Path $templateDir)) { Fail "Template not found: $templateDir" }

if (Test-Path $TargetDir) {
    $existing = Get-ChildItem $TargetDir -Force | Where-Object { $_.Name -notin @('.git') }
    if ($existing) { Fail "Target directory is not empty: $TargetDir" }
}

Step "Creating $TargetDir"
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

# --- 1. copy template ---
Step "Copying template: $Stack"
Copy-Item -Path (Join-Path $templateDir '*') -Destination $TargetDir -Recurse -Force
Copy-Item -Path (Join-Path $templateDir '.env.example') -Destination $TargetDir -Force -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $templateDir '.gitignore') -Destination $TargetDir -Force -ErrorAction SilentlyContinue

# --- 2. vendor devkit core (python stacks) ---
if ($Stack -eq 'python-fastapi') {
    $coreDir = Join-Path $DevKitDir 'CORE/python/devkit'
    if (-not (Test-Path $coreDir)) { Fail "devkit core not found: $coreDir" }
    Step 'Vendoring devkit core into project (keeps the project self-contained)'
    Copy-Item -Path $coreDir -Destination (Join-Path $TargetDir 'devkit') -Recurse -Force
}

# --- 3. drop agent doc templates (never overwrite existing) ---
Step 'Dropping AGENTS.md / CLAUDE.md / REVIEW.md templates'
foreach ($f in @('AGENTS.md', 'CLAUDE.md', 'REVIEW.md')) {
    $src = Join-Path $DevKitDir "SPEC/templates/$f.template"
    $dst = Join-Path $TargetDir $f
    if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst }
}

# --- 4. placeholder substitution ---
$clean = $ProjectName -replace '[^A-Za-z0-9]', '_'
$prefix = ($clean.ToUpper() + '_')
$sysCode = (($clean -replace '_', '').Substring(0, [Math]::Min(2, ($clean -replace '_', '').Length))).ToUpper()

$map = @{
    '__APP_NAME__'          = $ProjectName
    '__APP_PREFIX__'        = $prefix
    '__APP_CODE_OK__'       = "${sysCode}0000"
    '__APP_CODE_INTERNAL__' = "${sysCode}5000"
    '__PKG__'               = "com.example.$($clean.ToLower() -replace '_', '')"
}

Step "Substituting placeholders (prefix=$prefix)"
$files = Get-ChildItem $TargetDir -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '(\.venv|node_modules|\.git|__pycache__)' }
foreach ($file in $files) {
    try { $text = [System.IO.File]::ReadAllText($file.FullName) } catch { continue }
    $original = $text
    foreach ($key in $map.Keys) { $text = $text.Replace($key, $map[$key]) }
    if ($text -ne $original) { [System.IO.File]::WriteAllText($file.FullName, $text) }
}

# --- 5. local env ---
$envExample = Join-Path $TargetDir '.env.example'
if (Test-Path $envExample) {
    Copy-Item $envExample (Join-Path $TargetDir '.env') -Force
    Info 'Created .env from .env.example (never commit it)'
}

Write-Host ''
Info "Project created: $TargetDir"
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Yellow
switch ($Stack) {
    'python-fastapi' {
        Write-Host "  1. cd `"$TargetDir`""
        Write-Host '  2. uv sync            (or: python -m venv .venv && pip install -e .)'
        Write-Host '  3. pytest             (smoke test must pass)'
        Write-Host '  4. uvicorn app.main:app --reload'
        Write-Host '  5. Fill AGENTS.md / CLAUDE.md / REVIEW.md placeholders, then DELETE unfilled sections'
    }
    'java-springboot' {
        Write-Host "  1. cd `"$TargetDir`""
        Write-Host '  2. Confirm JDK baseline in README.md (Java 8 / 17 / 21) before coding'
        Write-Host '  3. mvn -q compile'
        Write-Host '  4. Fill AGENTS.md / CLAUDE.md / REVIEW.md'
    }
    'vue3-vite' {
        Write-Host "  1. cd `"$TargetDir`""
        Write-Host '  2. npm install'
        Write-Host '  3. npm run build      (or npm run dev)'
        Write-Host '  4. Fill AGENTS.md / CLAUDE.md / REVIEW.md'
    }
}
