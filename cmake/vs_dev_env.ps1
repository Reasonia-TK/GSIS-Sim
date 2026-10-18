<#
.SYNOPSIS
  Visual Studio の開発者環境(x64)を、現在の PowerShell セッションに取り込む。

.DESCRIPTION
  CMakePresets.json のプリセットは Ninja + MSVC(cl.exe)を使うため、cl・link・ninja が
  PATH にある開発者環境で cmake を実行する必要がある。このスクリプトをドットソースすると、
  vswhere で見つけた Visual Studio(C++ の x64 ツールを含むもの)の環境を取り込む。
  すでに x64 の開発者環境にいる場合は何もしない。

.PARAMETER InstallPath
  使う Visual Studio のインストール先。省略すると、C++ の x64 ツールを含む最新の版を使う。
  例: 'C:\BuildTools'(VS 2022 Build Tools)

.EXAMPLE
  . .\cmake\vs_dev_env.ps1
  cmake --preset release
  cmake --build --preset release
  ctest --preset release
#>
param(
    [string]$InstallPath = ''
)

$ErrorActionPreference = 'Stop'

if (-not $InstallPath -and $env:VSCMD_ARG_TGT_ARCH -eq 'x64' -and (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    Write-Host "開発者環境(x64)は設定済み: $env:VSINSTALLDIR"
    return
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $vswhere) {
    # VsDevCmd が内部で vswhere.exe を呼ぶので PATH に足しておく(無いと無害な警告が出る)
    $installerDir = Split-Path -Parent $vswhere
    if (-not (($env:PATH -split ';') -contains $installerDir)) {
        $env:PATH = $installerDir + ';' + $env:PATH
    }
}

if (-not $InstallPath) {
    if (-not (Test-Path -LiteralPath $vswhere)) {
        throw 'vswhere.exe が見つかりません。C++ のツールを含む Visual Studio をインストールしてください。'
    }
    $InstallPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $InstallPath) {
        throw 'C++ の x64 ツールを含む Visual Studio が見つかりません。'
    }
}

$devShell = Join-Path $InstallPath 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll'
if (Test-Path -LiteralPath $devShell) {
    Import-Module $devShell
    Enter-VsDevShell -VsInstallPath $InstallPath -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
} else {
    # DevShell モジュールがない古い版: vcvars64.bat を実行して環境変数を写す
    $vcvars = Join-Path $InstallPath 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path -LiteralPath $vcvars)) {
        throw "vcvars64.bat が見つかりません: $vcvars"
    }
    $lines = & $env:ComSpec /d /s /c ('call "' + $vcvars + '" >nul 2>&1 && set')
    foreach ($line in $lines) {
        if ($line -match '^([^=]+)=(.*)$') {
            Set-Item -Path ("env:" + $Matches[1]) -Value $Matches[2]
        }
    }
}

if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw "開発者環境の設定に失敗しました(cl.exe が見つかりません): $InstallPath"
}
if (-not (Get-Command ninja.exe -ErrorAction SilentlyContinue)) {
    Write-Warning 'ninja.exe が PATH にありません。Visual Studio の「C++ CMake tools for Windows」を入れてください。'
}
Write-Host "開発者環境(x64)を設定しました: $InstallPath"
