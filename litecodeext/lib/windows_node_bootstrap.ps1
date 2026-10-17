# 纯净 Windows 节点接入：仅主动访问 LiteCode Web，不安装 SSH 或开放监听端口。
# 安装命令已先核对本脚本的 sha256 再执行; -KeyFingerprint 为控制端签名公钥指纹 (同样来自带外复制的安装命令)。
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Server, [Parameter(Mandatory=$true)][string]$Code, [string]$KeyFingerprint = '')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请在要接入的用户桌面，以管理员身份打开 PowerShell 后执行。'
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
try {
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
            Invoke-WebRequest 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe' -OutFile $installer -UseBasicParsing
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
    # 登录时启动 + 每 5 分钟补拉一次 (运行中由 IgnoreNew 忽略), 崩溃最多自动重启 999 次
    $trigger = @((New-ScheduledTaskTrigger -AtLogOn -User $identity.Name),
                 (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)))
    $owner = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
    $taskName = 'LiteCode-Node-' + $enrollment.id
    # 重跑安装命令时先停掉旧代理 (MultipleInstances IgnoreNew 会忽略运行中的再次启动)
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $owner -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host ('已安装节点：' + $enrollment.alias + '。请在 LiteCode Web 查看上线状态与首张验收截图。')
    Write-Host ('本机停止：Stop-ScheduledTask -TaskName ' + $taskName)
    Write-Host ('本机完全卸载：powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $root 'uninstall.ps1') + '" -Root "' + $root + '" -Schedule')
} catch {
    try { Invoke-RestMethod -Method Post -Uri "$device/status" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body (@{state='error';error=$_.Exception.Message} | ConvertTo-Json -Compress) | Out-Null } catch {}
    throw
}
