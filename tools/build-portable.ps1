param([switch]$Replace)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectPrefix = $projectRoot.TrimEnd('\') + '\'
function Assert-InProject([string]$Target) {
    $resolved = [IO.Path]::GetFullPath($Target)
    if (-not $resolved.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build target is outside the project: $resolved"
    }
    return $resolved
}
$pythonPath = Assert-InProject (Join-Path $projectRoot '.venv\Scripts\python.exe')
$runtimePath = Assert-InProject (Join-Path $projectRoot 'runtime')
$appPath = Assert-InProject (Join-Path $runtimePath 'app')
$workPath = Assert-InProject (Join-Path $projectRoot 'work\pyinstaller')
$stagePath = Assert-InProject (Join-Path $projectRoot ('work\portable-' + [guid]::NewGuid().ToString('N')))
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run install.cmd to create the local build environment first.' }
if ((Test-Path -LiteralPath $appPath) -and -not $Replace) {
    throw 'runtime\app already exists. Use -Replace to rebuild while keeping a backup under work.'
}
& $pythonPath -c 'import PyInstaller'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is missing from the local .venv.' }
New-Item -ItemType Directory -Path $stagePath -Force | Out-Null
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
Push-Location $projectRoot
try {
    & $pythonPath -m PyInstaller --distpath $stagePath --workpath $workPath (Join-Path $projectRoot 'YutoNight.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed; the existing runtime was not changed.' }
} finally { Pop-Location }
$stagedApp = Assert-InProject (Join-Path $stagePath 'app')
$stagedExe = Assert-InProject (Join-Path $stagedApp 'YutoNight.exe')
if (-not (Test-Path -LiteralPath $stagedExe)) { throw 'The build did not produce YutoNight.exe.' }
$stagedLicenses = Assert-InProject (Join-Path $stagedApp '_internal\licenses')
if (Test-Path -LiteralPath $stagedLicenses) {
    $publicLicenses = Assert-InProject (Join-Path $stagedApp 'licenses')
    Copy-Item -LiteralPath $stagedLicenses -Destination $publicLicenses -Recurse
}
$backupPath = $null
if (Test-Path -LiteralPath $appPath) {
    if ((Get-Item -LiteralPath $appPath).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to move a linked runtime\app directory.'
    }
    $backupPath = Assert-InProject (Join-Path $projectRoot ('work\app-backup-' + [guid]::NewGuid().ToString('N')))
    Move-Item -LiteralPath $appPath -Destination $backupPath
}
try {
    Move-Item -LiteralPath $stagedApp -Destination $appPath
} catch {
    if ($backupPath -and -not (Test-Path -LiteralPath $appPath)) {
        Move-Item -LiteralPath $backupPath -Destination $appPath
    }
    throw
}
# External inference executables need application-local C++ runtime DLLs too.
$cppRuntime = Join-Path $projectRoot '.venv\Lib\site-packages\PySide6'
foreach ($engine in @('runtime\llama', 'runtime\whisper\Release')) {
    $enginePath = Assert-InProject (Join-Path $projectRoot $engine)
    if (Test-Path -LiteralPath $enginePath) {
        foreach ($dll in @('MSVCP140.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll')) {
            Copy-Item -LiteralPath (Join-Path $cppRuntime $dll) -Destination (Join-Path $enginePath $dll)
        }
    }
}
Write-Host ('Portable application: ' + (Join-Path $appPath 'YutoNight.exe'))
Write-Host 'Copy the whole project portable folder, including runtime, models, and licenses. Do not copy only the EXE.'
if ($backupPath) { Write-Host ('Previous application backup: ' + $backupPath) }
