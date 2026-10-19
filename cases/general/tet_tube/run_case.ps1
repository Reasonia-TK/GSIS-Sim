param([string]$Case,[string]$Name,[string]$Mode='gsis',[int]$Iterations=200,[string[]]$Overrides=@(),[string]$Exe='runs\phase8\s1\gsis-solver-boundary.exe',[string]$InitFrom='', [string]$SaveInit='', [double]$Tolerance=-1)
$env:TEMP='C:\Works\20261015_GSIS-PBC\build\tmp_s'
$env:TMP=$env:TEMP
$env:OMP_NUM_THREADS='8'
$outDir=Join-Path 'runs\phase8\s1' $Name
New-Item -ItemType Directory -Force $outDir | Out-Null
# 中止した実験は、GPUを確保する前に記録して戻る。
if (Test-Path (Join-Path 'runs\phase8\s1' ('CANCEL_' + $Name))) {
 '取り止め: GPU計算は開始しなかった' | Set-Content -Encoding UTF8 (Join-Path $outDir 'cancelled.txt')
 return
}
$lockDir='C:\Works\20261015_GSIS-PBC\runs\.gpu_lock'
$acquired=$false
try {
 while (-not $acquired) {
  try { New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null; $acquired=$true }
  catch { if (-not (Test-Path -LiteralPath $lockDir)) { throw }; Start-Sleep -Seconds 5 }
 }
 "S: $Name ($Mode), $(Get-Date -Format o)" | Set-Content -Encoding UTF8 (Join-Path $lockDir 'owner.txt')
 $solverArgs=@('run',$Case,'--out',$outDir,'--mode',$Mode,'--max-iter',$Iterations,'--threads','8','--print-every','10','--no-vtu','--cells-csv')
 if ($Tolerance -gt 0) { $solverArgs+=@('--tol',$Tolerance) }
 if ($InitFrom) { $solverArgs+=@('--init-from',$InitFrom) }
 if ($SaveInit) { $solverArgs+=@('--save-init',$SaveInit) }
 foreach ($item in $Overrides) { $solverArgs+=@('--set',$item) }
 Get-FileHash -Algorithm SHA256 $Case | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $outDir 'case_file.json')
 Get-FileHash -Algorithm SHA256 $Exe | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $outDir 'executable.json')
 $solverArgs | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $outDir 'arguments.json')
 & $Exe @solverArgs 1> (Join-Path $outDir 'stdout.log') 2> (Join-Path $outDir 'stderr.log')
 $LASTEXITCODE | Set-Content (Join-Path $outDir 'exit_code.txt')
} finally {
 if ($acquired) {
  if ([System.IO.Path]::GetFullPath($lockDir) -ne 'C:\Works\20261015_GSIS-PBC\runs\.gpu_lock') { throw 'GPUロックのパスが不正です' }
  Remove-Item -LiteralPath (Join-Path $lockDir 'owner.txt') -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $lockDir -Force
 }
}
