"""生成单行 Windows 接入命令，私钥留在 LiteCode 工作区。"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess


def build_command(bootstrap, public_key, remote_address='LocalSubnet'):
    if not re.fullmatch(r'ssh-(?:ed25519|rsa) [A-Za-z0-9+/=]+(?: [^\r\n]*)?', public_key):
        raise ValueError('必须传入 SSH 公钥')
    quote = lambda text: "'" + text.replace("'", "''") + "'"
    body = '& {\n' + bootstrap + '\n} -PublicKey ' + quote(public_key) + ' -RemoteAddress ' + quote(remote_address)
    encoded = base64.b64encode(body.encode('utf-16le')).decode('ascii')
    return 'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + encoded


def prepare(alias, host, user, workspace, bootstrap, port=22, jump='', remote_address='LocalSubnet'):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', alias):
        raise ValueError('别名只能包含英文、数字、下划线和连字符')
    if not host.strip() or not user.strip() or not 1 <= int(port) <= 65535:
        raise ValueError('必须提供有效地址、Windows 用户名和端口')
    from core.tools.handlers.ssh_hosts import _load, _save
    hosts = _load()
    if alias in hosts:
        raise ValueError('该别名已存在，请使用新别名，避免覆盖已有凭据')
    if jump and jump not in hosts:
        raise ValueError('跳板别名尚未保存')
    bootstrap_source = Path(bootstrap).read_text()
    secrets = Path(workspace) / '.secrets' / 'windows_keys'
    secrets.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = secrets / alias
    if private.exists() or private.with_suffix('.pub').exists():
        raise ValueError('该密钥文件已存在，不覆盖')
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'litecode-' + alias,
                    '-f', str(private)], check=True, capture_output=True)
    os.chmod(private, 0o600)
    public = private.with_suffix('.pub').read_text().strip()
    command = build_command(bootstrap_source, public, remote_address)
    output = Path(workspace) / 'uploads' / 'windows-onboarding' / alias
    output.mkdir(parents=True, exist_ok=True)
    (output / '初始化命令.txt').write_text(command + '\n', encoding='utf-8')
    hosts[alias] = {'host': host, 'port': int(port), 'user': user, 'key_file': str(private),
                    'password': '', 'jump': jump, 'note': '待目标机器执行单行引导命令，随后 initialize 截图验收'}
    _save(hosts)
    return {'alias': alias, 'command_file': str(output / '初始化命令.txt'),
            'public_key': public, 'status': 'awaiting_bootstrap',
            'next': f'在目标用户的管理员 PowerShell 粘贴命令；回到 LiteCode 说：初始化 {alias} 并截图验收'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alias', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--jump', default='')
    parser.add_argument('--workspace', default='/tmp/litecode_workspace')
    parser.add_argument('--bootstrap', default='/tmp/litecode_workspace/uploads/windows-onboarding/windows_desktop_bootstrap.ps1')
    parser.add_argument('--remote-address', default='LocalSubnet')
    args = parser.parse_args()
    print(json.dumps(prepare(args.alias, args.host, args.user, args.workspace, args.bootstrap,
                             args.port, args.jump, args.remote_address), ensure_ascii=False))


if __name__ == '__main__':
    main()
