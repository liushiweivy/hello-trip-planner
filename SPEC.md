# Hello 智能旅行规划助手 设计规格（SPEC）

> 本文件是本项目唯一事实源。可直接编辑；改完对 Claude 说「按 SPEC 实现」即驱动代码对齐。
> 实现完成后对应任务会被勾选、变更记录会追加。规格与代码冲突时以本文件为准。

## 1. 目标与范围

做一个「先选方案再出详情」的智能旅行规划应用：用户输入目的地/日期/偏好，后端结合高德实时数据（POI/酒店/天气）与火山方舟推理大模型（ark-code-latest，OpenAI 兼容端点），一次生成 4 套固定风格（经典全景/深度慢游/经济实惠/自然风光）的方案大纲；用户选定后扩写为完整行程详情；预算由后台任务按需懒生成。系统带企业级治理能力：trace_id 全链路、prompt 版本化、输入边界校验、LLM 主备 failover + 断路器、日 token 成本熔断、行程持久化与历史回填、异步任务队列、大纲契约评测集，并内置压测监控台与日志查看页。

明确不做：用户鉴权/账号体系（有意暂缓，用 X-Owner-Key 轻量归属过渡）；支付、订票等交易闭环；预算结果不入库（只短期缓存 24h）。

## 2. 需求

- [x] US-01 作为用户，我输入城市/起止日期/天数/交通/住宿/偏好/自由文本，得到 4 套风格互异的方案大纲。验收：`POST /api/trip/plan` 返回 `TripOutlineData`（plans 恰 4 套、plan_id 1-4、风格互异、天数覆盖、每天 1-3 个景点、经济版预算低于经典版）；响应带 `source`（llm/cache/seed）与 `record_id`。
- [x] US-02 作为用户，我选定一套大纲后得到完整详情。验收：`POST /api/trip/detail` 返回 `TripPlan`；每天景点名称/坐标被大纲锁定（LLM 只补描述/门票/三餐/酒店）；`budget` 字段恒为 null（预算异步化）；有 `record_id` 时优先 stored 回放（零 LLM）。
- [x] US-03 作为用户，预算在我打开详情时后台懒生成并可重试。验收：提交 `kind=budget` 任务 → 轮询得 `BudgetJobResult`（source=llm/cache/mock/fallback）；同内容同天重提幂等命中；失败任务重试会另起新任务。
- [x] US-04 作为用户，我能看到/回填/删除我的历史行程。验收：`GET /api/plans`（需 X-Owner-Key）返回摘要列表；`GET /api/plans/{id}` 回填请求快照+大纲；`DELETE` 级联删详情；跨 owner 不可见（404）。
- [x] US-05 作为用户，相同查询第二次秒开。验收：相同城市/天数/交通/住宿/排序偏好/自由文本（不含日期）命中 `plan_cache`（source=cache）；10 个热门城市 3 天无偏好请求命中预制路线（source=seed，零 token）；日期 serve 时重排、天气实时重查。
- [x] US-06 作为用户，长行程（>5 天）详情也能成功。验收：`outline.days > 5` 时按 5 天/块分段生成再合并；单块失败只降级该块为兜底，不拖垮整趟；截断（finish_reason=length）用 32K 预算+压缩指令重试一次。
- [x] US-07 作为运维者，我能压测并观测每次 LLM 调用。验收：`/loadtest` 页支持 1-50 虚拟用户、mock/真实、多城市轮询分配；`/api/metrics/recent|summary|batches` 提供耗时/p50/p95/四类 token/推理占比/排队耗时/按阶段/按城市/按状态聚合。
- [x] US-08 作为运维者，成本可控、故障可降级。验收：`DAILY_TOKEN_BUDGET` 超限 → 429（缓存/seed 命中与 mock 不拦截）；主 LLM 连续 3 失败熔断 300s 直发备用；`GET /api/cost/status` 可查预算与断路器状态。
- [x] US-09 作为安全底线，畸形输入在边界被拒。验收：city 等短标签禁 `<>"/控制符且 ≤20 字；偏好 ≤6 项；free_text ≤500 字；日期 YYYY-MM-DD 且 start≤end；违规 → 422（XSS 探针实测拦截）。
- [x] US-10 作为开发者，每次请求有 trace_id 贯穿日志/监控/任务。验收：X-Request-ID 入站接力/生成/响应头回写；日志行含 trace_id；`request_metrics.trace_id` 与 `jobs.trace_id` 落库。
- [x] US-11 作为质量保障，大纲输出有契约评测集。验收：`backend/eval/outline_contract.py` 校验黄金样本（杭州 2 天）通过、10 城预制路线全量回归通过、反例与逐条变异被拒。
- [ ] US-12 用户鉴权与账号体系（owner_key → user_id 迁移）——有意暂缓。
- [ ] US-13 预算生成从「打开详情即触发」演进为可选的 4 方案预算预取开关（周额度紧张时的成本控制手段）。

## 3. 架构

```
浏览器 Vue3/Vite (:5173)                压测/日志页 (静态, :8000/loadtest, /api/logs)
   │ Home.vue / Result.vue                    │
   │ axios（X-Owner-Key 头, 轮询 jobs）        │
   ▼                                          ▼
┌────────────────────── FastAPI (:8000) ──────────────────────┐
│ TraceIdMiddleware（X-Request-ID 接力/生成/回写）              │
│ routes/  trip · jobs · plans · cache · cost · metrics ·     │
│          logs · poi · map                                   │
│                                                             │
│ services/                                                   │
│  trip_execution  同步路由与 worker 共用的执行器（持久化唯一份）│
│  agents/trip_agent  两阶段规划 + 分段详情 + plan_budget        │
│  llm_service      主备双 LLM 单例 + 断路器 + usage 捕获补丁   │
│  prompt_service   app/prompts/*.md 文件优先 + 版本提取        │
│  cost_service     日 token 预算熔断（BudgetExceeded→429）     │
│  cache_service    plan_cache 命中/回写 + seed_data 预制路线   │
│  budget_cache     Redis(db=1) 24h TTL → 内存降级              │
│  plan_store       trip_records/trip_details（owner 归属）     │
│  job_service      jobs 表 + asyncio worker(并发2, 1s轮询)     │
│  metrics_service  request_metrics 落库 + 聚合                 │
│  amap_service     高德 POI/酒店/天气（进程内限速 0.4s/请求）  │
│  tracing / logging_setup / unsplash_service                 │
└──────┬───────────────┬──────────────┬───────────────────────┘
       ▼               ▼              ▼
  火山方舟 LLM     MySQL 8        Redis (db=1)
  ark-code-latest  trip_planner库  trip:budget:* (24h)
  (/api/coding/v1) 5 表, 连不上全  连不上降级内存
  主备双 key       部降级内存      （重启即失, 符合设计）
  +断路器
```

| 模块 | 职责 |
|------|------|
| `routes/trip.py` | 两阶段规划同步端点 + 健康检查；429/500 错误映射 |
| `routes/jobs.py` | 异步任务提交（202，幂等）与轮询；提交时复用同一套模型校验 |
| `routes/plans.py` | 历史行程列表/回填/删除（按 X-Owner-Key 过滤） |
| `routes/cache.py` | 缓存状态/清理（seed 默认保护） |
| `routes/cost.py` | 预算用量 + 断路器/备用供应商状态 |
| `routes/metrics.py` | LLM 调用明细/聚合/批次查询（监控台数据源） |
| `routes/logs.py` | 后端日志 HTML 页（5s 自刷）与纯文本 |
| `routes/poi.py`、`routes/map.py` | 高德 POI/天气/路线与 Unsplash 配图透传 |
| `services/trip_execution.py` | outline/detail/budget 执行器，同步路由与 worker 共用 |
| `services/job_service.py` | jobs 表、asyncio worker（并发 2）、启动恢复（running→queued） |
| `services/llm_service.py` | 主备 LLM、断路器（3 连败熔断 300s）、invoke 补丁捕获 usage |
| `services/cache_service.py` + `seed_data.py` | 运行时缓存（TTL 7 天）+ 10 城×4 风格×3 天预制路线（永不过期） |
| `services/budget_cache.py` | 预算 24h 缓存（Redis 优先、内存降级）、幂等键重写 |
| `services/cost_service.py` | 日 token 预算（0=不限），仅真实烧 token 前检查 |
| `services/plan_store.py` | 行程/详情持久化，MySQL 优先内存降级 |
| `services/metrics_service.py` | 每次 LLM 调用落一行指标，summary 聚合（by_phase/by_city/by_status） |
| `services/prompt_service.py` | prompt 文件加载与版本提取（`<!-- prompt_version: xxx -->`） |
| `services/tracing.py` | ContextVar trace_id + 纯 ASGI 中间件 |
| `frontend/src/views/Home.vue` | 需求表单 + 任务提交（crypto.randomUUID 幂等键 + 3s 轮询）+ 历史行程 |
| `frontend/src/views/Result.vue` | select/detail 两阶段页：4 方案卡、后台预生成、骨架屏渐进展示、预算三态卡 |

## 4. 接口契约

统一约定：除标注外均为 `application/json`；归属类接口需 `X-Owner-Key` 头（前端 localStorage `trip_client_id`）；成功响应 `{success, message, data}` 或任务视图 `{success, job_id, ...}`；错误码：400 参数/归属头缺失，404 不存在或不属于当前用户，422 输入校验失败，429 成本熔断，500 服务端异常。

### 4.1 两阶段规划（`routes/trip.py`）

| 方法/路径 | 入参 | 出参 | 错误 |
|---|---|---|---|
| POST `/api/trip/plan` | body `TripRequest`：city*, start_date*, end_date*(YYYY-MM-DD 且 start≤end), travel_days*(1-30), transportation*, accommodation*, preferences[](≤6 项，单项 ≤20 字), free_text_input(≤500), client_tag, mock_llm | `TripOutlineResponse{success,message,data:TripOutlineData{city,start_date,end_date,plans[4],weather_info,source,record_id}}` | 422 校验失败；429 预算超限；500 |
| POST `/api/trip/detail` | body `TripDetailRequest{request:TripRequest, outline:PlanOutline, record_id?}` | `TripPlanResponse{...,data:TripPlan{city,dates,days[],weather_info,overall_suggestions,budget:null,source}}`；record_id 命中 trip_details 时 source=stored 零 LLM | 422；429；500 |
| GET `/api/trip/health` | — | `{status, service, model, amap_enabled}` | — |

### 4.2 异步任务（`routes/jobs.py`）

| 方法/路径 | 入参 | 出参 | 错误 |
|---|---|---|---|
| POST `/api/jobs` | body `JobSubmitRequest{kind:outline\|detail\|budget, payload(与对应同步端点请求体相同；budget 为 BudgetJobRequest), idempotency_key(8-80)}`；Header X-Owner-Key | 202 `{success, job_id, created, status}`；幂等命中 created=false 返回原任务；budget 幂等键服务端按「内容 sha256+日期」重写，失败任务重提自动加随机后缀另起 | 400 未知 kind；422 payload 校验失败 |
| GET `/api/jobs/{job_id}` | Header X-Owner-Key | `{success, job_id, kind, status(queued/running/succeeded/failed), result, error, trace_id, created_at,...}`；budget 任务 result 为 `BudgetJobResult{budget,source}` | 404 不存在或跨 owner |

### 4.3 历史行程（`routes/plans.py`，必须带 X-Owner-Key，缺失 400）

| 方法/路径 | 出参 | 错误 |
|---|---|---|
| GET `/api/plans` | `{success, data:[摘要...](id/city/travel_days/dates/prefs/styles/source/created_at), backend(mysql/memory)}` | 400 |
| GET `/api/plans/{record_id}` | `{success, data:{请求快照+4 套大纲}}` | 400；404 |
| DELETE `/api/plans/{record_id}` | `{success, message}`（级联删 trip_details） | 400；404 |

### 4.4 缓存与成本（`routes/cache.py`、`routes/cost.py`）

| 方法/路径 | 入参 | 出参 |
|---|---|---|
| GET `/api/cache/stats` | — | `{success, backend, data:{条目数/分类/命中排行}}` |
| POST `/api/cache/clear` | query: city, kind(outline/detail), include_seed(默认 false 保护预制路线) | `{success, backend, data:{removed}}` |
| GET `/api/cost/status` | — | 当日 token 预算用量 + 断路器/备用供应商状态 |

### 4.5 监控与日志（`routes/metrics.py`、`routes/logs.py`）

| 方法/路径 | 入参 | 出参 |
|---|---|---|
| GET `/api/metrics/recent` | limit(1-500, 默认 50), client_tag(批次过滤), mock(all/mock/real) | `{success, backend, data:[调用明细行...]}`（含耗时/四类 token/queue_ms/finish_reason/attempt/prefs/prompt_excerpt） |
| GET `/api/metrics/summary` | client_tag, mock | 成功率、avg/p50/p95/max、token 合计、reasoning_share、by_phase/by_city/by_status |
| GET `/api/metrics/batches` | limit(1-100, 默认 20) | 批次聚合（剥掉 `batch\|uN` 后缀） |
| GET `/api/logs` | lines(1-500, 默认 300) | HTML 页（5s 自刷，错误红/警告黄，已转义） |
| GET `/api/logs/raw` | lines | `{success, log_file, lines:[...]}` |

### 4.6 POI/地图透传（`routes/poi.py`、`routes/map.py`）

| 方法/路径 | 入参 | 出参 |
|---|---|---|
| GET `/api/poi/detail/{poi_id}` | path poi_id | `{success,message,data}`（高德 POI 详情） |
| GET `/api/poi/search` | keywords, city(默认"北京") | `{success,message,data}` |
| GET `/api/poi/photo` | name | `{success,...,data:{name,photo_url}}`（未配 Unsplash Key 时 photo_url 为 null） |
| GET `/api/map/poi` | keywords*, city*, citylimit(默认 true) | `POISearchResponse{...,data:[POIInfo...]}` |
| GET `/api/map/weather` | city* | `WeatherResponse{...,data:[WeatherInfo...]}` |
| POST `/api/map/route` | `RouteRequest{origin_address*,destination_address*,origin_city?,destination_city?,route_type(walking/driving/transit)}` | `RouteResponse{...,data:RouteInfo{distance,duration,route_type,description}}` |
| GET `/api/map/health` | — | `{status,service,amap_enabled}` |

### 4.7 其他

- `GET /loadtest`：压测监控台静态页（不在 OpenAPI schema）。
- `GET /`、`GET /health`：根信息与全局健康检查。
- 所有响应带 `X-Request-ID` 头（trace_id 回写）。

## 5. 数据模型与存储

### 5.1 MySQL（库 `trip_planner`，凭据见 .env.example；任何一张表连不上即该存储整体降级内存，重启丢数据并日志告警）

| 表 | 关键列 | 归属/说明 |
|---|---|---|
| `request_metrics` | trace_id, phase(outline/detail/detail_bN/repair/...), status(success/truncated/error/cache_hit...), city, plan_style, client_tag, llm_ms, queue_ms, prompt/completion/reasoning tokens, finish_reason, max_tokens, attempt, prefs, prompt_excerpt, ts | 每次 LLM 调用一行；city/plan_style 落库前截断 64 字；老表用 information_schema 差集自动补列 |
| `plan_cache` | cache_key(sha256, UNIQUE), kind(outline/detail), seed(0/1), city, travel_days, plan_id, payload(MEDIUMTEXT), hit_count, last_hit_at, expires_at | 运行时缓存 TTL 7 天（`cache_ttl_days`）；seed=1 预制路线永不过期；缓存键不含日期/client_tag/mock；详情键额外混入大纲风格+每天景点名签名 |
| `trip_records` | id PK, owner_key, city, travel_days, start/end_date, transportation, accommodation, prefs, free_text, styles, source, request_json, outlines_json, created_at；INDEX(owner_key, created_at) | 一次规划一条记录；按 owner 过滤读写 |
| `trip_details` | id PK, record_id, plan_id, plan_style, payload, created_at；UNIQUE(record_id, plan_id) UPSERT | 详情持久化；stored 回放数据源；**不存 budget**（回放与缓存命中两处都会剥除） |
| `jobs` | id(uuid) PK, kind(outline/detail/budget), status(queued/running/succeeded/failed), idempotency_key, owner_key, payload, result, error, trace_id, created/started/finished_at；UNIQUE(owner_key, idempotency_key) | 异步任务队列；启动时 running→queued 恢复 |

### 5.2 Redis（db=1，与本机其他项目隔离；未装 redis 包/服务不通降级进程内 TTL dict）

| 键 | 值 | TTL | 说明 |
|---|---|---|---|
| `trip:budget:{sha256}` | `BudgetJobResult{budget, source}` JSON | 86400s | 预算唯一存放处（不入 MySQL）；键=路线账单内容（门票/酒店/餐饮/交通/城市）sha256，不含日期/天气/坐标/描述 |

### 5.3 前端存储

| 位置 | 键 | 内容 |
|---|---|---|
| localStorage | `trip_client_id` | 轻量归属标识，随 X-Owner-Key 头发送 |
| sessionStorage | `tripOutlines` / `tripRequest` / `tripPlan` / `tripPlanId` | 大纲 / 原始请求 / 已选详情 / 详情归属；Home 提交新规划时清 tripPlan |

### 5.4 配置文件与 Prompt

- `.env`（不入库，见 `.env.example`）：LLM 主备 key 与端点、高德 key、MySQL DSN、`REDIS_URL`、`DAILY_TOKEN_BUDGET`（默认 0 不限）、`llm_max_concurrency`(4)、`cache_ttl_days`(7) 等。
- `backend/app/prompts/{outline,planner,budget}.md`：首行 `<!-- prompt_version: xxx -->` 注释即版本号；文件优先，agent 内联常量兜底。
- `backend/logs/backend.log`：5MB×3 滚动 + 内存 500 行环形缓冲（日志页数据源）。

## 6. 关键设计决策

1. **两阶段生成而非一次出全量**：推理模型慢（单次 145-300s），先出 4 套轻量大纲让用户有选择权，再对选定方案扩详情；前端进 select 页即并发预生成 4 份详情 + 骨架屏渐进展示，用 token 换秒开体验。
2. **4 套固定风格卡位**：经典全景/深度慢游/经济实惠/自然风光写死在 prompt 里，防止自由生成时风格趋同；经济版要求优先免费低价且预算显著低。
3. **详情锁定大纲选点**：详情 prompt 明令禁止改/增删景点名称与坐标，LLM 只补描述/门票/三餐/酒店——保证 select 页骨架与最终详情坐标一致，地图标点与图片缓存无需重建。
4. **缓存只缓存创作成果**：缓存键不含日期（serve 时按请求日期重排每天日期），天气实时重查；坐标缺失景点命中后由高德反查补全并回写（上限 40 个，禁编坐标原则不破）。分段详情有降级块时不入缓存。
5. **预算异步化且不入库**（2026-10-24）：预算是 4 份详情里最贵又最少被看的部分，改为用户打开某方案时才提交 kind=budget 任务；结果只活 24h（Redis/内存），天然规避「预算过时」问题；幂等键服务端按内容重写，前端双击/换页不重复烧 token。
6. **主备 failover + 断路器**：主备双 LLM 单例跨账号容灾；主连续 3 失败熔断 300s 期间直发备用，冷却后半开探测；usage 记 provider，监控 model 列加 `@backup` 后缀归因。
7. **成本熔断只在真烧钱前检查**：`ensure_budget()` 在缓存/seed 未命中、确定要调 LLM 时才查当日 token；命中与 mock 零成本不拦截，避免误伤。
8. **usage 捕获靠 invoke 补丁**：HelloAgentsLLM.invoke 丢弃 response.usage，补丁直调底层 client 把 usage 存 threading.local；调用方必须在同一执行线程取（`_llm_call_sync` 把 run+取 usage 放同一次 run_in_executor）。
9. **任务幂等两级**：客户端 crypto.randomUUID 幂等键（UNIQUE(owner, idem) 去重）；budget 额外由服务端按内容+日期重写幂等键。worker 进程内 asyncio（并发 2，LLM 并发另有 Semaphore(4) 闸门），启动恢复 running→queued。
10. **已知坑（勿踩）**：
    - 后端重启必须用 venv 的 python（系统 Python 没装 hello_agents 依赖）；`run.py` 强制 reload=False（Windows 下 reload 会卡死并留 spawn_main 孤儿占 :8000）。
    - pymysql 每操作新连接时 `SELECT LAST_INSERT_ID()` 跨连接无效，必须在同一游标取 `cur.lastrowid/cur.rowcount`。
    - 高德免费 key 有 QPS 上限：进程内限速 0.4s/请求 + CUQPS 退避重试 3 次（单靠 Semaphore 不够，会整组候选静默丢失）。
    - 推理模型思维链吃 max_tokens：大纲预算 24576（16K 会截断 JSON 正文）；截断不做同预算重试，改 32K+压缩指令重试一次。
    - 多实例共用 MySQL jobs 表时另一实例的 worker 会抢任务（E2E 验证降级时须先杀 :8000 实例）。
    - 降级是首次使用时判定的：MySQL/Redis 恢复后须重启后端才能切回。
    - 监控台渲染请求体可控字符串（city/client_tag/plan_style）必须 esc() 防存储型 XSS（已修，勿回退）。

## 7. 任务清单

- [x] 两阶段规划：/api/trip/plan 出 4 套固定风格大纲 → /api/trip/detail 锁点扩详情
- [x] 前端 select/detail 两阶段页：后台 4 并发预生成 + 骨架屏渐进展示 + 详情按 plan_id 缓存
- [x] 高德采集纠偏：6 个跨类别关键词 + 进程内限速 + CUQPS 退避
- [x] 监控体系：request_metrics 落 MySQL + /loadtest 压测台（多城市轮询、mock 模式、批次聚合）
- [x] 观测增强：queue_ms/finish_reason/attempt/prefs/prompt_excerpt 六列 + by_phase/by_city/by_status 聚合
- [x] 相同查询缓存（plan_cache，键不含日期）+ 10 城预制路线库（seed，零 token）
- [x] 长行程治理：截断检测与 32K 重试 + >5 天按 5 天/块分段详情 + LLM 并发闸门
- [x] trace_id 全链路（中间件 + 日志 + 监控列 + 任务列）
- [x] Prompt 版本化（app/prompts/*.md + 版本提取，🤖 日志带版本）
- [x] 输入边界校验（XSS 探针实测 422；jobs 提交同套校验）
- [x] LLM 主备 failover + 断路器（3 连败熔断 300s）
- [x] 成本熔断（DAILY_TOKEN_BUDGET，超限 429）+ /api/cost/status
- [x] 行程持久化（trip_records/trip_details + X-Owner-Key 归属 + stored 回放 + 历史列表/删除）
- [x] 异步任务化（jobs 表 + worker + 幂等 + 启动恢复 + 前端任务流提交）
- [x] 评测集（outline_contract 契约校验器 + 黄金样本/反例/10 城全量回归）
- [x] 预算异步化（2026-10-24）：详情不产预算，kind=budget 懒生成，Redis db=1 24h 缓存，预算不入库
- [x] 测试：backend/tests pytest 全绿（90+ 项，含契约评测与预算缓存套件）
- [ ] 用户鉴权与账号体系（owner_key → user_id；Home 历史行程随之切换）
- [ ] 详情导出图片/PDF 的回归验证（克隆导出前须强制展开所有折叠面板）
- [ ] summary 聚合加时间窗/下推 GROUP BY（当前全表扫描，数据量大后会慢）
- [ ] 删除无调用方的旧 `plan_trip()` 直通方法（无闸门无监控，勿重新接线）
- [ ] 预算 4 方案预取的可配置开关（周额度充裕时恢复秒开体验）

## 8. 变更记录

| 日期 | 变更 | 状态 |
|------|------|------|
| 2026-10-24 | 创建 SPEC.md（以 HANDOFF.md 与 routes/schemas/services 源码核实为准）；新增项目根 CLAUDE.md「SPEC 驱动开发」一节 | 已完成 |
