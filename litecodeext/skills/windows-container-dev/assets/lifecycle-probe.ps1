# Run only through LiteCode on the dedicated Windows test VM.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Status','Crash','PrepareReboot','StartOffline','OfflineWorker')][string]$Mode,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9-]{1,48}$')][string]$RunId,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$NodeId,
    [int]$AdapterIndex = 0
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'WIN-JM2PTI2AMKB') { throw 'This probe only supports the dedicated test VM' }
$folder = Join-Path $env:ProgramData ('LiteCodeLifecycle\' + $RunId)
$taskName = 'LiteCode-Node-' + $NodeId
$prefix = 'LiteCode-Test-' + $RunId
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
function Save([string]$Name, $Value) {
    [IO.File]::WriteAllText((Join-Path $folder ($Name + '.json')), ($Value | ConvertTo-Json -Depth 8 -Compress), $utf8)
}
function Quote([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
function Register-Rescue([string]$Name, [datetime]$At, [string]$Code) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Code))
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument ('-NoProfile -NonInteractive -EncodedCommand ' + $encoded)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $action -Principal $principal -Settings $settings -Trigger (New-ScheduledTaskTrigger -Once -At $At) | Out-Null
}
if (-not (Test-Path $folder)) {
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $folder /inheritance:r /grant:r ('*' + $sid + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot secure probe evidence directory' }
}
function Snapshot {
    $task = Get-ScheduledTask -TaskName $taskName
    $agent = [regex]::Match($task.Actions[0].Arguments, '"([^"\r\n]*agent\.py)"').Groups[1].Value
    if (-not $agent -or -not (Test-Path -LiteralPath $agent)) { throw 'Cannot identify this node agent file' }
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like ('*' + $agent + '*') } |
        ForEach-Object { @{pid=$_.ProcessId; parent=$_.ParentProcessId; session=$_.SessionId; created=$_.CreationDate.ToUniversalTime().ToString('o')} })
    [xml]$xml = Export-ScheduledTask -TaskName $taskName
    $sshd = Get-Service sshd
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    $os = Get-CimInstance Win32_OperatingSystem
    return @{computer=$env:COMPUTERNAME; utc=[datetime]::UtcNow.ToString('o'); bootUtc=$os.LastBootUpTime.ToUniversalTime().ToString('o'); uptimeSeconds=[math]::Round((New-TimeSpan -Start $os.LastBootUpTime -End (Get-Date)).TotalSeconds);
        task=$taskName; enabled=$task.Settings.Enabled; state=[string]$task.State; agent=$agent; processes=$processes; agentSha256=(Get-FileHash -LiteralPath $agent -Algorithm SHA256).Hash.ToLower(); sshd=[string]$sshd.Status; sshdStart=[string]$sshd.StartType;
        policy=[string]$xml.Task.Settings.MultipleInstancesPolicy; restartCount=$task.Settings.RestartCount; restartInterval=$task.Settings.RestartInterval; repeatIntervals=@($task.Triggers | ForEach-Object {$_.Repetition.Interval} | Where-Object {$_}); lastRun=$info.LastRunTime.ToUniversalTime().ToString('o'); nextRun=$info.NextRunTime.ToUniversalTime().ToString('o'); lastTaskResult=$info.LastTaskResult}
}
if ($Mode -eq 'Status') {
    $result = @{snapshot=(Snapshot); records=@()}
    foreach ($file in Get-ChildItem $folder -Filter '*.json' -File) {
        $data = [IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json
        if ($file.Name -in @('crash-before.json','reboot-before.json')) { $data = @{utc=$data.utc; bootUtc=$data.bootUtc; pids=@($data.processes | ForEach-Object {$_.pid})} }
        $result.records += @{name=$file.Name; data=$data}
    }
    $result | ConvertTo-Json -Depth 10 -Compress
    exit 0
}
if ($Mode -eq 'Crash') {
    $before = Snapshot
    if (-not $before.processes.Count) { throw 'No matching agent process to test' }
    Save 'crash-before' $before
    $rescue = $prefix + '-CrashRescue'
    $rescueFile = Join-Path $folder 'crash-rescue.json'
    $code = '$used=$false; if((Get-ScheduledTask -TaskName ' + (Quote $taskName) + ').State -ne "Running") { Start-ScheduledTask -TaskName ' + (Quote $taskName) + '; $used=$true }; ' +
        '[IO.File]::WriteAllText(' + (Quote $rescueFile) + ',(@{utc=[datetime]::UtcNow.ToString("o");used=$used}|ConvertTo-Json -Compress)); Unregister-ScheduledTask -TaskName ' + (Quote $rescue) + ' -Confirm:$false'
    Register-Rescue $rescue (Get-Date).AddSeconds(150) $code
    foreach ($process in $before.processes) { Stop-Process -Id $process.pid -Force -ErrorAction SilentlyContinue }
    @{crash_dispatched=$true; before=$before; rescueTask=$rescue; rescueAfterSeconds=150} | ConvertTo-Json -Depth 8 -Compress
    exit 0
}
if ($Mode -eq 'PrepareReboot') {
    $login = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name AutoAdminLogon,DefaultUserName
    if ($login.AutoAdminLogon -ne '1' -or $login.DefaultUserName -ne 'litecode') { throw 'Existing automatic login is not configured for test user' }
    if (@(Get-Process msiexec -ErrorAction SilentlyContinue).Count) { throw 'An installer is running; do not restart' }
    $before = Snapshot
    Save 'reboot-before' $before
    $name = $prefix + '-Reboot'
    $record = Join-Path $folder 'reboot-fired.json'
    $code = '[IO.File]::WriteAllText(' + (Quote $record) + ',(@{utc=[datetime]::UtcNow.ToString("o")}|ConvertTo-Json -Compress)); ' +
        'Unregister-ScheduledTask -TaskName ' + (Quote $name) + ' -Confirm:$false; & shutdown.exe /r /t 5 /d p:4:1'
    Register-Rescue $name (Get-Date).AddSeconds(180) $code
    @{reboot_scheduled=$true; afterSeconds=180; task=$name; before=$before} | ConvertTo-Json -Depth 8 -Compress
    exit 0
}
if ($Mode -eq 'StartOffline') {
    $routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Where-Object { (Get-NetAdapter -InterfaceIndex $_.InterfaceIndex).Status -eq 'Up' } | Select-Object -ExpandProperty InterfaceIndex -Unique)
    if ($routes.Count -ne 1) { throw 'Expected one active IPv4 gateway interface' }
    $AdapterIndex = [int]$routes[0]
    $adapter = Get-NetAdapter -InterfaceIndex $AdapterIndex
    $name = $prefix + '-Offline'
    $backup = $prefix + '-NetworkRescue'
    $code = 'Get-NetAdapter -InterfaceIndex ' + $AdapterIndex + ' | Enable-NetAdapter -Confirm:$false; Unregister-ScheduledTask -TaskName ' + (Quote $backup) + ' -Confirm:$false'
    Register-Rescue $backup (Get-Date).AddMinutes(65) $code
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Mode OfflineWorker -RunId ' + $RunId + ' -NodeId ' + $NodeId + ' -AdapterIndex ' + $AdapterIndex)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 70) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
    Save 'offline-receipt' @{utc=[datetime]::UtcNow.ToString('o'); adapter=$adapter.Name; index=$AdapterIndex; durationSeconds=3605; workerTask=$name; rescueTask=$backup; delaySeconds=20}
    Start-ScheduledTask -TaskName $name
    [IO.File]::ReadAllText((Join-Path $folder 'offline-receipt.json'))
    exit 0
}
if ($Mode -eq 'OfflineWorker') {
    if ($AdapterIndex -le 0) { throw 'Missing adapter index' }
    Start-Sleep -Seconds 20
    $timer = [Diagnostics.Stopwatch]::new()
    $started = [datetime]::UtcNow
    $failure = $null
    try {
        Get-NetAdapter -InterfaceIndex $AdapterIndex | Disable-NetAdapter -Confirm:$false
        $timer.Start()
        $started = [datetime]::UtcNow
        Save 'offline-started' @{utc=$started.ToString('o'); adapterIndex=$AdapterIndex; requestedSeconds=3605}
        while ($timer.Elapsed.TotalSeconds -lt 3605) { Start-Sleep -Seconds 1 }
    } catch { $failure = $_.Exception.Message }
    finally {
        Get-NetAdapter -InterfaceIndex $AdapterIndex | Enable-NetAdapter -Confirm:$false
        $timer.Stop()
        Save 'offline-restored' @{startUtc=$started.ToString('o'); restoredUtc=[datetime]::UtcNow.ToString('o'); elapsedSeconds=$timer.Elapsed.TotalSeconds; error=$failure; adapterStatus=[string](Get-NetAdapter -InterfaceIndex $AdapterIndex).Status}
        Unregister-ScheduledTask -TaskName ($prefix + '-NetworkRescue') -Confirm:$false -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName ($prefix + '-Offline') -Confirm:$false -ErrorAction SilentlyContinue
    }
}
