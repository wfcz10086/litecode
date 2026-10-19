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
# [2026-10-19] 卸载按安装时登记的 ssh-owner.json 恢复 LiteCode 装的 sshd/防火墙规则/sshd_config; 已有的 sshd 永不删除。
# >>> LiteCode SSH 撤销 (bootstrap.ps1 与 uninstall.ps1 各一份, 必须逐字一致, 测试校验)
function Undo-LiteCodeSsh($Owner, [string]$Tag, [string]$Backup) {
    # 只撤销记录里属于本节点的项; 本机还有其它 LiteCode 节点的公钥时共享的 sshd/规则/配置一律保留
    if (-not $Owner) { return $true }
    $keyFile = Join-Path $env:ProgramData 'ssh/administrators_authorized_keys'
    if ($Owner.key_added -and (Test-Path $keyFile)) {
        $kept = @([IO.File]::ReadAllLines($keyFile) | Where-Object { $_ -notmatch ('\s' + $Tag + '$') })
        [IO.File]::WriteAllLines($keyFile, [string[]]$kept, (New-Object Text.UTF8Encoding($false)))
    }
    if ((Test-Path $keyFile) -and @([IO.File]::ReadAllLines($keyFile) | Where-Object { $_ -match '\slitecode-node-[a-f0-9]{32}$' }).Count -gt 0) {
        return $false
    }
    $sshdConfig = Join-Path $env:ProgramData 'ssh/sshd_config'
    if ($Owner.config_backup -and (Test-Path $Backup)) { Copy-Item -LiteralPath $Backup -Destination $sshdConfig -Force }
    if ($Owner.firewall_rule) { Remove-NetFirewallRule -Name 'LiteCode-SSH-In-TCP' -ErrorAction SilentlyContinue }
    # 内置规则只在它原本不存在 (由本次装的可选功能新建) 时被禁用: 卸载可选功能会一并删除; 功能保留时维持禁用, 不放开 22 端口
    if ($Owner.capability) {
        Stop-Service -Name sshd -Force -ErrorAction SilentlyContinue
        if ((Get-WindowsCapability -Online -Name $Owner.capability).State -eq 'Installed') {
            Remove-WindowsCapability -Online -Name $Owner.capability | Out-Null
        }
    } elseif ($Owner.service) {
        Set-Service -Name sshd -StartupType $Owner.service.start_type
        if ($Owner.service.status -ne 'Running') { Stop-Service -Name sshd -Force -ErrorAction SilentlyContinue }
        elseif ($Owner.config_backup) { Restart-Service -Name sshd -ErrorAction SilentlyContinue }
    } elseif ($Owner.config_backup) { Restart-Service -Name sshd -ErrorAction SilentlyContinue }
    return $true
}
# <<< LiteCode SSH 撤销
$nodeTask = 'LiteCode-Node-' + $config.id
$updateTask = 'LiteCode-Update-' + $config.id
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
    # 更新助手属于独立任务, 先停止它, 避免卸载期间再次启动节点代理。
    if (Get-ScheduledTask -TaskName $updateTask -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $updateTask
        Unregister-ScheduledTask -TaskName $updateTask -Confirm:$false
    }
    if (Get-ScheduledTask -TaskName $nodeTask -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $nodeTask
        Unregister-ScheduledTask -TaskName $nodeTask -Confirm:$false
    }
    # 删除清理任务本身，不留新的启动项。
    if (Get-ScheduledTask -TaskName $cleanupTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $cleanupTask -Confirm:$false
    }
    # SSH 是共享的系统环境, 只撤销本节点的公钥, 不删除其它人的密钥或卸载已有 sshd。
    $tag = 'litecode-node-' + $config.id
    foreach ($keysFile in @((Join-Path $env:ProgramData 'ssh/administrators_authorized_keys'), (Join-Path $env:USERPROFILE '.ssh/authorized_keys'))) {
        if (Test-Path $keysFile) {
            $remaining = @([IO.File]::ReadAllLines($keysFile) | Where-Object { $_ -notmatch ('\s' + $tag + '$') })
            [IO.File]::WriteAllLines($keysFile, [string[]]$remaining, (New-Object Text.UTF8Encoding($false)))
        }
    }
    # 安装时登记由本节点新装/修改的 SSH 项才恢复 (未登记的旧节点保持原样); 恢复失败不阻断卸载
    $sshOwnerFile = Join-Path $Root 'ssh-owner.json'
    if (Test-Path $sshOwnerFile) {
        try {
            $sshOwner = Get-Content $sshOwnerFile -Raw | ConvertFrom-Json
            if (-not (Undo-LiteCodeSsh $sshOwner $tag (Join-Path $Root 'sshd_config.litecode-backup'))) {
                Write-Warning '本机仍有其它 LiteCode 节点使用 SSH，保留 sshd 与防火墙规则。'
            }
        } catch { Write-Warning ('恢复 SSH 环境失败（不影响卸载）：' + $_.Exception.Message) }
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
    if ((Test-Path $Root) -or (Get-ScheduledTask -TaskName $nodeTask -ErrorAction SilentlyContinue) -or (Get-ScheduledTask -TaskName $updateTask -ErrorAction SilentlyContinue) -or (Get-ScheduledTask -TaskName $cleanupTask -ErrorAction SilentlyContinue)) {
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
