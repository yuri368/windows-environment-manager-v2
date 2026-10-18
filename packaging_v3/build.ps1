[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$NodePath,
    [string]$IsccPath,
    [ValidateSet('User', 'Machine', 'Both')][string]$Installers = 'Both',
    [switch]$BundleOnly
)
$ErrorActionPreference = 'Stop'
$buildRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $buildRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot '.pyinstaller-cache'
$runName = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$runRoot = Join-Path $buildRoot "output/$runName"
$stage = Join-Path $runRoot 'stage'
$venvPython = Join-Path $buildRoot '.build-venv/Scripts/python.exe'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    if (Test-Path -LiteralPath $runRoot) {
        & $Program @Arguments 2>&1 | Tee-Object -FilePath (Join-Path $runRoot 'commands.log') -Append
    } else {
        & $Program @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { throw 'Windows x64 build only.' }
if (-not $NodePath) { $NodePath = (Get-Command node.exe -ErrorAction Stop).Source }
$NodePath = (Resolve-Path -LiteralPath $NodePath).Path
if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-Checked $Python @('-m', 'venv', (Join-Path $buildRoot '.build-venv'))
}
Invoke-Checked $venvPython @('-B', '-m', 'pip', '--isolated', '--cache-dir', (Join-Path $buildRoot '.pip-cache'), 'install', '--index-url', 'https://pypi.org/simple', '--only-binary=:all:', '--require-hashes', '-r', (Join-Path $buildRoot 'requirements-build.txt'))
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
Start-Transcript -Path (Join-Path $runRoot 'build.log') | Out-Null
$oldStage = $env:V3_BUILD_STAGE
Push-Location $repoRoot
try {
    Invoke-Checked $venvPython @('-B', '-m', 'unittest', 'discover', '-s', 'tests_v3', '-p', 'test_launch.py', '-v')
    Invoke-Checked $venvPython @('-B', (Join-Path $buildRoot 'prepare.py'), 'stage', '--destination', $stage, '--node', $NodePath)
    $env:V3_BUILD_STAGE = $stage
    Set-Location $runRoot
    Invoke-Checked $venvPython @('-B', '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', (Join-Path $runRoot 'dist'), '--workpath', (Join-Path $runRoot 'work'), (Join-Path $buildRoot 'EnvManagerV3.spec'))
    $bundle = Join-Path $runRoot 'dist/EnvManagerV3'
    Invoke-Checked $venvPython @('-B', (Join-Path $buildRoot 'prepare.py'), 'seal', '--bundle', $bundle)
    try {
        Invoke-Checked $venvPython @('-B', (Join-Path $buildRoot 'smoke.py'), '--exe', (Join-Path $bundle 'EnvManagerV3.exe'), '--evidence-dir', (Join-Path $runRoot 'smoke'))
        Invoke-Checked $venvPython @('-B', (Join-Path $buildRoot 'smoke-upgrade.py'), '--exe', (Join-Path $bundle 'EnvManagerV3.exe'), '--evidence-dir', (Join-Path $runRoot 'upgrade-smoke'))
    } finally {
        # Preserve artifacts + hashes with honest failed/passed evidence, even on a smoke failure.
        Invoke-Checked $venvPython @('-B', (Join-Path $buildRoot 'prepare.py'), 'finalize', '--bundle', $bundle, '--output', $runRoot)
    }
    if (-not $BundleOnly) {
        if (-not $IsccPath) {
            $localCompiler = Join-Path $buildRoot 'tools/inno-6.7.3/ISCC.exe'
            if (Test-Path -LiteralPath $localCompiler) { $IsccPath = $localCompiler }
            $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if (-not $IsccPath -and $compiler) { $IsccPath = $compiler.Source }
            foreach ($candidate in @("${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe", "$env:LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe")) {
                if (-not $IsccPath -and (Test-Path -LiteralPath $candidate)) { $IsccPath = $candidate }
            }
        }
        if (-not $IsccPath) { throw "Onedir and hashes are ready at $runRoot; ISCC not found. Run packaging_v3/prepare-inno.ps1, pass -IsccPath or use -BundleOnly. No installer was produced." }
        $IsccPath = (Resolve-Path -LiteralPath $IsccPath).Path
        $compilerInfo = Get-Item -LiteralPath $IsccPath
        $toolLock = Get-Content -LiteralPath (Join-Path $buildRoot 'inno-tool-lock.json') -Raw | ConvertFrom-Json
        foreach ($entry in $toolLock.files.PSObject.Properties) {
            $toolFile = Join-Path $compilerInfo.DirectoryName $entry.Name
            if (-not (Test-Path -LiteralPath $toolFile) -or (Get-FileHash -LiteralPath $toolFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw "Pinned Inno $($toolLock.version) file mismatch: $($entry.Name)" }
        }
        [ordered]@{ path=$IsccPath; version=$toolLock.version; fileVersion=$compilerInfo.VersionInfo.FileVersion; sha256=(Get-FileHash -LiteralPath $IsccPath -Algorithm SHA256).Hash; sourceUrl=$toolLock.sourceUrl; installerSha256=$toolLock.installerSha256 } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $runRoot 'inno-tool.json')
        $finalizeArguments = @('-B', (Join-Path $buildRoot 'prepare.py'), 'finalize', '--bundle', $bundle, '--output', $runRoot)
        $modes = if ($Installers -eq 'Both') { @('User', 'Machine') } else { @($Installers) }
        foreach ($mode in $modes) {
            $iss = if ($mode -eq 'Machine') { 'EnvManagerV3-machine.iss' } else { 'EnvManagerV3.iss' }
            Invoke-Checked $IsccPath @('/Qp', "/DBundleDir=$bundle", "/DOutputDir=$runRoot", (Join-Path $buildRoot $iss))
            $installer = Join-Path $runRoot ("EnvManagerV3-3.0.0-dev1-setup-{0}-x64.exe" -f $mode.ToLowerInvariant())
            $finalizeArguments += @('--installer', $installer)
        }
        Invoke-Checked $venvPython $finalizeArguments
    }
    Invoke-Checked $venvPython @('-B', (Join-Path $buildRoot 'verify-bundle.py'), $runRoot)
    Write-Host "Build and isolated file smoke passed: $runRoot"
} finally {
    $env:V3_BUILD_STAGE = $oldStage
    Pop-Location
    Stop-Transcript | Out-Null
}
