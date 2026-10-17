"""Windows 节点代码下发签名: 控制端用私钥签名清单 (各文件 sha256 + 版本), 节点用安装时固定的公钥验签。

方案: RSA-3072 / PKCS#1 v1.5 / SHA-256。
- 控制端签名用 cryptography (容器已安装); 私钥只在 .secrets/windows_nodes/signing_key.pem (0600), 从不下发。
- 节点端 venv 没有任何密码学库, 标准库只有哈希/HMAC: RSA 验签只需一次 pow + 与期望编码整体比较,
  纯标准库十几行即可 (windows_node_agent.rsa_verify); Ed25519 要在节点端手写曲线运算, 代码多且难审。
- 安装脚本运行在 PowerShell 5.1 (.NET Framework), 原生 RSACryptoServiceProvider 能验 RSA, 不支持 Ed25519。
公钥指纹 = sha256("<alg>:<n base64>:<e base64>"), 安装命令携带该指纹作为可信锚点。
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time

KEY_ALG = 'RSA-PKCS1v15-SHA256'
MANIFEST_FORMAT = 'litecode-windows-manifest-v1'
KEY_BITS = 3072
_LIB = Path(__file__).resolve().parent
_CACHE = {}          # workspace -> (清单内容键, 已签名清单); 单槽: 内容一变就以新时间重签
_CACHE_LOCK = threading.Lock()


def available():
    try:
        import cryptography.hazmat.primitives.asymmetric.rsa  # noqa: F401
        return True
    except ImportError:
        return False


def _b64(data):
    return base64.b64encode(data).decode()


def _int_bytes(value):
    return value.to_bytes((value.bit_length() + 7) // 8, 'big')


def fingerprint(key):
    """公钥指纹: 节点端 (Python / PowerShell) 用同样的字符串拼接计算。"""
    return hashlib.sha256((key['alg'] + ':' + key['n'] + ':' + key['e']).encode()).hexdigest()


@contextmanager
def _locked(folder):
    import fcntl
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(folder / 'signing.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _private_key(workspace):
    """读取签名私钥, 不存在则生成 (持锁, 0600, 先写临时文件再原子改名)。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    folder = Path(workspace) / '.secrets/windows_nodes'
    path = folder / 'signing_key.pem'
    with _locked(folder):
        if not path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            temporary = path.with_name(path.name + '.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(pem)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        return serialization.load_pem_private_key(path.read_bytes(), password=None)


def public_key(workspace):
    numbers = _private_key(workspace).public_key().public_numbers()
    return {'alg': KEY_ALG, 'n': _b64(_int_bytes(numbers.n)), 'e': _b64(_int_bytes(numbers.e))}


def public_key_info(workspace):
    key = public_key(workspace)
    return {'key': key, 'fingerprint': fingerprint(key)}


def manifest_files():
    """代理与全部组件按路由实际下发的字节 (read_text 后 UTF-8) 计算完整 sha256。"""
    from lib.windows_nodes import WORKER_FILES
    return {name: hashlib.sha256(_LIB.joinpath(name).read_text().encode()).hexdigest()
            for name in ('windows_node_agent.py',) + WORKER_FILES}


def signed_manifest(workspace):
    """当前清单的签名包 {manifest: base64(清单 JSON 字节), signature, fingerprint}。
    issued 是首次为这份内容签名的时间, 节点拒绝早于已接受清单的 issued (防回放旧版本)。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lib.windows_nodes import component_versions
    content = {'versions': component_versions(), 'files': manifest_files()}
    cache_key = json.dumps(content, sort_keys=True)
    workspace = str(Path(workspace).resolve())
    with _CACHE_LOCK:
        cached = _CACHE.get(workspace)
        if cached and cached[0] == cache_key:
            return dict(cached[1])
        key = _private_key(workspace)
        manifest = {'format': MANIFEST_FORMAT, 'issued': time.time(), **content}
        message = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        numbers = key.public_key().public_numbers()
        pub = {'alg': KEY_ALG, 'n': _b64(_int_bytes(numbers.n)), 'e': _b64(_int_bytes(numbers.e))}
        signed = {'manifest': _b64(message), 'signature': _b64(signature), 'fingerprint': fingerprint(pub)}
        _CACHE[workspace] = (cache_key, signed)
        return dict(signed)


def bootstrap_sha256():
    """安装命令携带的引导脚本指纹, 按路由实际下发的字节 (read_text 后 UTF-8) 计算。"""
    return hashlib.sha256(_LIB.joinpath('windows_node_bootstrap.ps1').read_text().encode()).hexdigest()


def uninstall_sha256():
    return hashlib.sha256(_LIB.joinpath('windows_node_uninstall.ps1').read_text().encode()).hexdigest()


def agent_sha256():
    return hashlib.sha256(_LIB.joinpath('windows_node_agent.py').read_text().encode()).hexdigest()
