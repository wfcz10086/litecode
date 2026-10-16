# 纯净 Windows 节点接入：仅主动访问 LiteCode Web，不安装 SSH 或开放监听端口。
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Server, [Parameter(Mandatory=$true)][string]$Code)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请在要接入的用户桌面，以管理员身份打开 PowerShell 后执行。'
}
$Server = $Server.TrimEnd('/')
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
    Invoke-WebRequest "$device/assets/agent.py" -Headers $headers -OutFile (Join-Path $root 'agent.py') -UseBasicParsing
    Invoke-RestMethod "$device/assets/uninstall.ps1" -Headers $headers | Set-Content -Encoding UTF8 (Join-Path $root 'uninstall.ps1')
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"'+(Join-Path $root 'agent.py')+'" "'+$root+'"')
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
