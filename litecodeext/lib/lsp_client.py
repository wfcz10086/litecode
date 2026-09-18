"""
lsp_client.py — LSP 集成骨架 (P18)
====================================
go-to-definition / find-references / rename via Language Server Protocol.
极速骨架: stdio JSON-RPC 套壳, 真集成留 v1.6.
"""
from typing import List, Dict, Optional


def goto_definition(file_path: str, line: int, col: int) -> Optional[Dict]:
    """返回 {file, line, col} 或 None"""
    return None


def find_references(file_path: str, line: int, col: int) -> List[Dict]:
    return []


def rename_symbol(file_path: str, line: int, col: int, new_name: str) -> Dict:
    return {"changes": [], "ok": False, "note": "P18 skeleton"}
