param(
    [switch]$Demo,
    [string]$Config,
    [switch]$Check,
    [switch]$CheckAdmin,
    [string]$StateDir
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$venvDir = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvPythonw = Join-Path $venvDir 'Scripts\pythonw.exe'
$requirementsFile = Join-Path $projectRoot 'requirements.txt'

function Get-AbsoluteArgumentPath([string]$Value) {
    if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Value))
}

function Quote-NativePath([string]$Value) {
    # Windows paths have no double quotes. Double terminal backslashes before
    # the closing quote so the Windows CRT does not treat that quote as escaped.
    return ('"' + ($Value -replace '(\\+)$', '$1$1') + '"')
}

function Stop-WithMessage([string]$Message) {
    [Console]::Error.WriteLine("启动失败：$Message")
    [void](Read-Host '按 Enter 关闭此窗口')
    exit 1
}

try {
    if (-not (Test-Path -LiteralPath $requirementsFile -PathType Leaf)) {
        Stop-WithMessage "找不到依赖文件：$requirementsFile"
    }

    if (-not ((Test-Path -LiteralPath $venvPython -PathType Leaf) -and (Test-Path -LiteralPath $venvPythonw -PathType Leaf))) {
        $pythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { $pythonCommand = Get-Command 'python' -ErrorAction SilentlyContinue }
        if (-not $pythonCommand) { Stop-WithMessage '未找到 Python。请安装 Python 3.11 或更新版本，并启用 PATH。' }
        $systemPython = $pythonCommand.Source

        & $systemPython -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 2)' 2>$null
        if ($LASTEXITCODE -eq 2) { Stop-WithMessage '需要 Python 3.11 或更新版本。' }
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage "无法运行 Python：$systemPython" }

        Write-Host '正在创建 V2 项目虚拟环境…'
        & $systemPython -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage '创建 .venv 失败。请检查 Python 安装和目录权限。' }
    }

    if (-not ((Test-Path -LiteralPath $venvPython -PathType Leaf) -and (Test-Path -LiteralPath $venvPythonw -PathType Leaf))) {
        Stop-WithMessage 'V2 虚拟环境中缺少 python.exe 或 pythonw.exe。'
    }

    & $venvPython -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 2)' 2>$null
    if ($LASTEXITCODE -eq 2) { Stop-WithMessage 'V2 虚拟环境需要 Python 3.11 或更新版本。' }
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage '无法运行 V2 虚拟环境中的 Python。' }

    function Test-RequiredPackages {
        # Windows PowerShell 5.1 strips embedded double quotes from native
        # arguments. Python single quotes survive its legacy argument passing.
        # A failed import must return false so missing packages can be installed.
        $ErrorActionPreference = 'Continue'
        & $venvPython -c "import importlib.metadata as m; import PySide6, tomlkit; raise SystemExit(0 if m.version('PySide6') == '6.11.2' and m.version('tomlkit') == '0.13.3' else 1)" >$null 2>&1
        return ($LASTEXITCODE -eq 0)
    }

    if (-not ($Check -or $CheckAdmin) -and -not (Test-RequiredPackages)) {
        Write-Host '正在安装 V2 桌面界面依赖…'
        & $venvPython -m pip install --disable-pip-version-check -r $requirementsFile
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage '依赖安装失败。请检查网络连接和 requirements.txt。' }
        if (-not (Test-RequiredPackages)) { Stop-WithMessage 'PySide6 6.11.2 或 tomlkit 0.13.3 安装后仍不可用。' }
    }

    $arguments = @('-m', 'envmanager')
    if ($Demo) { $arguments += '--demo' }
    if ($Check) { $arguments += '--check' }
    if ($CheckAdmin) { $arguments += '--check-admin' }
    if ($Config) {
        $arguments += '--config'
        $arguments += (Get-AbsoluteArgumentPath $Config)
    }
    if ($StateDir) {
        $arguments += '--state-dir'
        $arguments += (Get-AbsoluteArgumentPath $StateDir)
    }

    if ($Check -or $CheckAdmin) {
        Push-Location -LiteralPath $projectRoot
        try {
            & $venvPython @arguments
            $result = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        exit $result
    }

    $guiArguments = @($arguments | ForEach-Object {
        if ($_ -eq $arguments[0] -or $_ -eq 'envmanager' -or $_.StartsWith('--')) { $_ }
        else { Quote-NativePath $_ }
    })
    Start-Process -FilePath $venvPythonw -ArgumentList $guiArguments -WorkingDirectory $projectRoot -WindowStyle Hidden
} catch {
    Stop-WithMessage $_.Exception.Message
}
