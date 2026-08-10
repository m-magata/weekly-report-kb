<#
.SYNOPSIS
    週報自動取り込み（run_auto_upload.bat）を Windows タスクスケジューラに登録する。

.DESCRIPTION
    タスク名 : WeeklyReportAutoUpload
    トリガー : 毎週火曜 9:00
    実行内容 : <プロジェクトルート>\run_auto_upload.bat

    設定:
      - StartWhenAvailable      = $true  （時刻に実行できなかった場合、すぐに開始する）
      - AllowStartIfOnBatteries = $true  （バッテリー駆動時も実行する）
      - DontStopIfGoingOnBatteries = $true （実行中に電源からバッテリーへ切り替わっても停止しない）

.NOTES
    ■ 管理者権限について
      Register-ScheduledTask はタスクの登録に管理者権限を必要とする場合があります。
      「アクセスが拒否されました」("Access is denied") で失敗した場合は、
      PowerShell を「管理者として実行」してから再実行してください。

    ■ ログオン種別を Interactive にしている理由（重要）
      取り込み対象の Q:\共有\... はネットワークドライブ
      （\\DirectCloud\株式会社ランドロームジャパン）です。
      マップされたドライブレターはユーザーセッションごとに割り当てられるため、
      「ユーザーがログオンしているかどうかにかかわらず実行する」(S4U / パスワード保存)
      で登録すると Q: が見えず、auto_upload.py は
      「ERROR: 対象フォルダが見つかりません」で終了します。
      そのため -LogonType Interactive（ログオン中のみ実行）で登録しています。

      火曜9:00にPCがログオンされていない場合は、StartWhenAvailable により
      次にログオンしたタイミングで自動実行されます。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\register_task.ps1
#>

$ErrorActionPreference = 'Stop'

$TaskName = 'WeeklyReportAutoUpload'

# このスクリプトが置かれているフォルダ＝プロジェクトルート
$ProjectRoot = $PSScriptRoot
$BatPath     = Join-Path $ProjectRoot 'run_auto_upload.bat'

if (-not (Test-Path -LiteralPath $BatPath)) {
    throw "run_auto_upload.bat が見つかりません: $BatPath"
}

Write-Host "プロジェクトルート : $ProjectRoot"
Write-Host "実行するバッチ     : $BatPath"
Write-Host ""

# --- タスクの構成要素 ---------------------------------------------------

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday -At '09:00'

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# ネットワークドライブ Q: を参照するため、ログオン中のみ実行する
$Principal = New-ScheduledTaskPrincipal `
    -UserId (Get-CimInstance Win32_ComputerSystem).UserName `
    -LogonType Interactive `
    -RunLevel Limited

# --- 登録 ---------------------------------------------------------------

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Write-Host "既存のタスク '$TaskName' を上書きします。" -ForegroundColor Yellow
}

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $Action `
    -Trigger     $Trigger `
    -Settings    $Settings `
    -Principal   $Principal `
    -Description '販売部週報を共有フォルダから自動取り込みする（毎週火曜 9:00）' `
    -Force | Out-Null

Write-Host ""
Write-Host "タスク '$TaskName' を登録しました。" -ForegroundColor Green
Write-Host ""

# --- 登録結果の確認 -----------------------------------------------------

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host "状態         : $($task.State)"
Write-Host "次回実行予定 : $($info.NextRunTime)"
Write-Host ""
Write-Host "手動でテスト実行する場合:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host "実行結果の確認:"
Write-Host "    Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "タスクを削除する場合:"
Write-Host "    Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
