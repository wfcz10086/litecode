"""Windows SSH 的 PowerShell 脚本载体与 WSL Bash stdin 载体。"""
import base64
import re
import subprocess


# cmd.exe 上限约 8191 字符，预留默认 SSH shell 包装所需空间。
POWERSHELL_COMMAND_LIMIT = 7000
_POWERSHELL_PREFIX = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand '
_POWERSHELL_PRELUDE = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "$OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
    "$ErrorActionPreference='Stop';\n"
)

# PowerShell 把 U+2018~U+201B 也当单引号 (双引号同理 U+201C~U+201E)。
_SQ = "'‘’‚‛"
_DQ = '"“”„'
_USING_RE = re.compile(r'using[ \t]+(?:namespace|module|assembly)\b', re.I)
_PARAM_RE = re.compile(r'param\s*\(', re.I)


def _skip_trivia(s: str, i: int) -> int:
    """跳过空白、行注释 (#requires 也是注释) 和 <# #> 块注释。"""
    n = len(s)
    while i < n:
        if s[i] in ' \t\r\n﻿':
            i += 1
        elif s.startswith('<#', i):
            j = s.find('#>', i + 2)
            if j < 0:
                return n
            i = j + 2
        elif s[i] == '#':
            j = s.find('\n', i)
            i = n if j < 0 else j + 1
        else:
            break
    return i


def _skip_balanced(s: str, i: int) -> int:
    """s[i] 是 ( 或 [；返回匹配右括号之后的位置，失配返回 -1。识别引号与注释。"""
    depth, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth < 0:
                return -1
            if depth == 0:
                return i + 1
        elif c in _SQ:
            i += 1
            while i < n and not (s[i] in _SQ and not (i + 1 < n and s[i + 1] in _SQ)):
                i += 2 if s[i] in _SQ else 1
        elif c in _DQ:
            i += 1
            while i < n and not (s[i] in _DQ and not (i + 1 < n and s[i + 1] in _DQ)):
                i += 2 if (s[i] == '`' or s[i] in _DQ) else 1
        elif s.startswith('<#', i):
            j = s.find('#>', i + 2)
            if j < 0:
                return -1
            i = j + 1
        elif c == '#':
            j = s.find('\n', i)
            if j < 0:
                return -1
            i = j
        i += 1
    return -1


def _header_end(script: str) -> int:
    """开头 using 语句与 [CmdletBinding()] param(...) 块的结束位置；没有则 0。"""
    end = 0
    i = _skip_trivia(script, 0)
    while _USING_RE.match(script, i):
        line_end = script.find('\n', i)
        line_end = len(script) if line_end < 0 else line_end
        semi = script.find(';', i, line_end)
        end = semi + 1 if semi >= 0 else line_end
        i = _skip_trivia(script, end)
    k = i
    while k < len(script) and script[k] == '[':
        e = _skip_balanced(script, k)
        if e < 0:
            return end
        k = _skip_trivia(script, e)
    m = _PARAM_RE.match(script, k)
    if m:
        e = _skip_balanced(script, m.end() - 1)
        if e > 0:
            end = e
    return end


def powershell_script(script: str) -> str:
    # [2026-10-19] 引导前置后, 以 using namespace / param(...) 开头的脚本直接解析失败
    # ("using 语句必须出现在其它语句之前" / param 不是首句被当命令)。把开头的 using/param 头部
    # 原样放在引导之前, 其余脚本不包 ScriptBlock, $LASTEXITCODE 与 exit N 语义不变。
    end = _header_end(script)
    if not end:
        return _POWERSHELL_PRELUDE + script
    return script[:end] + '\n' + _POWERSHELL_PRELUDE + script[end:]


def powershell_command(script: str) -> str:
    """保持短脚本原有 EncodedCommand 格式。"""
    return _POWERSHELL_PREFIX + base64.b64encode(powershell_script(script).encode('utf-16le')).decode()


def _ps_base64_text(text: str) -> str:
    """把任意文本变成 PowerShell 表达式；内容只以 Base64 出现，不受任何引号转义影响。"""
    return "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + \
        base64.b64encode(text.encode('utf-8')).decode() + "'))"


def powershell_file_command(path: str) -> str:
    """短引导只含临时文件路径，不把脚本或凭据再次拼入命令行。"""
    # [2026-10-19] 原先只把 ASCII ' 翻倍, 但 PowerShell 把 ‘ ’ ‚ ‛ (U+2018~U+201B) 也当单引号:
    # 用户名 O’Brien 的临时目录让字符串提前结束, 长脚本整段跑不起来。路径改 Base64 传入。
    # 保持 EncodedCommand 的最后一条命令失败 => rc=1 语义；脚本显式 exit N 原样退出。
    # 读取后在内存执行，与短 EncodedCommand 一样，不修改执行策略。
    source = ("& ([ScriptBlock]::Create([IO.File]::ReadAllText(" + _ps_base64_text(path) + ")"
              ' + "`nif (-not `$?) { exit 1 }"))')
    command = powershell_command(source)
    if len(command) > POWERSHELL_COMMAND_LIMIT:
        raise ValueError('Windows temporary script path is too long for the SSH bootstrap')
    return command


_BOOTSTRAP = (
    "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
    "$OutputEncoding=[Console]::OutputEncoding; $ErrorActionPreference='Stop'; "
    "$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())); "
    "& ([ScriptBlock]::Create($source))"
)

_WSL_SCRIPT = r"""
$ProgressPreference = 'SilentlyContinue'
$env:WSL_UTF8 = '1'
$job = Join-Path ([IO.Path]::GetTempPath()) ('LiteCodeWsl-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $job | Out-Null
$outputFile = Join-Path $job 'stdout'
$errorFile = Join-Path $job 'stderr'
$process = $null; $outputStream = $null; $errorStream = $null; $exitCode = 1
$timeoutMs = __TIMEOUT_MS__

function Read-WslText([string]$path) {
    $bytes = [IO.File]::ReadAllBytes($path)
    if ($bytes.Length -eq 0) { return '' }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 255 -and $bytes[1] -eq 254) {
        return [Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
    }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 254 -and $bytes[1] -eq 255) {
        return [Text.Encoding]::BigEndianUnicode.GetString($bytes, 2, $bytes.Length - 2)
    }
    # WSL 管理错误可能使用无 BOM 的 UTF-16；不通过删 NUL 伪造解码。
    $pairs = [Math]::Min([int][Math]::Floor($bytes.Length / 2), 256)
    $oddZero = 0; $evenZero = 0
    for ($i = 0; $i -lt $pairs; $i++) {
        if ($bytes[2*$i] -eq 0) { $evenZero++ }
        if ($bytes[2*$i+1] -eq 0) { $oddZero++ }
    }
    if ($pairs -gt 2 -and $oddZero -gt $pairs * 0.3 -and $evenZero -lt $pairs * 0.05) {
        return [Text.Encoding]::Unicode.GetString($bytes)
    }
    # [2026-10-19] 原先严格 UTF-8 (throwOnInvalid=$true): GBK 文件/二进制/Latin-1 文件名一出现就抛异常,
    # 在 ErrorActionPreference=Stop 下真实 stdout/stderr 和退出码全丢, 只剩 rc=1。改为替换字符不抛。
    $utf8 = New-Object Text.UTF8Encoding($false, $false)
    return $utf8.GetString($bytes)
}

function Stop-WslTree($p) {
    try { if ($p.HasExited) { return } } catch { return }
    $killed = $false
    try { $p.Kill($true); $killed = $true } catch { }   # .NET Core 3+ 才有整树 Kill
    if (-not $killed) {
        try { & taskkill.exe /T /F /PID $p.Id 2>&1 | Out-Null } catch { }
        try { if (-not $p.HasExited) { $p.Kill() } } catch { }
    }
}

try {
    $inputBytes = [Convert]::FromBase64String('__BASH__')
    $utf8 = New-Object Text.UTF8Encoding($false)
    [Console]::InputEncoding = $utf8
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:WINDIR 'System32/wsl.exe'
    # [2026-10-19] 参数 Base64 传入: wsl_distro/wsl_user 含 ’ (U+2019) 时单引号转义失效可注入。
    $info.Arguments = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__ARGS__'))
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    if ($info.PSObject.Properties['StandardInputEncoding']) { $info.StandardInputEncoding = $utf8 }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    # 按原始字节并行排空两个输出流，避免管道死锁及 Start-Process 的行/编码转换。
    $outputStream = [IO.File]::OpenWrite($outputFile)
    $errorStream = [IO.File]::OpenWrite($errorFile)
    $outputCopy = $process.StandardOutput.BaseStream.CopyToAsync($outputStream)
    $errorCopy = $process.StandardError.BaseStream.CopyToAsync($errorStream)
    $inputPipe = $process.StandardInput.BaseStream
    $inputFailed = $false
    try {
        $inputPipe.Write($inputBytes, 0, $inputBytes.Length)
        $inputPipe.Flush()
    } catch [IO.IOException] {
        $inputFailed = $true
    } finally { $inputPipe.Close() }
    # [2026-10-19] 原先无限 WaitForExit(): 卡住的 WSL 命令在远端一直跑, finally 清理永远不执行。
    # 按剩余预算等待, 超时杀进程树并返回 124, 清理照常。
    $timedOut = $false
    if ($timeoutMs -gt 0) { $timedOut = -not $process.WaitForExit($timeoutMs) } else { $process.WaitForExit() }
    if ($timedOut) {
        Stop-WslTree $process
        try { [void]$process.WaitForExit(5000) } catch { }
        try { [void]$outputCopy.Wait(5000) } catch { }
        try { [void]$errorCopy.Wait(5000) } catch { }
    } else {
        [void]$outputCopy.GetAwaiter().GetResult()
        [void]$errorCopy.GetAwaiter().GetResult()
    }
    $outputStream.Dispose(); $errorStream.Dispose()
    if ($timedOut) {
        $exitCode = 124
    } else {
        if ($null -eq $process.ExitCode) { throw 'WSL process completion is unknown: no exit code' }
        $exitCode = $process.ExitCode
        if ($inputFailed -and $exitCode -eq 0) { throw 'WSL input was not fully delivered; completion is unknown' }
    }
    try { [Console]::Out.Write((Read-WslText $outputFile)) } catch { [Console]::Error.WriteLine('[LiteCode] stdout 读取失败: ' + $_) }
    try { [Console]::Error.Write((Read-WslText $errorFile)) } catch { [Console]::Error.WriteLine('[LiteCode] stderr 读取失败: ' + $_) }
    if ($timedOut) {
        [Console]::Error.WriteLine("`n[LiteCode] WSL command timed out after $([int]($timeoutMs / 1000))s; wsl.exe process tree was killed. " +
            'Linux-side processes may still be running; check before retrying.')
    }
} finally {
    if ($null -ne $outputStream) { $outputStream.Dispose() }
    if ($null -ne $errorStream) { $errorStream.Dispose() }
    if ($null -ne $process) { $process.Dispose() }
    Remove-Item -LiteralPath $job -Recurse -Force -ErrorAction SilentlyContinue
}
exit $exitCode
"""


def wsl_command(script: str, distro: str, user: str = '', timeout: float | None = None) -> tuple[str, bytes]:
    """返回短 PowerShell 引导命令与 ASCII stdin，避免 Windows 命令行长度限制。

    timeout 为 SSH 侧总超时 (秒)；远端 WSL 等待预算比它少 10s，保证先在远端超时、杀进程并清理，
    再由 SSH 侧拿到 rc=124 与已有输出。None/<=0 表示不限时。"""
    if not isinstance(distro, str) or not distro.strip():
        raise ValueError('shell=wsl 需要明确的 wsl_distro')
    for name, value in [('wsl_distro', distro), ('wsl_user', user)]:
        if not isinstance(value, str) or len(value) > 128 or any(c in value for c in '\x00\r\n') or value.startswith('-'):
            raise ValueError(name + ' 必须是有效的单个 WSL 名称')
    if not isinstance(script, str) or '\x00' in script:
        raise ValueError('WSL Bash 脚本必须是无 NUL 的文本')
    script = script.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if not script.endswith('\n'):
        script += '\n'
    # 先读完 stdin 再执行，脚本内的命令不会意外消费后续脚本内容。
    argv = ['--distribution', distro]
    if user:
        argv += ['--user', user]
    argv += ['--cd', '~', '--exec', 'bash', '-c', 'exec bash -c "$(cat)"']
    arguments = base64.b64encode(subprocess.list2cmdline(argv).encode('utf-8')).decode()
    budget_ms = int(max(5.0, float(timeout) - 10) * 1000) if timeout and timeout > 0 else -1
    body = (_WSL_SCRIPT.replace('__BASH__', base64.b64encode(script.encode('utf-8')).decode())
            .replace('__ARGS__', arguments).replace('__TIMEOUT_MS__', str(budget_ms)))
    command = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode(_BOOTSTRAP.encode('utf-16le')).decode()
    return command, base64.b64encode(body.encode('utf-8'))
