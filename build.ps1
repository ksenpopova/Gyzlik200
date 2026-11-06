param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$OutputDirectory = (Join-Path $env:LOCALAPPDATA 'AudioFlowBuild')
)
$ErrorActionPreference = 'Stop'
$Python = (Resolve-Path -LiteralPath $Python).Path
$previousPath = $env:Path
Push-Location -LiteralPath $PSScriptRoot
try {
    # Prevent unrelated SDK and bundled tool DLLs on PATH from entering the release.
    $env:Path = "$(Split-Path -Parent $Python);$env:SystemRoot\System32;$env:SystemRoot"
    $buildArgs = @(
        '--noconfirm', '--name', 'AudioFlow', '--onedir', '--console', '--hide-console', 'hide-early',
        '--distpath', (Join-Path $OutputDirectory 'dist'),
        '--workpath', (Join-Path $OutputDirectory 'build'), '--specpath', $OutputDirectory,
        '--add-data', "$PSScriptRoot/icons:icons", '--add-data', "$PSScriptRoot/image:image",
        '--hidden-import', 'transliterate.contrib.languages.ru.translit_language_pack',
        '--exclude-module', 'librosa', '--exclude-module', 'numba', '--exclude-module', 'matplotlib',
        '--exclude-module', 'sklearn', '--exclude-module', 'pandas', 'main (3).py'
    )
    & $Python -m PyInstaller @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally {
    $env:Path = $previousPath
    Pop-Location
}
