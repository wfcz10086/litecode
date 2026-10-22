# Windows 节点接入：主动回连 + OpenSSH 后台运维, SSH 私钥只保留在控制端。
# 安装命令已先核对本脚本的 sha256 再执行; -KeyFingerprint 为控制端签名公钥指纹 (同样来自带外复制的安装命令)。
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Server, [Parameter(Mandatory=$true)][string]$Code,
      [string]$KeyFingerprint = '', [string]$SshPublicKey = '')
$ErrorActionPreference = 'Stop'
# [2026-10-20] PowerShell 5.1 的 Invoke-WebRequest 进度条会把下载拖慢几十倍 (真机装 Python 卡在"正在写入 Web 请求")
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请在要接入的用户桌面，以管理员身份打开 PowerShell 后执行。'
}
if ($SshPublicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+ litecode-node-[a-f0-9]{32}$') {
    throw '接入命令缺少有效 SSH 公钥，请更新控制端并重新生成命令。无需提供 Windows 密码。'
}
$Server = $Server.TrimEnd('/')
function Get-Sha256Hex([byte[]]$Data) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($Data))).Replace('-','').ToLower() } finally { $hasher.Dispose() }
}
function Get-Bytes([string]$Uri, $Headers) {
    $response = Invoke-WebRequest $Uri -Headers $Headers -UseBasicParsing
    return ,[byte[]]$response.RawContentStream.ToArray()
}
$signingKey = $null
if ($KeyFingerprint) {
    # 消费接入码之前先确认签名公钥与安装命令中的指纹一致, 不一致就停止, 不改动本机任何东西
    $signingKey = (Invoke-RestMethod "$Server/api/windows/signing-key").key
    $actual = Get-Sha256Hex ([Text.Encoding]::UTF8.GetBytes([string]$signingKey.alg + ':' + $signingKey.n + ':' + $signingKey.e))
    if ($signingKey.alg -ne 'RSA-PKCS1v15-SHA256' -or $actual -ne $KeyFingerprint.ToLower()) {
        throw '签名公钥校验失败：控制端返回的公钥与安装命令中的指纹不一致，可能在网络途中被替换。已停止安装，请让管理员重新生成安装命令。'
    }
}
function Assert-SignedFiles($Published, [hashtable]$Files) {
    # 用安装命令锚定的公钥验签清单, 再逐个核对下载内容的 sha256; 任一不符即停止, 不写入任何代理文件
    $signed = $Published.signed_manifest
    if (-not $signed) { throw '组件签名校验失败：控制端未提供签名清单。已停止安装。' }
    $manifestBytes = [Convert]::FromBase64String($signed.manifest)
    $rsa = New-Object Security.Cryptography.RSACryptoServiceProvider
    $parameters = New-Object Security.Cryptography.RSAParameters
    $parameters.Modulus = [Convert]::FromBase64String($signingKey.n)
    $parameters.Exponent = [Convert]::FromBase64String($signingKey.e)
    $rsa.ImportParameters($parameters)
    if (-not $rsa.VerifyData($manifestBytes, 'SHA256', [Convert]::FromBase64String($signed.signature))) {
        throw '组件签名校验失败：清单签名无效，可能在网络途中被替换。已停止安装。'
    }
    $manifest = [Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
    if ($manifest.format -ne 'litecode-windows-manifest-v1') { throw '组件签名校验失败：清单格式不受支持。已停止安装。' }
    foreach ($name in $Files.Keys) {
        if ((Get-Sha256Hex $Files[$name]) -ne $manifest.files.$name) {
            throw ('组件签名校验失败：' + $name + ' 与签名清单不符，可能在网络途中被替换。已停止安装。')
        }
    }
    return $manifest
}
Add-Type -AssemblyName System.Security
$sha = [Security.Cryptography.SHA256]::Create()
$localId = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Code)))).Replace('-','').ToLower()
$sha.Dispose()
$root = Join-Path $env:LOCALAPPDATA ('LiteCodeNodes/' + $localId)
New-Item -ItemType Directory -Force $root | Out-Null
$grant = $identity.Name + ':(OI)(CI)F'
& icacls.exe $root /inheritance:r /grant:r $grant '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw '无法限制节点凭据目录访问权限' }
$configFile = Join-Path $root 'node.json'
if (Test-Path $configFile) {
    # 安装中断后在同一用户下重复原命令，复用已登记凭据，不重复消费接入码。
    $enrollment = Get-Content $configFile -Raw | ConvertFrom-Json
    if ($enrollment.server -ne $Server) { throw '本机已有不同服务器配置' }
    if ($enrollment.token_dpapi) {
        $token = [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect(
            [Convert]::FromBase64String($enrollment.token_dpapi), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser))
    } else { $token = $enrollment.token }
} else {
    $enrollment = Invoke-RestMethod -Method Post -Uri "$Server/api/windows/enroll" -ContentType 'application/json; charset=utf-8' -Body (@{
        code=$Code; metadata=@{computer=$env:COMPUTERNAME;user=$identity.Name}
    } | ConvertTo-Json -Compress)
    $token = $enrollment.token
}
$protected = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($token), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
@{server=$Server;id=$enrollment.id;alias=$enrollment.alias;token_dpapi=[Convert]::ToBase64String($protected)} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 $configFile
$headers = @{Authorization=('Bearer ' + $token)}
$device = "$Server/api/windows/device/$($enrollment.id)"
function Report-Stage([string]$Stage) {
    Invoke-RestMethod -Method Post -Uri "$device/status" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body (@{state='installing';stage=$Stage} | ConvertTo-Json -Compress) | Out-Null
}
# [2026-10-19] 卸载会留下 LiteCode 装的 sshd/防火墙规则/改过的 sshd_config; 安装中途失败还会留下 sshd 与管理员公钥却没有卸载入口。
# 安装时把"本节点新建/修改了什么"记进 ssh-owner.json (卸载按它恢复, 已有的 sshd 永不删除);
# 本次运行失败时 catch 只撤销本次新建的部分 ($sshRun), 之前运行已登记的保留。
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
    } elseif ($Owner.portable) {
        # 自带官方安装包装的 sshd: 用它自己的卸载脚本注销服务, 再删安装目录
        Stop-Service -Name sshd -Force -ErrorAction SilentlyContinue
        $uninstaller = Join-Path $Owner.portable 'uninstall-sshd.ps1'
        if (Test-Path $uninstaller) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uninstaller | Out-Null }
        Remove-Item -LiteralPath $Owner.portable -Recurse -Force -ErrorAction SilentlyContinue
    } elseif ($Owner.service) {
        Set-Service -Name sshd -StartupType $Owner.service.start_type
        if ($Owner.service.status -ne 'Running') { Stop-Service -Name sshd -Force -ErrorAction SilentlyContinue }
        elseif ($Owner.config_backup) { Restart-Service -Name sshd -ErrorAction SilentlyContinue }
    } elseif ($Owner.config_backup) { Restart-Service -Name sshd -ErrorAction SilentlyContinue }
    return $true
}
# <<< LiteCode SSH 撤销
$sshOwnerFile = Join-Path $root 'ssh-owner.json'
$sshBackup = Join-Path $root 'sshd_config.litecode-backup'
$sshPrior = $null
if (Test-Path $sshOwnerFile) { try { $sshPrior = Get-Content $sshOwnerFile -Raw | ConvertFrom-Json } catch { $sshPrior = $null } }
$sshRun = @{capability=''; portable=''; firewall_rule=$false; builtin_rule_disabled=$false; service=$null; config_backup=$false; key_added=$false}
function Save-SshOwner {
    # 每改一步就落盘 (之前运行登记的 OR 本次新建的), 中途崩溃也有据可依
    $merged = @{}
    foreach ($name in @('capability','portable','firewall_rule','builtin_rule_disabled','service','config_backup','key_added')) {
        $merged[$name] = if ($sshPrior -and $sshPrior.$name) { $sshPrior.$name } else { $sshRun[$name] }
    }
    $merged | ConvertTo-Json -Compress -Depth 4 | Set-Content -Encoding UTF8 $sshOwnerFile
}

# [2026-10-20] Windows 可选功能装不上 OpenSSH 时 (连不上 Windows Update / 公司 WSUS, 如 0x80240007) 改用自带的
# 微软官方 Win32-OpenSSH 安装包: 先从控制端下载 (国内访问 GitHub 不稳), 不行再回落 GitHub; 压缩包 sha256 必须与
# 下面写死的值一致 (本脚本已被安装命令锁定哈希), 解出的 sshd.exe 还要通过微软 Authenticode 签名校验。
$OpenSshZipSha256 = '23f50f3458c4c5d0b12217c6a5ddfde0137210a30fa870e98b29827f7b43aba5'
$OpenSshZipUrl = 'https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.0.0.0p2-Preview/OpenSSH-Win64.zip'
function Install-LiteCodeOpenSsh {
    $dest = Join-Path $env:ProgramFiles 'OpenSSH'
    if (Test-Path (Join-Path $dest 'sshd.exe')) { throw ($dest + ' 已有 OpenSSH 文件但没有注册 sshd 服务，为免覆盖请手动检查后重试。') }
    $bytes = $null
    try { $bytes = Get-Bytes "$device/vendor/openssh.zip" $headers } catch { $bytes = $null }
    if (-not $bytes -or (Get-Sha256Hex $bytes) -ne $OpenSshZipSha256) {
        try { $bytes = [byte[]](Invoke-WebRequest $OpenSshZipUrl -UseBasicParsing).Content } catch { $bytes = $null }
    }
    if (-not $bytes -or (Get-Sha256Hex $bytes) -ne $OpenSshZipSha256) { throw '自带 OpenSSH 安装包下载失败或校验不符（控制端与 GitHub 都试过）。' }
    $work = Join-Path $env:TEMP ('litecode-openssh-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $zip = Join-Path $work 'openssh.zip'
        [IO.File]::WriteAllBytes($zip, $bytes)
        Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
        $src = Join-Path $work 'OpenSSH-Win64'
        $sig = Get-AuthenticodeSignature -FilePath (Join-Path $src 'sshd.exe')
        if ($sig.Status -ne 'Valid' -or [string]$sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw '自带 OpenSSH 的 sshd.exe 签名校验失败，已停止。' }
        $sshRun.portable = $dest; Save-SshOwner
        Move-Item -LiteralPath $src -Destination $dest
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dest 'install-sshd.ps1') | Out-Null
        if (-not (Get-Service -Name sshd -ErrorAction SilentlyContinue)) { throw '自带 OpenSSH 已解压，但注册 sshd 服务失败。' }
    } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
try {
    Report-Stage 'ssh'
    Write-Host '正在准备 OpenSSH Server 与开机启动…'
    if ($SshPublicKey -notmatch ('^ssh-ed25519 [A-Za-z0-9+/=]+ litecode-node-' + $enrollment.id + '$')) {
        throw 'SSH 公钥与当前节点不匹配，请重新生成接入命令。'
    }
    # [2026-10-20] OpenSSH 只是额外运维通道, 节点靠桌面代理主动回连就能用。真机遇到 Add-WindowsCapability 0x80240007
    # (连不上 Windows Update/可选功能源, 如公司 WSUS) 整个安装失败; 改为撤销本步改动、提示后继续装代理。
    try {
        $existingSshService = Get-Service -Name sshd -ErrorAction SilentlyContinue
        $capability = $null
        if (-not $existingSshService) {
            try { $capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' -ErrorAction Stop | Select-Object -First 1 } catch { $capability = $null }
        }
        $sshWasInstalled = [bool]$existingSshService -or ($capability -and $capability.State -eq 'Installed')
        $existingFirewall = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
        if (-not $sshWasInstalled) {
            $viaCapability = $false
            if ($capability) {
                try {
                    $sshRun.capability = $capability.Name; Save-SshOwner
                    Add-WindowsCapability -Online -Name $capability.Name -ErrorAction Stop | Out-Null
                    $viaCapability = (Get-WindowsCapability -Online -Name $capability.Name).State -eq 'Installed'
                } catch { Write-Warning ('Windows 可选功能装不上 OpenSSH（' + $_.Exception.Message + '），改用自带的微软官方安装包…') }
                if (-not $viaCapability) { $sshRun.capability = ''; Save-SshOwner }
            }
            if (-not $viaCapability) { Install-LiteCodeOpenSsh }
        }
        # 新建规则只允许本地子网; 保留已有 SSH 环境及防火墙策略, 不打断现有隧道。
        if (-not $existingFirewall) {
            $builtinRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
            if ($builtinRule) { $sshRun.builtin_rule_disabled = $true; Save-SshOwner; $builtinRule | Disable-NetFirewallRule }
            if (-not (Get-NetFirewallRule -Name 'LiteCode-SSH-In-TCP' -ErrorAction SilentlyContinue)) {
                $sshRun.firewall_rule = $true; Save-SshOwner
                New-NetFirewallRule -Name 'LiteCode-SSH-In-TCP' -DisplayName 'LiteCode SSH (LocalSubnet)' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -RemoteAddress LocalSubnet | Out-Null
            }
        }
        if ($existingSshService -and ([string]$existingSshService.StartType -ne 'Automatic' -or [string]$existingSshService.Status -ne 'Running')) {
            # 已有 sshd 原本不是自动启动/运行中: 记下原状态, 卸载时恢复
            $sshRun.service = @{start_type=[string]$existingSshService.StartType; status=[string]$existingSshService.Status}; Save-SshOwner
        }
        Set-Service -Name sshd -StartupType Automatic
        Start-Service -Name sshd
        if ((Get-Service -Name sshd).Status -ne 'Running') { throw 'OpenSSH Server 未启动成功' }
        if ($SshPublicKey) {
            $keyFile = Join-Path $env:ProgramData 'ssh/administrators_authorized_keys'
            New-Item -ItemType Directory -Path (Split-Path $keyFile) -Force | Out-Null
            if (-not (Test-Path $keyFile)) { New-Item -ItemType File -Path $keyFile -Force | Out-Null }
            $keys = @([IO.File]::ReadAllLines($keyFile))
            if ($keys -notcontains $SshPublicKey) {
                $sshRun.key_added = $true; Save-SshOwner
                [IO.File]::AppendAllText($keyFile, "`n" + $SshPublicKey + "`n", (New-Object Text.UTF8Encoding($false)))
            }
            & icacls.exe $keyFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
            if ($LASTEXITCODE -ne 0) { throw '无法设置 SSH 公钥文件权限' }
            if (-not $sshWasInstalled) {
                # 只收紧本次新装的服务; 已有 SSH 配置/密码认证及隧道不改动。
                $sshdConfig = Join-Path $env:ProgramData 'ssh/sshd_config'
                Copy-Item -LiteralPath $sshdConfig -Destination $sshBackup -Force
                $sshRun.config_backup = $true; Save-SshOwner
                $configText = [IO.File]::ReadAllText($sshdConfig)
                $configText = [regex]::Replace($configText, '(?m)^\s*#?\s*PasswordAuthentication\s+.*$', '')
                [IO.File]::WriteAllText($sshdConfig, "PasswordAuthentication no`n" + $configText, (New-Object Text.UTF8Encoding($false)))
                Restart-Service -Name sshd
            }
        }
    } catch {
        $sshError = $_.Exception.Message
        try {
            if (Undo-LiteCodeSsh $sshRun ('litecode-node-' + $enrollment.id) $sshBackup) {
                if ($sshRun.config_backup -and -not ($sshPrior -and $sshPrior.config_backup)) { Remove-Item -LiteralPath $sshBackup -Force -ErrorAction SilentlyContinue }
            }
            if ($sshPrior) { $sshPrior | ConvertTo-Json -Compress -Depth 4 | Set-Content -Encoding UTF8 $sshOwnerFile }
            else { Remove-Item -LiteralPath $sshOwnerFile -Force -ErrorAction SilentlyContinue }
        } catch { Write-Warning ('撤销本次 SSH 改动失败：' + $_.Exception.Message) }
        $sshRun = @{}
        Write-Warning ('OpenSSH Server 没装上，已跳过（' + $sshError + '）。这台电脑照常接入，桌面操作不受影响，只是暂时没有 SSH 运维通道。' +
            '常见原因：连不上 Windows Update 或可选功能源（例如公司 WSUS）。处理后重新运行原命令即可补装；也可手动安装 OpenSSH Server 后重跑。')
        try { Report-Stage 'ssh_skipped' } catch {}
    }
    Report-Stage 'python'
    Write-Host '正在准备 Python 运行环境…'
    $python = $null
    $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Source -notlike '*\WindowsApps\*') {
        $version = & $candidate.Source -c 'import sys; print(int((3,12)<=sys.version_info[:2]<(3,14)))' 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq '1') { $python=$candidate.Source }
    }
    if (-not $python) {
        foreach ($hive in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')) {
            foreach ($versionKey in @('3.13', '3.12')) {
                $installKey = Join-Path $hive ($versionKey + '\InstallPath')
                if (Test-Path $installKey) {
                    $candidatePath = Join-Path (Get-Item $installKey).GetValue('') 'python.exe'
                    if (Test-Path $candidatePath) { $python=$candidatePath; break }
                }
            }
            if ($python) { break }
        }
    }
    $runtime = Join-Path $root 'runtime'
    if ($python -and -not (Test-Path (Join-Path $runtime 'python.exe'))) {
        # 复制基础运行时，后续节点不会依赖另一节点或用户的 Python 目录。
        $baseRuntime = & $python -c 'import sys; print(sys.base_prefix)'
        if ($LASTEXITCODE -ne 0) { throw '读取 Python 基础目录失败' }
        New-Item -ItemType Directory -Force $runtime | Out-Null
        foreach ($part in @('Lib', 'DLLs', 'tcl')) {
            $from = Join-Path $baseRuntime $part
            if (Test-Path $from) {
                & robocopy.exe $from (Join-Path $runtime $part) /E /XD site-packages __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { throw '复制独立 Python 运行时失败' }
            }
        }
        Get-ChildItem $baseRuntime -File | Where-Object { $_.Extension -in @('.exe','.dll','.zip') } | Copy-Item -Destination $runtime
        '{"kind":"copy"}' | Set-Content -Encoding UTF8 (Join-Path $root 'runtime-owner.json')
    }
    if (Test-Path (Join-Path $runtime 'python.exe')) { $python=Join-Path $runtime 'python.exe' }
    if (-not $python) {
        $python = Join-Path $runtime 'python.exe'
        if (-not (Test-Path $python)) {
            $installer = Join-Path $root 'python-installer.exe'
            # 官方受支持维护版；验证签名后安装，无需预装 winget。
            # 先走国内镜像 (华为云, 同一官方安装包), 失败再回落 python.org; 来源不同不影响安全: 下面一律校验 PSF 签名
            $downloaded = $false
            foreach ($url in @('https://mirrors.huaweicloud.com/python/3.13.16/python-3.13.16-amd64.exe',
                               'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe')) {
                try { Invoke-WebRequest $url -OutFile $installer -UseBasicParsing -TimeoutSec 600; $downloaded = $true; break }
                catch { Write-Warning ('Python 安装包下载失败，换下一个地址：' + $url) }
            }
            if (-not $downloaded) { throw 'Python 安装包下载失败（国内镜像和 python.org 都试过），请检查网络后重试原命令。' }
            $signature = Get-AuthenticodeSignature $installer
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw 'Python 安装程序签名验证失败'
            }
            '{"kind":"installer"}' | Set-Content -Encoding UTF8 (Join-Path $root 'runtime-owner.json')
            $process = Start-Process $installer -ArgumentList @('/quiet','InstallAllUsers=0','Include_test=0','Include_launcher=0','AssociateFiles=0','Shortcuts=0','PrependPath=0',('TargetDir="'+$runtime+'"')) -Wait -PassThru
            if ($process.ExitCode -notin @(0,3010)) { throw 'Python 安装失败' }
            # 保留官方卸载器，用于清理它登记的注册表和安装信息。
        }
    }
    # 依赖全部放入本节点目录，卸载时不修改用户已有 Python 或其他节点。
    $venv = Join-Path $root 'venv'
    if (-not (Test-Path (Join-Path $venv 'Scripts/python.exe'))) {
        & $python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw '创建节点独立环境失败' }
    }
    $python = Join-Path $venv 'Scripts/python.exe'
    Write-Host '正在安装桌面依赖…'
    Report-Stage 'packages'
    & $python -m pip install --no-cache-dir --disable-pip-version-check mss pillow uiautomation pywin32 pyautogui pyperclip winocr psutil -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { throw '桌面依赖安装失败，请检查网络后重试' }
    Report-Stage 'agent'
    $agentBytes = Get-Bytes "$device/assets/agent.py" $headers
    $uninstallBytes = Get-Bytes "$device/assets/uninstall.ps1" $headers
    $manifest = $null
    if ($signingKey) {
        $manifest = Assert-SignedFiles (Invoke-RestMethod "$device/versions" -Headers $headers) @{
            'windows_node_agent.py'=$agentBytes; 'windows_node_uninstall.ps1'=$uninstallBytes }
    }
    [IO.File]::WriteAllBytes((Join-Path $root 'agent.py'), $agentBytes)
    # PowerShell 5.1 按 ANSI 读无 BOM 脚本, 中文会乱码: 落盘补 UTF-8 BOM (哈希按下发字节)
    [IO.File]::WriteAllBytes((Join-Path $root 'uninstall.ps1'), [byte[]](@(0xEF,0xBB,0xBF) + $uninstallBytes))
    if ($manifest) {
        # 固定签名公钥, 代理之后的每次自动更新都要求验签通过
        @{key=@{alg=$signingKey.alg;n=$signingKey.n;e=$signingKey.e};fingerprint=$KeyFingerprint.ToLower();pinned_by='install';last_issued=$manifest.issued} |
            ConvertTo-Json -Compress | Set-Content -Encoding UTF8 (Join-Path $root 'trust.json')
    }
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
    # 指定工作目录, 否则代理 cwd 为 C:\Windows\system32, 它拉起的应用"另存为"会默认跳到 System32
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"'+(Join-Path $root 'agent.py')+'" "'+$root+'"') -WorkingDirectory $root
    # 开机 + 登录触发 + 每分钟补拉 (运行中 IgnoreNew), 崩溃最多重启 999 次。
    # Interactive 仍需安装用户已登录, 不改变自动登录/锁屏设置。
    $trigger = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $identity.Name),
                 (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
    $owner = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
    $taskName = 'LiteCode-Node-' + $enrollment.id
    # 重跑安装命令时先停掉旧代理 (MultipleInstances IgnoreNew 会忽略运行中的再次启动)
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $owner -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host ('已安装节点：' + $enrollment.alias + '。请在 LiteCode Web 查看上线状态与首张验收截图。')
    Write-Host '已启用开机/登录自动启动与崩溃恢复；代理和功能包会在空闲时自动更新。桌面操作需要安装用户已登录并解锁。'
    Write-Host ('本机停止：Stop-ScheduledTask -TaskName ' + $taskName)
    Write-Host ('本机完全卸载：powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $root 'uninstall.ps1') + '" -Root "' + $root + '" -Schedule')
} catch {
    $failure = $_
    # 本次运行新建的 SSH 项 (含管理员公钥) 全部撤销: 安装没完成就没有卸载入口, 不能留下可登录的后门
    try {
        if (Undo-LiteCodeSsh $sshRun ('litecode-node-' + $enrollment.id) $sshBackup) {
            if ($sshRun.config_backup -and -not ($sshPrior -and $sshPrior.config_backup)) { Remove-Item -LiteralPath $sshBackup -Force -ErrorAction SilentlyContinue }
        }
        if ($sshPrior) { $sshPrior | ConvertTo-Json -Compress -Depth 4 | Set-Content -Encoding UTF8 $sshOwnerFile }
        else { Remove-Item -LiteralPath $sshOwnerFile -Force -ErrorAction SilentlyContinue }
    } catch { Write-Warning ('撤销本次 SSH 改动失败：' + $_.Exception.Message) }
    try { Invoke-RestMethod -Method Post -Uri "$device/status" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body (@{state='error';error=$failure.Exception.Message} | ConvertTo-Json -Compress) | Out-Null } catch {}
    throw $failure
}
