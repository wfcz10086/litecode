# 新 Windows 的一次性引导：管理员 PowerShell 执行，仅写入公钥。
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PublicKey,
    [string]$RemoteAddress = 'LocalSubnet'
)
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请在需要接入的 Windows 上，以管理员身份运行 PowerShell。'
}
if ($PublicKey -notmatch '^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( [^\r\n]*)?$') {
    throw '请传入有效 SSH 公钥，不能传私钥。'
}
$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $capability.Name | Out-Null
}
Set-Service sshd -StartupType Automatic
Start-Service sshd
# 管理员账户默认使用此文件；普通账户使用自己的 authorized_keys。
$keyFile = Join-Path $env:ProgramData 'ssh/administrators_authorized_keys'
if (-not (Test-Path $keyFile)) { New-Item -ItemType File -Path $keyFile -Force | Out-Null }
if (-not ((Get-Content $keyFile) -contains $PublicKey)) {
    Add-Content -Path $keyFile -Value $PublicKey -Encoding ascii
}
& icacls.exe $keyFile /inheritance:r /grant '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
$userDir = Join-Path $env:USERPROFILE '.ssh'
New-Item -ItemType Directory -Path $userDir -Force | Out-Null
$userKeys = Join-Path $userDir 'authorized_keys'
if (-not (Test-Path $userKeys)) { New-Item -ItemType File -Path $userKeys | Out-Null }
if (-not ((Get-Content $userKeys) -contains $PublicKey)) {
    Add-Content -Path $userKeys -Value $PublicKey -Encoding ascii
}
$rules = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($rules) {
    $rules | Set-NetFirewallRule -Enabled True -RemoteAddress $RemoteAddress
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'LiteCode SSH' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -RemoteAddress $RemoteAddress | Out-Null
}
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ((-not $pythonCommand) -or ($pythonCommand.Source -like '*\WindowsApps\*')) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'SSH 已配置；未找到 Python 或 winget，请安装 Python 3.12 后继续。'
    }
    winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python 安装失败，请检查 winget 输出。' }
}
Write-Output '引导完成。重新登录 Windows 以更新 PATH；在 LiteCode 保存 SSH 别名后，说：初始化这台 Windows 并截图验收。'
Write-Output ('Windows 用户：' + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notmatch '^(127\.|169\.254\.)'} | Select-Object InterfaceAlias,IPAddress
