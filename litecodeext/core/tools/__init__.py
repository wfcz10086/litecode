"""core/tools/ — tool_dispatch helper domains (T-52d-1 split).

Each submodule is a self-contained helper package that tool_dispatch.py
re-imports back at module scope. Preserves the original attribute surface
(e.g. `tool_dispatch._do_web_fetch` still works) so callers need no changes.
"""
