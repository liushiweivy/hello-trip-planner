# HelloAgents 智能旅行助手 🌍✈️

输入目的地、日期、偏好，自动生成含每日景点/餐饮/酒店/天气/预算的完整旅行计划。

> 本项目由《Hello-Agents》第 13 章示例 `helloagents-trip-planner` 拷贝并优化而来。
> 原版依赖 uvx 启动 amap-mcp-server 子进程、4 个 Agent 串行靠文本约定驱动工具；
> 本版改为 **高德 REST 直连 + 单次高质量 LLM 规划**，Windows 本地一条命令即可跑通。

## 与原版的差异（v2.0 优化点）

| 项 | 原版 | 本版 |
|----|------|------|
| 地图能力 | uvx 启动 `amap-mcp-server` MCP 子进程（需装 uv） | httpx 直连高德 Web 服务 REST API，无子进程 |
| Agent 结构 | 景点/天气/酒店/规划 4 个 SimpleAgent 串行，靠 `[TOOL_CALL:...]` 文本约定 | 高德数据并发预取 → 1 个规划 Agent 一次成稿，JSON 解析失败自动修复重试 1 次 |
| 数据解析 | `amap_service.py` 全是 TODO，永远返回空列表 | 真正解析 POI/天气/路线，坐标真实可用 |
| LLM | OpenAI 占位 | 火山方舟 Coding Plan `ark-code-latest`（与 Claude Code 同模型，OpenAI 兼容接口） |
| 高德 Key | 不填无法启动 | 可选：不填走纯 LLM 知识规划模式，启动不阻断 |
| 前端地图 | 无 JS Key 直接报错 | 无 Key 显示占位卡片；新 Key 自动注入 `securityJsCode` |
| 同步/异步 | async 路由里跑阻塞 MCP 调用 | REST 调用走线程池，POI/酒店/天气并发采集 |
| 编码 | Windows GBK 控制台打印 emoji 崩溃 | 包入口统一 stdout UTF-8 |

## 快速开始

前置：Python 3.10+、Node.js 16+。

**一键启动（Windows）**：双击 `start.bat`，首次会自动创建虚拟环境并安装依赖，
随后弹出两个窗口分别运行后端 (:8000) 与前端 (:5173)。

手动启动：

```bash
# 后端
cd backend
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # 填入 LLM_API_KEY（高德 Key 可选）
python run.py

# 前端
cd frontend
npm install
copy .env.example .env          # JS Key 可选
npm run dev
```

打开 http://localhost:5173 ，API 文档 http://localhost:8000/docs 。

## Key 申请

| Key | 必需 | 申请地址 | 填到哪里 |
|-----|------|---------|---------|
| 火山方舟 API Key（Coding Plan） | ✅ | 方舟控制台（与 Claude Code 同一个） | `backend/.env` 的 `LLM_API_KEY` |
| 高德 Web服务 Key | 可选 | https://console.amap.com/dev/key/app （平台选「Web服务」） | `backend/.env` 的 `AMAP_API_KEY` |
| 高德 Web端 JS Key + 安全密钥 | 可选 | 同上（平台选「Web端(JS API)」） | `frontend/.env` |
| Unsplash Access Key | 可选 | https://unsplash.com/oauth/applications | `backend/.env` |

不填高德 Key：行程、餐饮、酒店、预算由 LLM 知识生成（坐标为模型知识，可能有偏差）；
填入后：景点/酒店来自高德实时 POI（真实坐标），天气来自高德实时预报。

## 技术栈

- 后端：FastAPI + hello-agents 0.2.9（SimpleAgent + HelloAgentsLLM）+ httpx
- 前端：Vue 3 + TypeScript + Vite 6 + Ant Design Vue 4 + 高德 JS API 2.0
- LLM：火山方舟 `ark-code-latest`（OpenAI 兼容协议，温度 0.3，16K max_tokens）
- 预算：路线定稿后由后台任务独立生成（不入库），Redis 24h 短期缓存（未装/连不上自动降级进程内缓存）

## 主要接口

- `POST /api/trip/plan` — 生成旅行计划（推理模型耗时约 2–5 分钟，前端已设 5 分钟超时）
- `POST /api/jobs` + `GET /api/jobs/{id}` — 异步任务（大纲/预算；幂等提交 + 轮询）
- `GET  /api/map/poi?keywords=&city=` — POI 搜索（需高德 Key）
- `GET  /api/map/weather?city=` — 天气预报（需高德 Key）
- `POST /api/map/route` — 步行/驾车/公交路线规划（需高德 Key）

## 项目结构

```
hello-trip-planner/
├── start.bat                  # Windows 一键启动
├── backend/
│   ├── run.py
│   ├── .env / .env.example
│   └── app/
│       ├── agents/trip_planner_agent.py   # 数据预取 + 规划 Agent + 解析/兜底
│       ├── api/                           # FastAPI 路由（trip/poi/map）
│       ├── services/                      # llm_service / amap_service(REST) / unsplash
│       ├── models/schemas.py
│       └── config.py
└── frontend/
    ├── .env / .env.example
    └── src/ (views/Home.vue, views/Result.vue, services/api.ts ...)
```

## 说明

- 行程生成较慢（实测 2 天行程约 275 秒）是推理模型思维链较长所致，属正常现象。
- 高德免费版天气预报仅覆盖未来 3–4 天，超出日期由模型按季节常识补全。
- 开源协议：CC BY-NC-SA 4.0（沿用原教程）。
