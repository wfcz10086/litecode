#!/usr/bin/env python3
"""只读提取指定工具调用的原文证据，不执行目标机操作，也不改会话/WAL。"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

WORKSPACE = Path('/tmp/litecode_workspace')
ALLOWED_TOOLS = {'windows_desktop', 'windows_system', 'ssh_exec'}
SAFE_SID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
SAFE_OUTPUT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.json\Z')
# 保守拒绝：不尝试从含凭据的文本生成另一份“脱敏原文”。
SENSITIVE = re.compile(
    r'password|passwd|passphrase|\bpwd\b|token|private[\s_-]*key|cookie|secret|credential|'
    r'authorization|\bbearer\b|api[\s_-]*key|key_file|sshpass|'
    r'(?:access|join|enroll)[\s_-]*code|密码|口令|接入码|访问令牌|私钥|密钥|'
    r'-----BEGIN[^\r\n]*PRIVATE KEY', re.I)
TRIMMED = re.compile(
    r'\[(?:budget-)?trimmed\]|\[cleared\s*:|\[subagent truncated\b|\[SYSTEM OVERRIDE\]|'
    r'\.\.\.\s*\(truncated\)|"(?:[A-Za-z0-9_]*_)?truncated"\s*:\s*true\b', re.I)


class EvidenceError(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError('JSON 含重复字段，无法可靠核对原文，拒绝提取')
        result[key] = value
    return result


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_sources(workspace, sid):
    """用目录 fd 直读，不导入会回放、压实或清 WAL 的会话模块。"""
    directory = os.open(workspace / 'sessions', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    sources, signatures = {}, {}
    try:
        for suffix in ('.json', '.wal'):
            name = sid + suffix
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                sources[suffix], signatures[name] = None, None
                continue
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise EvidenceError('会话来源必须是普通文件')
                signatures[name] = _signature(info)
                sources[suffix] = stream.read()
        # 快照替换/WAL 压实或继续追加时，本次不导出不一致的组合。
        for name, before in signatures.items():
            try:
                after = _signature(os.stat(name, dir_fd=directory, follow_symlinks=False))
            except FileNotFoundError:
                after = None
            if before != after:
                raise EvidenceError('会话正在写入或压实，请稍后重新提取')
    finally:
        os.close(directory)
    if all(value is None for value in sources.values()):
        raise EvidenceError('找不到指定会话的快照或 WAL')
    return sources


def _messages(sources, sid):
    messages = []
    if sources['.json'] is not None:
        try:
            document = json.loads(sources['.json'].decode('utf-8'), object_pairs_hook=_unique_pairs)
            if not isinstance(document, dict) or document.get('sid', sid) != sid:
                raise ValueError
            messages = document['msgs']
            if not isinstance(messages, list):
                raise ValueError
        except (UnicodeError, ValueError, KeyError):
            raise EvidenceError('会话快照格式无效，拒绝提取') from None
    wal = sources['.wal']
    if wal:
        lines = wal.split(b'\n')
        last = len(lines) - 1
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line.decode('utf-8'), object_pairs_hook=_unique_pairs))
            except (UnicodeError, json.JSONDecodeError):
                if index == last and not wal.endswith(b'\n'):
                    continue  # 只忽略未写完的最后一行；所选结果仍须齐全。
                raise EvidenceError('WAL 中间或完整行损坏，拒绝提取') from None
    if any(not isinstance(message, dict) for message in messages):
        raise EvidenceError('会话消息格式无效，拒绝提取')
    return messages


def _records(messages, call_ids):
    calls, results = {}, {}
    wanted = set(call_ids)
    for message in messages:
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls') or []:
                if not isinstance(call, dict) or not isinstance(call.get('id'), str) or call['id'] not in wanted:
                    continue
                key = call['id']
                if key in calls and calls[key] != call:
                    raise EvidenceError('同一调用 ID 的调用记录冲突，拒绝提取')
                calls[key] = call
        elif (message.get('role') == 'tool' and isinstance(message.get('tool_call_id'), str)
              and message['tool_call_id'] in wanted):
            key, content = message['tool_call_id'], message.get('content')
            if key in results and results[key] != content:
                raise EvidenceError('同一调用 ID 的结果记录冲突，拒绝提取')
            results[key] = content
    records = []
    for key in dict.fromkeys(call_ids):
        if key not in calls or key not in results:
            raise EvidenceError('所选调用或原始结果缺失，不能从摘要补写')
        call, response = calls[key], results[key]
        function = call.get('function')
        if (set(call) - {'id', 'type', 'function'} or call.get('type', 'function') != 'function'
                or not isinstance(function, dict) or set(function) - {'name', 'arguments'}
                or function.get('name') not in ALLOWED_TOOLS):
            raise EvidenceError('只支持 Windows 桌面、系统和 SSH 工具的标准调用记录')
        arguments = function.get('arguments')
        try:
            parsed = json.loads(arguments, object_pairs_hook=_unique_pairs) if isinstance(arguments, str) else arguments
            if not isinstance(parsed, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise EvidenceError('所选调用参数格式无效') from None
        if not isinstance(response, str) or not response.strip():
            raise EvidenceError('所选原始工具结果为空或格式无效')
        decoded_response = response
        try:
            decoded, _ = json.JSONDecoder(object_pairs_hook=_unique_pairs).raw_decode(response.lstrip())
            decoded_response += '\n' + json.dumps(decoded, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        if TRIMMED.search(decoded_response):
            raise EvidenceError('所选工具结果已被裁剪或截断，必须重新取得完整结果')
        if (parsed.get('action') == 'type_secret'
                or SENSITIVE.search(json.dumps(call, ensure_ascii=False))
                or SENSITIVE.search(json.dumps(parsed, ensure_ascii=False))
                or SENSITIVE.search(decoded_response)):
            raise EvidenceError('所选输入或结果可能含密码、令牌、密钥或 Cookie，拒绝保存')
        records.append({'tool_call': call, 'raw_response': response})
    return records


def capture(workspace, sid, call_ids, output):
    if not isinstance(sid, str) or not SAFE_SID.fullmatch(sid):
        raise EvidenceError('会话 ID 只能包含字母、数字、下划线或连字符')
    if not call_ids or any(not isinstance(key, str) or not key.strip() or len(key) > 256 for key in call_ids):
        raise EvidenceError('必须显式指定非空的工具调用 ID')
    workspace = Path(workspace).resolve(strict=True)
    uploads = workspace / 'uploads'
    target = Path(output)
    if not target.is_absolute():
        target = uploads / target
    if target.is_symlink():
        raise EvidenceError('输出不能是符号链接或已有链接文件')
    target = target.resolve()
    if target.parent != uploads or not SAFE_OUTPUT.fullmatch(target.name):
        raise EvidenceError('输出必须是当前工作区 uploads 内的独立 JSON 文件')
    records = _records(_messages(_read_sources(workspace, sid), sid), call_ids)
    payload = json.dumps({'session_id': sid, 'records': records}, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    directory = os.open(uploads, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
        except FileExistsError:
            raise EvidenceError('输出文件已存在，拒绝覆盖；请使用新的文件名') from None
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
    finally:
        os.close(directory)
    return {'path': str(target), 'count': len(records)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--tool-call-id', action='append', required=True)
    parser.add_argument('--output', required=True, help='uploads 内的新 JSON 文件名或完整路径')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(capture(WORKSPACE, args.session, args.tool_call_id, args.output), ensure_ascii=False))
    except (EvidenceError, OSError) as error:
        # 系统异常可能包含未经验证的路径；不在错误消息中回显源数据。
        reason = str(error) if isinstance(error, EvidenceError) else '无法安全读取来源或创建受限证据文件'
        print('ERROR: ' + reason, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
