"""后端日志查看路由：浏览器直接打开 http://localhost:8000/api/logs"""

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from ...logging_setup import LOG_FILE, tail_lines

router = APIRouter(prefix="/logs", tags=["日志"])

_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>旅行助手 · 后端日志</title>
<meta http-equiv="refresh" content="5">
<style>
body{{background:#0d1117;color:#c9d1d9;font:13px/1.6 Consolas,Menlo,monospace;margin:0;padding:16px}}
h1{{font-size:15px;color:#58a6ff;margin:0 0 4px}}
.meta{{color:#8b949e;margin-bottom:12px}}
pre{{white-space:pre-wrap;word-break:break-all;margin:0}}
.w{{color:#d29922}} .e{{color:#f85149}} .i{{color:#c9d1d9}}
</style></head><body>
<h1>旅行助手 · 后端日志（最近 {n} 行，5 秒自动刷新）</h1>
<div class="meta">日志文件：{path}</div>
<pre>{body}</pre>
</body></html>"""


@router.get("", response_class=HTMLResponse, summary="查看后端日志（HTML，5 秒自刷）")
def view_logs(lines: int = Query(300, ge=1, le=500)):
    import html

    rows = tail_lines(lines)
    parts = []
    for row in rows:
        cls = "e" if "| ERROR" in row else ("w" if "| WARNING" in row else "i")
        parts.append(f'<span class="{cls}">{html.escape(row)}</span>')
    body = "\n".join(parts) or "（暂无日志）"
    return _PAGE.format(n=len(rows), path=LOG_FILE, body=body)


@router.get("/raw", summary="查看后端日志（纯文本）")
def view_logs_raw(lines: int = Query(300, ge=1, le=500)):
    return {"success": True, "log_file": LOG_FILE, "lines": tail_lines(lines)}
