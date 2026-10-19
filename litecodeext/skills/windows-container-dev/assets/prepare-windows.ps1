# 新建的专用测试机 OEM 入口；包来自宿主缓存，节点接入命令在首次登录后另行投递。
param([string]$UserName = 'litecode')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$taskName = 'LiteCode-Fresh-Prepare'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.User.Value -eq 'S-1-5-18') {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -UserName "' + $UserName + '"')
    $owner = New-ScheduledTaskPrincipal -UserId ($env:COMPUTERNAME + '\' + $UserName) -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $owner -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $owner.UserId) -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    return
}
Start-Transcript -Path 'C:\OEM\install.log' -Append | Out-Null
try {
    if ($env:USERNAME -ne $UserName -or (Get-Process -Id $PID).SessionId -eq 0) {
        throw 'OEM must run in the intended interactive Windows account'
    }
    tzutil.exe /s 'China Standard Time'
    $shared = $null
    for ($i=0; $i -lt 60 -and -not $shared; $i++) {
        foreach ($candidate in @('Z:\', '\\host.lan\Data\')) {
            if (Test-Path $candidate) { $shared=$candidate; break }
        }
        if (-not $shared) { Start-Sleep -Seconds 2 }
    }
    if (-not $shared) { throw 'Host shared directory is not available' }
    $proof = @{state='installing-apps';computer=$env:COMPUTERNAME;user=$identity.Name;
               session=(Get-Process -Id $PID).SessionId;os=(Get-CimInstance Win32_OperatingSystem).Caption;
               boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')}
    $proof | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $shared 'guest-ready.json')
    foreach ($package in @(
        @{name='git.exe';subject='Johannes Schindelin|Git for Windows|Open Source Developer';arguments='/VERYSILENT /NORESTART /NOCANCEL /SP-'},
        @{name='vscode.exe';subject='Microsoft Corporation';arguments='/VERYSILENT /NORESTART /MERGETASKS=!runcode,addtopath'}
    )) {
        $file = Join-Path 'C:\OEM\pkgs' $package.name
        $signature = Get-AuthenticodeSignature $file
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $package.subject) {
            throw ('Installer signature rejected: ' + $package.name)
        }
        $process = Start-Process -FilePath $file -ArgumentList $package.arguments -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw ('Installer failed: ' + $package.name + ' code=' + $process.ExitCode) }
    }
    $proof.state='waiting-for-node-install'
    $proof | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $shared 'guest-ready.json')
    $join = Join-Path $shared 'join.ps1'
    for ($i=0; $i -lt 900 -and -not (Test-Path $join); $i++) { Start-Sleep -Seconds 2 }
    if (-not (Test-Path $join)) { throw 'Timed out waiting for fresh node enrollment command' }
    & $join
    if ($LASTEXITCODE -ne 0) { throw 'Node installation failed; inspect controller stage and OEM log' }
    $proof.state='node-install-finished'
    $proof | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $shared 'guest-ready.json')
    Remove-Item $join -Force
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
} catch {
    if ($shared) {
        @{state='error';computer=$env:COMPUTERNAME;error=$_.Exception.Message} | ConvertTo-Json |
            Set-Content -Encoding UTF8 (Join-Path $shared 'guest-error.json')
    }
    throw
} finally { Stop-Transcript | Out-Null }
