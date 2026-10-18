#Requires -Version 7.0
<#
.SYNOPSIS
  管→真空ケースの DSMC 計算を 1 回実行する(ユーザーの DSMC コード C:\Works\20261003_DSMC を読み取り専用で使う)。

.DESCRIPTION
  1. validation\dsmc\runs\<RunName>\ を作り、ケースの inputs.tube・params.json と形状 STL をコピーする
  2. そのフォルダを作業ディレクトリにして DSMC の実行ファイルを起動する(plotfile・チェックポイントもそこに出る)
  3. 標準出力を solver.log に、実行条件と終了状態を run.json に、GPU メモリの推移を gpu.csv に記録する

  既定の実行ファイル(どちらも 2026-10-07 版。build-windows-* と SHA-256 が同一):
    cuda: C:\Works\20261003_DSMC\dist\DSMC-Studio-Windows-20261007-release\bin\dsmc-cuda.exe(curand の DLL 同梱)
    cpu : C:\Works\20261003_DSMC\dist\DSMC-Studio-Windows-20261007-release\bin\dsmc-cpu.exe(単一スレッド)
  開発中の最新ビルドを使う場合は -Exe C:\Works\20261003_DSMC\build-windows-cuda\dsmc3d.exe のように指定する。
  DSMC 側のファイルは変更しない。

.PARAMETER Case
  validation\dsmc\tube_vacuum\cases\ の下のケース名(smoke_d1, d1_q, d1_full, d10_q, d10_probe)。
.PARAMETER Backend
  cuda(既定)または cpu。
.PARAMETER Exe
  実行ファイルを明示する場合のパス。
.PARAMETER RunName
  出力フォルダ名(既定: <Case>_<日時>)。既存のフォルダは上書きしない。
.PARAMETER Override
  ParmParse の上書き。複数は ; で区切る(例: -Override 'dsmc.max_step=100;output.plot_int=50')。
  pwsh -File で呼ぶと配列の書き方(a,b)は 1 つの文字列になるので、; 区切りを使う。
.PARAMETER Restart
  チェックポイントから再開する場合のフォルダ名(実行フォルダ内の chkNNNNN)。-RunName に既存の実行フォルダを指定する。
.PARAMETER ArenaInitBytes
  AMReX の GPU メモリプールの初期確保量。既定 128 MiB(足りなければ自動で拡張する。既定値の全 VRAM の 75% を避ける)。
.PARAMETER PrepareOnly
  フォルダと入力の準備だけを行い、計算を始めない。

.EXAMPLE
  pwsh -File validation\dsmc\run_dsmc.ps1 -Case smoke_d1 -RunName smoke
.EXAMPLE
  pwsh -File validation\dsmc\run_dsmc.ps1 -Case d10_q
.EXAMPLE
  # 実行済みフォルダのチェックポイントから最大ステップを増やして再開
  pwsh -File validation\dsmc\run_dsmc.ps1 -Case d10_q -RunName d10_q_20261016_010000 -Restart chk18000 -Override 'dsmc.max_step=24000'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Case,
    [ValidateSet('cuda', 'cpu')][string]$Backend = 'cuda',
    [string]$Exe = '',
    [string]$RunName = '',
    [string[]]$Override = @(),
    [string]$Restart = '',
    [long]$ArenaInitBytes = 134217728,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
# 上書きは ; 区切りでも受け付ける(値に空白を含む指定 'bc.xlo.velocity=0 0 0' もそのまま渡す)
$Override = @($Override | ForEach-Object { $_ -split ';' } | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
$Root = $PSScriptRoot                                   # validation\dsmc
$CaseDir = Join-Path $Root "tube_vacuum\cases\$Case"
$GeomDir = Join-Path $Root 'tube_vacuum\geometry'
$RunsDir = Join-Path $Root 'runs'
$DsmcRoot = 'C:\Works\20261003_DSMC'
$DistBin = Join-Path $DsmcRoot 'dist\DSMC-Studio-Windows-20261007-release\bin'

if (-not (Test-Path (Join-Path $CaseDir 'inputs.tube'))) {
    throw "ケース $Case の入力がありません: $CaseDir(tools\make_inputs.py で生成する)"
}
if ($Exe -eq '') {
    $Exe = if ($Backend -eq 'cuda') { Join-Path $DistBin 'dsmc-cuda.exe' } else { Join-Path $DistBin 'dsmc-cpu.exe' }
}
if (-not (Test-Path $Exe)) { throw "実行ファイルがありません: $Exe" }
$Exe = (Resolve-Path $Exe).Path

# ---- 実行フォルダ
if ($RunName -eq '') { $RunName = "{0}_{1}" -f $Case, (Get-Date -Format 'yyyyMMdd_HHmmss') }
$RunDir = Join-Path $RunsDir $RunName
if ($Restart -ne '') {
    if (-not (Test-Path (Join-Path $RunDir $Restart))) { throw "チェックポイントがありません: $RunDir\$Restart" }
} else {
    if (Test-Path $RunDir) { throw "実行フォルダが既にあります(上書きしない): $RunDir" }
    New-Item -ItemType Directory -Path $RunDir | Out-Null
    Copy-Item (Join-Path $CaseDir 'inputs.tube') $RunDir
    Copy-Item (Join-Path $CaseDir 'params.json') $RunDir
    $stl = (Select-String -Path (Join-Path $CaseDir 'inputs.tube') -Pattern '^\s*surface0\.file\s*=\s*(\S+)').Matches[0].Groups[1].Value
    Copy-Item (Join-Path $GeomDir $stl) $RunDir
    $geomJson = [IO.Path]::ChangeExtension((Join-Path $GeomDir $stl), '.json')
    if (Test-Path $geomJson) { Copy-Item $geomJson $RunDir }
}

$solverArgs = @('inputs.tube') + $Override
if ($Restart -ne '') { $solverArgs += "output.restart=$Restart" }

$gpuName = ''
try { $gpuName = (& nvidia-smi '--query-gpu=name,driver_version,memory.total,memory.used' '--format=csv,noheader') -join '; ' } catch { }
$tag = if ($Restart -ne '') { "restart_$Restart" } else { 'initial' }
$runInfo = [ordered]@{
    case             = $Case
    run_dir          = $RunDir
    stage            = $tag
    backend          = $Backend
    executable       = $Exe
    executable_sha256 = (Get-FileHash -Algorithm SHA256 $Exe).Hash.ToLower()
    arguments        = $solverArgs
    amrex_the_arena_init_size = $ArenaInitBytes
    gpu_before       = $gpuName
    host             = $env:COMPUTERNAME
    started          = (Get-Date).ToString('o')
}
$runJson = Join-Path $RunDir ("run_{0}.json" -f $tag)
$runInfo | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $runJson

Write-Host "実行フォルダ : $RunDir"
Write-Host "実行ファイル : $Exe"
Write-Host "引数         : $($solverArgs -join ' ')"
if ($PrepareOnly) { Write-Host '準備のみ(-PrepareOnly)。計算は開始しない。'; return }

# ---- GPU メモリの記録(5 秒間隔)
$gpuProc = $null
if ($Backend -eq 'cuda') {
    try {
        $gpuProc = Start-Process -FilePath 'nvidia-smi' -PassThru -WindowStyle Hidden `
            -ArgumentList '--query-gpu=timestamp,memory.used,memory.total,utilization.gpu', '--format=csv', '-l', '5' `
            -RedirectStandardOutput (Join-Path $RunDir ("gpu_{0}.csv" -f $tag))
    } catch { Write-Warning "nvidia-smi による記録を開始できません: $_" }
}

$env:AMREX_THE_ARENA_INIT_SIZE = "$ArenaInitBytes"
$sw = [Diagnostics.Stopwatch]::StartNew()
Push-Location $RunDir
try {
    & $Exe @solverArgs 2>&1 | Tee-Object -FilePath (Join-Path $RunDir ("solver_{0}.log" -f $tag))
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
    $sw.Stop()
    if ($gpuProc -and -not $gpuProc.HasExited) { Stop-Process -Id $gpuProc.Id -Force }
}

$runInfo['finished'] = (Get-Date).ToString('o')
$runInfo['elapsed_s'] = [math]::Round($sw.Elapsed.TotalSeconds, 1)
$runInfo['exit_code'] = $exitCode
$runInfo | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $runJson
Write-Host ("終了コード {0}、経過 {1:N1} s。記録: {2}" -f $exitCode, $sw.Elapsed.TotalSeconds, $runJson)
exit $exitCode
