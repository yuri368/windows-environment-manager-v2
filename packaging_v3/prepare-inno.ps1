[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$toolsRoot = Join-Path $PSScriptRoot 'tools'
$downloads = Join-Path $toolsRoot 'downloads'
$destination = Join-Path $toolsRoot 'inno-6.7.3'
$installer = Join-Path $downloads 'innosetup-6.7.3.exe'
$sourceUrl = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
$expectedHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
New-Item -ItemType Directory -Force -Path $downloads | Out-Null
if (-not (Test-Path -LiteralPath $installer)) {
    Invoke-WebRequest $sourceUrl -OutFile $installer
}
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'Official Inno installer SHA256 mismatch.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Pyrsys B\.V\.') {
    throw "Official installer signature failed: $($signature.Status)"
}
# Official /PORTABLE=1 mode disables uninstall registration, associations and icons.
# This prepares the compiler only; never execute the generated V3 installer here.
if (-not (Test-Path -LiteralPath (Join-Path $destination 'ISCC.exe'))) {
    $tempDir = Join-Path $toolsRoot 'tmp'
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $priorTemp, $priorTmp = $env:TEMP, $env:TMP
    try {
        $env:TEMP = $tempDir
        $env:TMP = $tempDir
        $arguments = @('/PORTABLE=1', '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS', '/TASKS=""', "/DIR=`"$destination`"", "/LOG=`"$(Join-Path $toolsRoot 'prepare-inno.log')`"")
        $process = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
        if ($process.ExitCode -ne 0) { throw "Portable Inno preparation failed: $($process.ExitCode)" }
    } finally {
        $env:TEMP, $env:TMP = $priorTemp, $priorTmp
    }
}
$compiler = Get-Item -LiteralPath (Join-Path $destination 'ISCC.exe')
[ordered]@{
    sourceUrl=$sourceUrl
    installerSha256=$expectedHash
    installerSignature=$signature.Status.ToString()
    signer=$signature.SignerCertificate.Subject
    signerThumbprint=$signature.SignerCertificate.Thumbprint
    compilerPath=$compiler.FullName
    compilerVersion=$compiler.VersionInfo.FileVersion
    compilerSha256=(Get-FileHash -LiteralPath $compiler.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    mode='official /PORTABLE=1 /CURRENTUSER; no elevation; compiler preparation only'
} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $toolsRoot 'inno-provenance.json')
Write-Host "Official portable Inno compiler ready: $($compiler.FullName)"
