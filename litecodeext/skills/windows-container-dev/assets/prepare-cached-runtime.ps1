# 专用测试机可选的缓存预装；不替代一键安装器的全新系统验收。
param(
    [Parameter(Mandatory=$true)][string]$ExpectedComputer,
    [string]$PackageDirectory = '\\host.lan\Data\devpackages',
    [string]$TrustedSshForwarder = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:COMPUTERNAME -ne $ExpectedComputer) { throw 'Wrong dedicated test VM' }
$manifest = Get-Content (Join-Path $PackageDirectory 'recovery-packages.json') -Raw | ConvertFrom-Json
$cache = Join-Path $env:LOCALAPPDATA 'LiteCodeDevCache'
New-Item -ItemType Directory -Force $cache | Out-Null
foreach ($item in $manifest) {
    $file = Join-Path $cache $item.name
    Copy-Item (Join-Path $PackageDirectory $item.name) $file -Force
    if ((Get-FileHash $file -Algorithm SHA256).Hash.ToLower() -ne $item.sha256) { throw ('Package hash mismatch: ' + $item.name) }
    $publisher = if ($item.name -eq 'openssh.msi') { 'Microsoft Corporation' } else { 'Python Software Foundation' }
    $signature = Get-AuthenticodeSignature $file
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $publisher) {
        throw ('Package signature rejected: ' + $item.name)
    }
}
$freshSsh = -not (Get-Service sshd -ErrorAction SilentlyContinue)
if ($freshSsh) {
    $file = Join-Path $cache 'openssh.msi'
    $process = Start-Process msiexec.exe -ArgumentList @('/i',('"'+$file+'"'),'/qn','/norestart') -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw ('OpenSSH MSI failed: ' + $process.ExitCode) }
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    $config = Join-Path $env:ProgramData 'ssh\sshd_config'
    $text = [IO.File]::ReadAllText($config)
    [IO.File]::WriteAllText($config, "PasswordAuthentication no`r`n" + $text, (New-Object Text.UTF8Encoding($false)))
    # 此分支只处理本脚本刚安装的服务；限制其新建规则，保留其它 SSH 环境。
    Get-NetFirewallRule -Name '*OpenSSH*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    $sources = @('LocalSubnet')
    if ($TrustedSshForwarder) {
        $parsed = $null
        if (-not [Net.IPAddress]::TryParse($TrustedSshForwarder, [ref]$parsed)) { throw 'Forwarder must be one explicit IP address' }
        $sources += $TrustedSshForwarder
    }
    New-NetFirewallRule -Name 'LiteCode-Devtest-Cached-SSH' -DisplayName 'LiteCode dedicated test VM SSH' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress $sources | Out-Null
    Restart-Service sshd
}
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
if (-not (Test-Path $python)) {
    $target = Split-Path $python
    $process = Start-Process (Join-Path $cache 'python-3.13.16-amd64.exe') -ArgumentList @('/quiet','InstallAllUsers=0','Include_test=0','Include_launcher=0','AssociateFiles=0','Shortcuts=0','PrependPath=0',('TargetDir="'+$target+'"')) -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw ('Python installer failed: ' + $process.ExitCode) }
}
if (-not (Test-Path $python)) { throw 'Cached Python installation did not produce python.exe' }
& $python --version
if ($LASTEXITCODE -ne 0) { throw 'Cached Python runtime verification failed' }
@{computer=$env:COMPUTERNAME;state='cached-runtime-prepared';sshd=(Get-Service sshd).Status.ToString();sshStart=(Get-Service sshd).StartType.ToString();freshSsh=$freshSsh;python=$python} |
    ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path (Split-Path $PackageDirectory) 'cached-runtime-proof.json')
