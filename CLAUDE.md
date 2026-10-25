# Claude Code 项目指引

## SPEC 驱动开发

- `SPEC.md` 是本项目唯一事实源：用户改 SPEC 后说「按 SPEC 实现」，先 diff 规格与代码差异，列实现计划再动手
- 实现完成后：勾选 SPEC.md 第 7 节对应任务、在第 8 节追加变更记录（日期|变更|状态）
- 规格与代码冲突时以 SPEC.md 为准，除非变更记录注明有意偏离

## 关键备忘

- 权威交接文档见 `HANDOFF.md`（历次大改的坑与验证记录）；本仓库为公开仓，密钥只放 `.env`（见 `.env.example`），不入库不入文档
- 启动：双击 `start.bat`（后端 :8000 + 前端 :5173）；重启后端必须用 `backend/.venv` 的 python，勿用系统 Python
- 测试：`cd backend && pytest tests/ -q`
