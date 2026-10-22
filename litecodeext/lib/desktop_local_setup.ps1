# 桌面版"操控本机" 第一步 (管理员): 装 OpenSSH Server 并设为开机自启, 只放行本机/局域网。
# 由 lib/desktop_local_node.py 嵌进托盘/安装程序以管理员运行的脚本; 随后同一脚本再跑节点接入 (windows_node_bootstrap.ps1)。
# 已有 sshd 时只确保开机自启并运行, 不改它的配置与防火墙; 新装时只允许密钥登录。
# 来源: 离线包优先 (安装包里自带的微软官方 Win32-OpenSSH, 校验 sha256 + 微软签名), 不行再用 Windows 可选功能。
param([string]$ZipPath = '', [Parameter(Mandatory=$true)][string]$ZipSha256)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function LcSha256([byte[]]$d) { $x = [Security.Cryptography.SHA256]::Create(); try { ([BitConverter]::ToString($x.ComputeHash($d))).Replace('-','').ToLower() } finally { $x.Dispose() } }
$marker = Join-Path $env:ProgramData 'LiteCode/openssh-installed-by-litecode.json'
function Add-CapabilityWithTimeout([string]$Name, [int]$Seconds = 180) {
    # Add-WindowsCapability 要连 Windows Update; 开着代理/公司网络时能一直卡着不返回 (2026-10-22 用户机), 限时
    $job = Start-Job -ScriptBlock { param($n) Add-WindowsCapability -Online -Name $n -ErrorAction Stop | Out-Null } -ArgumentList $Name
    if (-not (Wait-Job $job -Timeout $Seconds)) {
        Stop-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force -ErrorAction SilentlyContinue
        throw ('Windows 可选功能安装 ' + $Seconds + ' 秒没完成（多半连不上 Windows Update）')
    }
    try { Receive-Job $job -ErrorAction Stop | Out-Null } finally { Remove-Job $job -Force -ErrorAction SilentlyContinue }
}
$existing = Get-Service -Name sshd -ErrorAction SilentlyContinue
$builtinBefore = [bool](Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)
$via = ''
if (-not $existing) {
    $dest = Join-Path $env:ProgramFiles 'OpenSSH'
    if ($ZipPath -and (Test-Path -LiteralPath $ZipPath) -and -not (Test-Path (Join-Path $dest 'sshd.exe'))) {
        # 先读进内存再校验, 校验通过的字节才落到管理员临时目录解压 (源文件在用户可写目录, 不直接执行它)
        $bytes = [IO.File]::ReadAllBytes($ZipPath)
        if ((LcSha256 $bytes) -ne $ZipSha256.ToLower()) { Write-Warning '自带 OpenSSH 安装包校验不符, 改用 Windows 可选功能' }
        else {
            $work = Join-Path $env:TEMP ('litecode-openssh-' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $work -Force | Out-Null
            try {
                $zip = Join-Path $work 'openssh.zip'
                [IO.File]::WriteAllBytes($zip, $bytes)
                Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
                $src = Join-Path $work 'OpenSSH-Win64'
                $sig = Get-AuthenticodeSignature -FilePath (Join-Path $src 'sshd.exe')
                if ($sig.Status -ne 'Valid' -or [string]$sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw '自带 OpenSSH 的 sshd.exe 签名校验失败' }
                Move-Item -LiteralPath $src -Destination $dest
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dest 'install-sshd.ps1') | Out-Null
                if (Get-Service -Name sshd -ErrorAction SilentlyContinue) { $via = 'portable' }
            } catch { Write-Warning ('自带 OpenSSH 安装失败: ' + $_.Exception.Message) }
            finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
        }
    }
    if (-not $via) {
        $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' | Select-Object -First 1
        if (-not $cap) { throw '本机没有 OpenSSH Server 可选功能, 自带安装包也没装上' }
        Add-CapabilityWithTimeout $cap.Name 180
        if ((Get-WindowsCapability -Online -Name $cap.Name).State -ne 'Installed') { throw 'Windows 可选功能装不上 OpenSSH Server' }
        $via = 'capability:' + $cap.Name
    }
    # 新装的 sshd 只允许密钥登录 (LiteCode 用接入时生成的密钥, 不需要 Windows 密码)
    Start-Service -Name sshd   # 首次启动才会生成 sshd_config 与主机密钥
    $cfg = Join-Path $env:ProgramData 'ssh/sshd_config'
    if (Test-Path $cfg) {
        $text = [regex]::Replace([IO.File]::ReadAllText($cfg), '(?m)^\s*#?\s*PasswordAuthentication\s+.*$', '')
        [IO.File]::WriteAllText($cfg, "PasswordAuthentication no`n" + $text, (New-Object Text.UTF8Encoding($false)))
    }
    # 防火墙: 新建规则只放行本地子网 (含 WSL 子系统所在网段); 可选功能自带的放行所有地址的规则停用
    if (-not $builtinBefore) { Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule }
    if (-not (Get-NetFirewallRule -Name 'LiteCode-SSH-In-TCP' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'LiteCode-SSH-In-TCP' -DisplayName 'LiteCode SSH (LocalSubnet)' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -RemoteAddress LocalSubnet | Out-Null
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $marker) | Out-Null
    @{via=$via; installed_at=(Get-Date).ToString('s'); firewall_rule='LiteCode-SSH-In-TCP'} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 $marker
}
Set-Service -Name sshd -StartupType Automatic
# 新装的要重启让 PasswordAuthentication no 生效; 已有的只在没运行时启动, 不打断别人正在用的 SSH 会话
if ($via) { Restart-Service -Name sshd } elseif ((Get-Service -Name sshd).Status -ne 'Running') { Start-Service -Name sshd }
if ((Get-Service -Name sshd).Status -ne 'Running') { throw 'OpenSSH Server 没有启动成功' }
Write-Host ('OpenSSH Server 已就绪 (开机自启)' + $(if ($via) { '，本次新装: ' + $via } else { '，沿用本机已有的 sshd' }))
