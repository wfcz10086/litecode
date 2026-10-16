# 完全卸载本节点：独立清理任务避免代理删除正在使用的 Python。
[CmdletBinding()]
param([string]$Root, [string]$NodeId, [switch]$Schedule)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Security
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请在安装时的 Windows 用户桌面，以管理员身份打开 PowerShell 运行卸载。'
}
$base = Join-Path $env:LOCALAPPDATA 'LiteCodeNodes'
if (-not $Root) {
    if ($NodeId -notmatch '^[a-f0-9]{32}$') { throw '节点 ID 无效' }
    foreach ($directory in @(Get-ChildItem $base -Directory -ErrorAction SilentlyContinue)) {
        $file = Join-Path $directory.FullName 'node.json'
        if (Test-Path $file) {
            $candidate = Get-Content $file -Raw | ConvertFrom-Json
            if ($candidate.id -eq $NodeId) { $Root=$directory.FullName; break }
        }
    }
    if (-not $Root) { throw '当前用户下未发现该节点，请使用安装时的 Windows 用户。' }
}
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
if ((Split-Path $Root -Parent) -ne [IO.Path]::GetFullPath($base) -or (Split-Path $Root -Leaf) -notmatch '^[a-f0-9]{64}$') {
    throw '拒绝删除非 LiteCode 节点目录'
}
$config = Get-Content (Join-Path $Root 'node.json') -Raw | ConvertFrom-Json
if ($config.id -notmatch '^[a-f0-9]{32}$' -or ($NodeId -and $config.id -ne $NodeId)) { throw '节点身份不匹配' }
$nodeTask = 'LiteCode-Node-' + $config.id
$cleanupTask = 'LiteCode-Uninstall-' + $config.id
if ($Schedule) {
    $source = if ($PSCommandPath) { Get-Content $PSCommandPath -Raw } else { $MyInvocation.MyCommand.Definition }
    $script = Join-Path $env:TEMP ($cleanupTask + '.ps1')
    $source | Set-Content -Encoding UTF8 $script
    & icacls.exe $script /inheritance:r /grant:r ($identity.Name + ':F') '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '无法保护卸载脚本权限' }
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$script+'" -Root "'+$Root+'"')
    $owner = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $cleanupTask -Action $action -Principal $owner -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $cleanupTask
    Write-Host '已启动独立清理任务；Web 收到清理回执后才显示已卸载。'
    return
}
# 凭据只在内存解密，删除配置后仍能回传最终清理结果。
$token = if ($config.token_dpapi) {
    [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect(
        [Convert]::FromBase64String($config.token_dpapi), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser))
} else { $config.token }
$headers = @{Authorization=('Bearer ' + $token)}
$device = $config.server.TrimEnd('/') + '/api/windows/device/' + $config.id
try {
    if (Get-ScheduledTask -TaskName $nodeTask -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $nodeTask
        Unregister-ScheduledTask -TaskName $nodeTask -Confirm:$false
    }
    # 删除清理任务本身，不留新的启动项。
    if (Get-ScheduledTask -TaskName $cleanupTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $cleanupTask -Confirm:$false
    }
    # 官方 Python 用户安装可能注册卸载项；先调用其安装器移除注册信息。
    $installer = Join-Path $Root 'python-installer.exe'
    $ownerFile = Join-Path $Root 'runtime-owner.json'
    $runtimeOwner = if (Test-Path $ownerFile) { (Get-Content $ownerFile -Raw | ConvertFrom-Json).kind } else { '' }
    if ($runtimeOwner -eq 'installer') {
        $signature = Get-AuthenticodeSignature $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw '运行环境安装器签名异常' }
        $process = Start-Process $installer -ArgumentList @('/uninstall','/quiet') -Wait -PassThru
        if ($process.ExitCode -notin @(0,1605,3010)) { throw '专属 Python 运行环境卸载失败' }
    }
    for ($attempt=0; $attempt -lt 15; $attempt++) {
        try { Remove-Item -LiteralPath $Root -Recurse -Force; break } catch { if ($attempt -eq 14) { throw }; Start-Sleep -Seconds 1 }
    }
    if ((Test-Path $Root) -or (Get-ScheduledTask -TaskName $nodeTask -ErrorAction SilentlyContinue) -or (Get-ScheduledTask -TaskName $cleanupTask -ErrorAction SilentlyContinue)) {
        throw '仍有目录或启动任务残留，不能确认完全卸载'
    }
    if ($PSCommandPath -and (Test-Path $PSCommandPath)) { Remove-Item -LiteralPath $PSCommandPath -Force }
    # 本机最后一个节点卸载后不留空的上级目录；仍有其他节点时不动
    try { if (-not (Get-ChildItem -LiteralPath $base -Force | Select-Object -First 1)) { Remove-Item -LiteralPath $base -Force } } catch {}
} catch {
    $message = $_.Exception.Message
    try { Invoke-RestMethod -Method Post -Uri "$device/status" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body (@{state='uninstall_failed';error=$message} | ConvertTo-Json -Compress) | Out-Null } catch {}
    throw $message
}
try {
    Invoke-RestMethod -Method Post -Uri "$device/uninstalled" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body '{}' | Out-Null
    Write-Host '本机已完全卸载，后台已确认并清除设备凭据。'
} catch {
    Write-Host '本机已完全卸载；服务器暂未确认，请在 Web 查看状态。已撤销的设备不会接受回执。'
}
