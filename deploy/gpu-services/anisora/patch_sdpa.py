"""无 flash-attn 时让 wan 的 flash_attention 回退到 PyTorch SDPA (带 k_lens 的 key padding mask)。
官方代码在 Ulysses / cross-attn 路径直接 assert FLASH_ATTN_2_AVAILABLE; 本镜像 torch 2.11+cu130 没有现成 flash-attn 轮子。"""
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
marker = "def flash_attention("
assert marker in s and "_litecode_sdpa" not in s
fallback = '''def _litecode_sdpa(q, k, v, q_lens=None, k_lens=None, softmax_scale=None, q_scale=None, causal=False, dtype=torch.bfloat16):
    # q/k/v: [B, L, N, C]
    out_dtype = q.dtype
    if q_scale is not None:
        q = q * q_scale
    q, k, v = (t.transpose(1, 2).to(dtype) for t in (q, k, v))
    mask = None
    # 只对短 key 序列 (文本 cross-attn, Lk<=4096) 加 padding mask; 长的 self-attn (Ulysses 补齐最多 sp_size-1 个 token)
    # 加 mask 会让 SDPA 物化 [Lq,Lk] 的掩码/走 math 内核, 显存暴涨, 所以与官方 SDPA 回退一致不加
    if k_lens is not None and k.shape[2] <= 4096 and int(k_lens.min()) < k.shape[2]:
        mask = (torch.arange(k.shape[2], device=k.device)[None, :] < k_lens.to(k.device)[:, None])[:, None, None, :]
    out = torch.nn.functional.scaled_dot_product_attention(q, k, v, attn_mask=mask, is_causal=causal, scale=softmax_scale)
    return out.transpose(1, 2).contiguous().to(out_dtype)


'''
s = s.replace(marker, fallback + marker, 1)
# 在 flash_attention 函数体最前面插入回退分支
m = re.search(r"def flash_attention\((?:.|\n)*?\):\n(\s+)", s)
indent = m.group(1)
body_start = m.end() - len(indent)
ins = (f"{indent}if not (FLASH_ATTN_2_AVAILABLE or FLASH_ATTN_3_AVAILABLE):\n"
       f"{indent}    return _litecode_sdpa(q, k, v, q_lens=q_lens, k_lens=k_lens, softmax_scale=softmax_scale, q_scale=q_scale, causal=causal, dtype=dtype)\n")
s = s[:body_start] + ins + s[body_start:]
open(p, "w", encoding="utf-8").write(s)
print("patched", p)
