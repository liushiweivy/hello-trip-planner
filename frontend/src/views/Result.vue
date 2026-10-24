<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-space size="middle">
        <a-button class="back-button" size="large" @click="goBack">
          ← 返回首页
        </a-button>
        <a-button v-if="phase === 'detail' && outlineData" size="large" @click="backToSelect">
          🗂️ 返回方案列表
        </a-button>
      </a-space>
      <a-space v-if="phase === 'detail'" size="middle">
        <a-button v-if="!editMode" @click="toggleEditMode" type="default" :disabled="detailPending">
          ✏️ 编辑行程
        </a-button>
        <a-button v-else @click="saveChanges" type="primary">
          💾 保存修改
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          ❌ 取消编辑
        </a-button>

        <!-- 导出按钮 -->
        <a-dropdown v-if="!editMode" :disabled="detailPending">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            📥 导出行程 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <!-- 阶段一：方案选择（3 套大纲 + 时间轴） -->
    <div v-if="phase === 'select' && outlineData" class="plan-select-wrapper">
      <div class="select-header">
        <h2 class="select-title">为「{{ outlineData.city }}」定制了 {{ outlineData.plans.length }} 套方案</h2>
        <p class="select-subtitle">
          {{ outlineData.start_date }} ~ {{ outlineData.end_date }} · 挑一套喜欢的，查看完整详情
          <span v-if="outlineData.source === 'seed'" class="source-badge seed">⚡ 热门城市预制路线 · 秒开</span>
          <span v-else-if="outlineData.source === 'cache'" class="source-badge cache">⚡ 缓存命中 · 秒开</span>
        </p>
      </div>
      <div class="plan-cards">
        <div v-for="plan in outlineData.plans" :key="plan.plan_id" class="plan-card">
          <div class="plan-card-head">
            <span class="plan-style">{{ plan.style }}</span>
            <span v-if="plan.estimated_budget" class="plan-budget">约 ¥{{ plan.estimated_budget }}/人</span>
          </div>
          <p class="plan-tagline">{{ plan.tagline }}</p>
          <ul v-if="plan.highlights && plan.highlights.length" class="plan-highlights">
            <li v-for="(h, i) in plan.highlights" :key="i">✨ {{ h }}</li>
          </ul>
          <a-timeline class="plan-timeline">
            <a-timeline-item v-for="day in plan.days" :key="day.day_index">
              <div class="timeline-day">
                D{{ day.day_index + 1 }} · {{ day.date.slice(5) }}<span v-if="day.title">｜{{ day.title }}</span>
              </div>
              <div class="timeline-spots">{{ spotsText(day) }}</div>
            </a-timeline-item>
          </a-timeline>
          <div class="detail-status">
            <span v-if="detailReady[plan.plan_id]" class="status-ready">✅ 详情已就绪，点击秒开</span>
            <span v-else-if="detailFailed[plan.plan_id]" class="status-failed">⚠️ 预生成失败，点击重试</span>
            <span v-else class="status-pending">⏳ 详情后台生成中…</span>
          </div>
          <a-button
            type="primary"
            block
            size="large"
            class="plan-detail-btn"
            @click="selectPlan(plan)"
          >
            查看完整方案 →
          </a-button>
        </div>
      </div>
    </div>

    <!-- 阶段二：选定方案的完整详情 -->
    <div v-if="phase === 'detail' && tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="overview">
              <span>📋 行程概览</span>
            </a-menu-item>
            <a-menu-item key="budget" v-if="tripPlan.budget || detailPending || budgetState !== 'idle'">
              <span>💰 预算明细</span>
            </a-menu-item>
            <a-menu-item key="map">
              <span>📍 景点地图</span>
            </a-menu-item>
            <a-sub-menu key="days" title="📅 每日行程">
              <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                第{{ day.day_index + 1 }}天
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 详情生成中：已有信息先展示，未生成的区块显示「规划中」 -->
        <a-alert
          v-if="detailPending"
          class="detail-pending-banner"
          type="info"
          show-icon
          message="🤖 详情生成中"
          description="景点、时间轴、地图已就位；餐饮、酒店正在规划中，完成后自动填充，无需等待。"
        />
        <a-alert
          v-else-if="detailError"
          class="detail-pending-banner"
          type="error"
          show-icon
          message="详情生成失败"
          :description="detailError"
        >
          <template #action>
            <a-button size="small" type="primary" @click="retryDetail">重试</a-button>
          </template>
        </a-alert>

        <!-- 顶部信息区:左侧概览+预算,右侧地图 -->
        <div class="top-info-section">
          <!-- 左侧:行程概览和预算明细 -->
          <div class="left-info">
            <!-- 行程概览 -->
            <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
              <div class="overview-content">
                <div class="info-item">
                  <span class="info-label">📅 日期:</span>
                  <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">💡 建议:</span>
                  <span class="info-value">{{ tripPlan.overall_suggestions || (detailPending ? '规划中…' : '') }}</span>
                </div>
              </div>
            </a-card>

            <!-- 预算明细 -->
            <a-card id="budget" v-if="tripPlan.budget" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="budget-grid">
                <div class="budget-item">
                  <div class="budget-label">景点门票</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_attractions }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">酒店住宿</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_hotels }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">餐饮费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_meals }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">交通费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_transportation }}</div>
                </div>
              </div>
              <div class="budget-total">
                <span class="total-label">预估总费用</span>
                <span class="total-value">¥{{ tripPlan.budget.total }}</span>
              </div>
            </a-card>

            <!-- 预算占位（详情生成中：路线就绪后预算才开工） -->
            <a-card v-else-if="detailPending" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="pending-placeholder">💰 路线就绪后自动生成预算…</div>
            </a-card>

            <!-- 预算占位（后台任务进行中：路线已可浏览） -->
            <a-card v-else-if="budgetState === 'pending'" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="pending-placeholder">💰 预算后台生成中…路线已就绪，不影响浏览</div>
            </a-card>

            <!-- 预算失败：可重试 -->
            <a-card v-else-if="budgetState === 'failed'" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="pending-placeholder">
                预算生成失败
                <a-button size="small" type="link" @click="retryBudget">重试</a-button>
              </div>
            </a-card>
          </div>

          <!-- 右侧:地图 -->
          <div class="right-map">
            <a-card id="map" title="📍 景点地图" :bordered="false" class="map-card">
              <div class="map-wrap">
                <!-- 容器必须始终存在，AMap.Map 初始化时要能找到该 div；占位层用遮罩实现 -->
                <div id="amap-container" style="width: 100%; height: 100%"></div>
                <div v-if="!mapReady" class="map-placeholder">
                  <p>🗺️ 地图未启用</p>
                  <span>在 frontend/.env 配置 VITE_AMAP_WEB_JS_KEY 后显示景点地图</span>
                </div>
              </div>
            </a-card>
          </div>
        </div>

        <!-- 每日行程:可折叠 -->
        <a-card title="📅 每日行程" :bordered="false" class="days-card">
          <a-collapse v-model:activeKey="activeDays" accordion>
            <a-collapse-panel
              v-for="(day, index) in tripPlan.days"
              :key="index"
              :id="`day-${index}`"
            >
              <template #header>
                <div class="day-header">
                  <span class="day-title">第{{ day.day_index + 1 }}天</span>
                  <span class="day-date">{{ day.date }}</span>
                </div>
              </template>

              <!-- 行程基本信息 -->
              <div class="day-info">
                <div class="info-row">
                  <span class="label">📝 行程描述:</span>
                  <span class="value">{{ day.description }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🚗 交通方式:</span>
                  <span class="value">{{ day.transportation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🏨 住宿:</span>
                  <span class="value">{{ day.accommodation }}</span>
                </div>
              </div>

              <!-- 景点安排 -->
              <a-divider orientation="left">🎯 景点安排</a-divider>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, column: 2 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card :title="item.name" size="small" class="attraction-card">
                      <!-- 编辑模式下的操作按钮 -->
                      <template #extra v-if="editMode">
                        <a-space>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'up')"
                            :disabled="index === 0"
                          >
                            ↑
                          </a-button>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'down')"
                            :disabled="index === day.attractions.length - 1"
                          >
                            ↓
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                          >
                            🗑️
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点图片 -->
                      <div class="attraction-image-wrapper">
                        <img
                          :src="getAttractionImage(item.name, index)"
                          :alt="item.name"
                          class="attraction-image"
                          @error="handleImageError"
                        />
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <div v-if="item.ticket_price" class="price-tag">
                          ¥{{ item.ticket_price }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong></p>
                        <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p><strong>描述:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ detailPending ? '规划中…' : `${item.visit_duration}分钟` }}</p>
                        <p><strong>描述:</strong> {{ item.description }}</p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
              <template v-else-if="detailPending">
                <a-divider orientation="left">🏨 住宿推荐</a-divider>
                <div class="pending-placeholder">🏨 酒店规划中…</div>
              </template>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <template #title>
                  <span class="hotel-title">{{ day.hotel.name }}</span>
                </template>
                <a-descriptions :column="2" size="small">
                  <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                  <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                </a-descriptions>
              </a-card>

              <!-- 餐饮安排 -->
              <template v-if="day.meals && day.meals.length">
                <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
                <a-descriptions :column="1" bordered size="small">
                  <a-descriptions-item
                    v-for="meal in day.meals"
                    :key="meal.type"
                    :label="getMealLabel(meal.type)"
                  >
                    {{ meal.name }}
                    <span v-if="meal.description"> - {{ meal.description }}</span>
                  </a-descriptions-item>
                </a-descriptions>
              </template>
              <template v-else-if="detailPending">
                <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
                <div class="pending-placeholder">🍽️ 三餐规划中…</div>
              </template>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-card id="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0" title="天气信息" style="margin-top: 20px" :bordered="false">
        <a-list
          :data-source="tripPlan.weather_info"
          :grid="{ gutter: 16, column: 3 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card size="small" class="weather-card">
                <div class="weather-date">{{ item.date }}</div>
                <div class="weather-info-row">
                  <span class="weather-icon">☀️</span>
                  <div>
                    <div class="weather-label">白天</div>
                    <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-info-row">
                  <span class="weather-icon">🌙</span>
                  <div>
                    <div class="weather-label">夜间</div>
                    <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-wind">
                  💨 {{ item.wind_direction }} {{ item.wind_power }}
                </div>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
        </a-card>
      </div>
    </div>

    <a-empty v-if="!outlineData && !tripPlan" description="没有找到旅行计划数据">
      <template #image>
        <div style="font-size: 80px;">🗺️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import type { Budget, OutlineDay, PlanOutline, TripFormData, TripOutlineData, TripPlan } from '@/types'
import { generateTripDetail, pollJob, submitJob } from '@/services/api'

const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const editMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const attractionPhotos = ref<Record<string, string>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([0]) // 默认展开第一天
let map: any = null
const mapReady = ref(false)

// 两阶段：select = 3 套方案大纲时间轴；detail = 选定方案的完整详情
const phase = ref<'select' | 'detail'>('select')
const outlineData = ref<TripOutlineData | null>(null)
const tripRequest = ref<TripFormData | null>(null)
// 已生成过的详情按方案号缓存，来回切换不重复调 LLM
const detailCache = new Map<number, TripPlan>()
// 后台预生成：进方案页立刻并发生成全部详情，用户看时间轴时详情已在路上
const detailTasks = new Map<number, Promise<TripPlan>>()
const detailReady = ref<Record<number, boolean>>({})
const detailFailed = ref<Record<number, boolean>>({})
// 渐进展示：骨架屏期间 true（已有信息先显示，未生成区块显示「规划中」）
const detailPending = ref(false)
const detailError = ref('')
const selectedOutline = ref<PlanOutline | null>(null)
// 预算：路线定稿后由后台任务生成（不入库，服务端 Redis 24h 缓存）。
// 只给「正在查看的方案」发预算任务，未查看的方案不烧 token。
const budgetCache = new Map<number, Budget>()
const budgetTasks = new Map<number, Promise<void>>()
const budgetState = ref<'idle' | 'pending' | 'ready' | 'failed'>('idle')
const currentPlanId = ref<number | null>(null)

onMounted(async () => {
  const outlinesRaw = sessionStorage.getItem('tripOutlines')
  const requestRaw = sessionStorage.getItem('tripRequest')
  if (outlinesRaw) {
    outlineData.value = JSON.parse(outlinesRaw)
  }
  if (requestRaw) {
    tripRequest.value = JSON.parse(requestRaw)
  }

  const data = sessionStorage.getItem('tripPlan')
  if (data) {
    tripPlan.value = JSON.parse(data)
    const planIdRaw = sessionStorage.getItem('tripPlanId')
    currentPlanId.value = planIdRaw ? Number(planIdRaw) : null
    if (!outlineData.value) {
      // 兼容旧版直通详情（无大纲缓存时）
      phase.value = 'detail'
      await loadAttractionPhotos()
      // 等待DOM渲染完成后初始化地图
      await nextTick()
      initMap()
    }
    // 恢复的页面：无预算且知道方案号与原始请求时，补发预算任务（24h 内服务端缓存秒回）
    if (tripPlan.value && !tripPlan.value.budget && currentPlanId.value && tripRequest.value) {
      ensureBudget(currentPlanId.value, tripPlan.value)
    } else if (tripPlan.value?.budget) {
      budgetState.value = 'ready'
    }
  }

  prefetchAllDetails()
})

// 启动/复用某方案的后台详情任务（预生成与点击共用同一 Promise，不重复调 LLM）
const ensureDetailTask = (plan: PlanOutline): Promise<TripPlan> => {
  const existing = detailTasks.get(plan.plan_id)
  if (existing) return existing
  // record_id 关联历史行程：已持久化的详情直接回放，新生成的详情落库
  const task = generateTripDetail(tripRequest.value!, plan, outlineData.value?.record_id ?? null)
    .then((res) => {
      if (res.success && res.data) {
        detailCache.set(plan.plan_id, res.data)
        detailReady.value[plan.plan_id] = true
        return res.data
      }
      throw new Error(res.message || '详情生成失败')
    })
    .catch((err) => {
      // 失败允许重试：清掉任务，下次点击重新发起
      detailTasks.delete(plan.plan_id)
      detailFailed.value[plan.plan_id] = true
      throw err
    })
  detailTasks.set(plan.plan_id, task)
  task.catch(() => {}) // 后台任务失败不打未捕获异常，点击时会再提示
  return task
}

// 一进方案页就并发生成全部 3 套详情
const prefetchAllDetails = () => {
  if (!outlineData.value || !tripRequest.value) return
  outlineData.value.plans.forEach((plan) => {
    if (!detailCache.has(plan.plan_id)) {
      ensureDetailTask(plan)
    }
  })
}

// 预算结果只定向回填当前展示的方案（不整体替换 tripPlan，避免冲掉编辑中的数据）
const applyBudget = (planId: number, budget: Budget) => {
  budgetCache.set(planId, budget)
  if (currentPlanId.value === planId && tripPlan.value && !tripPlan.value.budget) {
    tripPlan.value.budget = budget
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  }
  if (currentPlanId.value === planId) budgetState.value = 'ready'
}

// 懒触发预算任务：路线就绪后调用；本地已算过/任务在途则复用
const ensureBudget = (planId: number, plan: TripPlan) => {
  if (plan.budget) { // 已有预算（如 sessionStorage 恢复）：直接展示，不重发任务
    budgetCache.set(planId, plan.budget)
    budgetState.value = 'ready'
    return
  }
  const cached = budgetCache.get(planId)
  if (cached) {
    applyBudget(planId, cached)
    return
  }
  if (!tripRequest.value) return
  budgetState.value = 'pending' // 含任务在途时切回该方案的场景
  const existing = budgetTasks.get(planId)
  if (existing) return
  const task = submitJob('budget', {
    record_id: outlineData.value?.record_id ?? null,
    plan_id: planId,
    request: tripRequest.value,
    plan
  }, crypto.randomUUID?.() || `bg-${Date.now()}`)
    .then(({ job_id }) => pollJob(job_id, undefined, 3000, 10 * 60 * 1000))
    .then((res) => {
      if (res?.budget) {
        applyBudget(planId, res.budget as Budget)
      } else {
        throw new Error('预算结果为空')
      }
    })
    .catch((err) => {
      console.error('预算生成失败:', err)
      budgetTasks.delete(planId) // 允许重试
      if (currentPlanId.value === planId) budgetState.value = 'failed'
    })
  budgetTasks.set(planId, task)
  task.catch(() => {})
}

const retryBudget = () => {
  if (currentPlanId.value && tripPlan.value) {
    budgetState.value = 'idle'
    ensureBudget(currentPlanId.value, tripPlan.value)
  }
}

// 大纲时间轴某天的景点串
const spotsText = (day: OutlineDay): string =>
  day.spots.map((s) => s.name).join(' → ') || '（待定）'

// 用大纲搭骨架 TripPlan：景点/时间轴/天气立即展示，其余字段留空由占位符显示「规划中」
const buildSkeletonPlan = (plan: PlanOutline): TripPlan => ({
  city: outlineData.value?.city ?? '',
  start_date: outlineData.value?.start_date ?? '',
  end_date: outlineData.value?.end_date ?? '',
  days: plan.days.map((d) => ({
    date: d.date,
    day_index: d.day_index,
    description: d.title || '规划中…',
    transportation: tripRequest.value?.transportation ?? '',
    accommodation: tripRequest.value?.accommodation ?? '',
    attractions: d.spots.map((s) => ({
      name: s.name,
      address: s.address,
      location: s.location ?? null,
      visit_duration: 0,
      description: '详情规划中…',
      category: s.category ?? '景点',
      ticket_price: 0
    })),
    meals: []
  })),
  weather_info: outlineData.value?.weather_info ?? [],
  overall_suggestions: ''
})

// 选定方案：命中缓存秒开；否则先进骨架详情页，后台任务完成后自动填充
const selectPlan = async (plan: PlanOutline) => {
  currentPlanId.value = plan.plan_id
  sessionStorage.setItem('tripPlanId', String(plan.plan_id))
  budgetState.value = budgetCache.has(plan.plan_id) ? 'ready' : 'idle'
  const cached = detailCache.get(plan.plan_id)
  if (cached) {
    detailPending.value = false
    detailError.value = ''
    tripPlan.value = cached
    sessionStorage.setItem('tripPlan', JSON.stringify(cached))
    await enterDetailPhase()
    ensureBudget(plan.plan_id, cached) // 路线已定稿：懒触发预算（本地/服务端缓存秒回）
    return
  }
  if (!tripRequest.value) {
    message.error('缺少原始请求数据，请返回首页重新规划')
    return
  }
  selectedOutline.value = plan
  detailPending.value = true
  detailError.value = ''
  tripPlan.value = buildSkeletonPlan(plan)
  await enterDetailPhase()
  try {
    const data = await ensureDetailTask(plan)
    // 骨架与详情的景点/坐标一致：直接换数据即可，图片缓存与地图标记无需重建
    tripPlan.value = data
    sessionStorage.setItem('tripPlan', JSON.stringify(data))
    detailPending.value = false
    message.success('详情已补全 ✅')
    ensureBudget(plan.plan_id, data) // 详情就绪后后台生成预算
  } catch (error: any) {
    detailPending.value = false
    detailError.value = error.message || '详情生成失败'
  }
}

const retryDetail = () => {
  if (selectedOutline.value) {
    selectPlan(selectedOutline.value)
  }
}

const enterDetailPhase = async () => {
  phase.value = 'detail'
  editMode.value = false
  activeDays.value = [0]
  attractionPhotos.value = {}
  await loadAttractionPhotos()
  await nextTick()
  // 地图容器随 v-if 销毁过，旧实例必须废弃重建
  if (map) {
    map.destroy()
    map = null
    mapReady.value = false
  }
  initMap()
}

const backToSelect = () => {
  phase.value = 'select'
}

const goBack = () => {
  router.push('/')
}

// 滚动到指定区域
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  const element = document.getElementById(key)
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 切换编辑模式
const toggleEditMode = () => {
  editMode.value = true
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入编辑模式')
}

// 保存修改
const saveChanges = () => {
  editMode.value = false
  // 更新sessionStorage
  if (tripPlan.value) {
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  }
  message.success('修改已保存')

  // 重新初始化地图以反映更改
  if (map) {
    map.destroy()
  }
  nextTick(() => {
    initMap()
  })
}

// 取消编辑
const cancelEdit = () => {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  message.info('已取消编辑')
}

// 删除景点
const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  if (day.attractions.length <= 1) {
    message.warning('每天至少需要保留一个景点')
    return
  }

  day.attractions.splice(attrIndex, 1)
  message.success('景点已删除')
}

// 移动景点顺序
const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions

  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
  }
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '早餐',
    lunch: '午餐',
    dinner: '晚餐',
    snack: '小吃'
  }
  return labels[type] || type
}

// 加载所有景点图片
const loadAttractionPhotos = async () => {
  if (!tripPlan.value) return

  const promises: Promise<void>[] = []

  tripPlan.value.days.forEach(day => {
    day.attractions.forEach(attraction => {
      const promise = fetch(`http://localhost:8000/api/poi/photo?name=${encodeURIComponent(attraction.name)}`)
        .then(res => res.json())
        .then(data => {
          if (data.success && data.data.photo_url) {
            attractionPhotos.value[attraction.name] = data.data.photo_url
          }
        })
        .catch(err => {
          console.error(`获取${attraction.name}图片失败:`, err)
        })

      promises.push(promise)
    })
  })

  await Promise.all(promises)
}

// 获取景点图片
const getAttractionImage = (name: string, index: number): string => {
  // 如果已加载真实图片,返回真实图片
  if (attractionPhotos.value[name]) {
    return attractionPhotos.value[name]
  }

  // 返回一个纯色占位图(避免跨域问题)
  const colors = [
    { start: '#667eea', end: '#764ba2' },
    { start: '#f093fb', end: '#f5576c' },
    { start: '#4facfe', end: '#00f2fe' },
    { start: '#43e97b', end: '#38f9d7' },
    { start: '#fa709a', end: '#fee140' }
  ]
  const colorIndex = index % colors.length
  const { start, end } = colors[colorIndex]

  // 使用base64编码避免中文问题
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs>
      <linearGradient id="grad${index}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:${start};stop-opacity:1" />
        <stop offset="100%" style="stop-color:${end};stop-opacity:1" />
      </linearGradient>
    </defs>
    <rect width="400" height="300" fill="url(#grad${index})"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="24" font-weight="bold" fill="white">${name}</text>
  </svg>`

  return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`
}

// 图片加载失败时的处理
const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement
  // 使用灰色占位图
  img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
}



// 折叠面板默认只展开第一天，克隆导出前强制全部展开（否则图片/PDF 缺内容）
const expandAllCollapsePanels = (container: HTMLElement) => {
  container.querySelectorAll('.ant-collapse-item').forEach((item) => {
    item.classList.add('ant-collapse-item-active')
  })
  container.querySelectorAll('.ant-collapse-content').forEach((content) => {
    const el = content as HTMLElement
    el.classList.remove('ant-collapse-content-hidden')
    el.style.setProperty('display', 'block')
    el.style.setProperty('height', 'auto')
    el.style.setProperty('overflow', 'visible')
  })
}

// 导出为图片
const exportAsImage = async () => {
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML
    expandAllCollapsePanels(exportContainer)

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = '' // 移除所有类
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    // 转换为图片并下载
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML
    expandAllCollapsePanels(exportContainer)

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const imgWidth = 210 // A4宽度(mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    // 如果内容高度超过一页,分页处理
    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297 // A4高度

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}

// 初始化地图
const initMap = async () => {
  const jsKey = import.meta.env.VITE_AMAP_WEB_JS_KEY
  if (!jsKey) {
    // 未配置 JS Key 时不报错，仅提示并跳过地图（行程内容不受影响）
    mapReady.value = false
    message.info('未配置高德 JS Key，地图未展示（在 frontend/.env 配置 VITE_AMAP_WEB_JS_KEY）')
    return
  }
  try {
    // 2021-12-02 之后申请的 JS Key 需要配合安全密钥使用
    const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE
    if (securityCode) {
      ;(window as any)._AMapSecurityConfig = { securityJsCode: securityCode }
    }
    const AMap = await AMapLoader.load({
      key: jsKey,
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    })

    // 创建地图实例
    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527], // 默认中心点(北京)
      viewMode: '3D'
    })

    // 添加景点标记
    addAttractionMarkers(AMap)

    mapReady.value = true
    message.success('地图加载成功')
  } catch (error) {
    mapReady.value = false
    console.error('地图加载失败:', error)
    message.error('地图加载失败，请检查高德 JS Key 配置')
  }
}

// 添加景点标记
const addAttractionMarkers = (AMap: any) => {
  if (!tripPlan.value) return

  const markers: any[] = []
  const allAttractions: any[] = []

  // 收集所有景点
  tripPlan.value.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attrIndex) => {
      if (attraction.location && attraction.location.longitude && attraction.location.latitude) {
        allAttractions.push({
          ...attraction,
          dayIndex,
          attrIndex
        })
      }
    })
  })

  // 创建标记
  allAttractions.forEach((attraction, index) => {
    const marker = new AMap.Marker({
      position: [attraction.location.longitude, attraction.location.latitude],
      title: attraction.name,
      label: {
        content: `<div style="background: #4CAF50; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${index + 1}</div>`,
        offset: new AMap.Pixel(0, -30)
      }
    })

    // 创建信息窗口
    const infoWindow = new AMap.InfoWindow({
      content: `
        <div style="padding: 10px;">
          <h4 style="margin: 0 0 8px 0;">${attraction.name}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${attraction.address}</p>
          <p style="margin: 4px 0;"><strong>游览时长:</strong> ${attraction.visit_duration}分钟</p>
          <p style="margin: 4px 0;"><strong>描述:</strong> ${attraction.description}</p>
          <p style="margin: 4px 0; color: #1890ff;"><strong>第${attraction.dayIndex + 1}天 景点${attraction.attrIndex + 1}</strong></p>
        </div>
      `,
      offset: new AMap.Pixel(0, -30)
    })

    // 点击标记显示信息窗口
    marker.on('click', () => {
      infoWindow.open(map, marker.getPosition())
    })

    markers.push(marker)
  })

  // 添加标记到地图
  map.add(markers)

  // 自动调整视野以包含所有标记
  if (allAttractions.length > 0) {
    map.setFitView(markers)
  }

  // 绘制路线
  drawRoutes(AMap, allAttractions)
}

// 绘制路线
const drawRoutes = (AMap: any, attractions: any[]) => {
  if (attractions.length < 2) return

  // 按天分组绘制路线
  const dayGroups: any = {}
  attractions.forEach(attr => {
    if (!dayGroups[attr.dayIndex]) {
      dayGroups[attr.dayIndex] = []
    }
    dayGroups[attr.dayIndex].push(attr)
  })

  // 为每天的景点绘制路线
  Object.values(dayGroups).forEach((dayAttractions: any) => {
    if (dayAttractions.length < 2) return

    const path = dayAttractions.map((attr: any) => [
      attr.location.longitude,
      attr.location.latitude
    ])

    const polyline = new AMap.Polyline({
      path: path,
      strokeColor: '#1890ff',
      strokeWeight: 4,
      strokeOpacity: 0.8,
      strokeStyle: 'solid',
      showDir: true // 显示方向箭头
    })

    map.add(polyline)
  })
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 40px 20px;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  background: white;
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(102, 126, 234, 0.1);
}

.main-content {
  flex: 1;
  min-width: 0;
}

/* 景点图片样式 */
.attraction-image-wrapper {
  position: relative;
  margin-bottom: 12px;
  border-radius: 8px;
  overflow: hidden;
}

.attraction-image {
  width: 100%;
  height: 200px;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.attraction-image-wrapper:hover .attraction-image {
  transform: scale(1.05);
}

.attraction-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.badge-number {
  font-size: 18px;
}

.price-tag {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 77, 79, 0.9);
  color: white;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* 天气卡片样式 */
.weather-card {
  background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
  border: none !important;
  transition: all 0.3s ease;
}

.weather-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
}

.weather-date {
  font-size: 16px;
  font-weight: bold;
  color: #00796b;
  margin-bottom: 12px;
  text-align: center;
}

.weather-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.weather-icon {
  font-size: 24px;
}

.weather-label {
  font-size: 12px;
  color: #666;
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: #00796b;
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: #00796b;
  font-size: 14px;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 酒店卡片样式 */
.hotel-card {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%);
}

.hotel-title {
  color: white !important;
  font-weight: 600;
}

/* 顶部信息区布局 */
.top-info-section {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.left-info {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-map {
  flex: 1;
}

/* 行程概览卡片 */
.overview-card {
  height: fit-content;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

/* 预算卡片 */
.budget-card {
  height: fit-content;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.budget-item {
  text-align: center;
  padding: 12px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.budget-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: #1890ff;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.total-label {
  font-size: 16px;
  font-weight: 600;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
}

/* 地图卡片 */
.map-card {
  height: 100%;
  min-height: 500px;
}

.map-card :deep(.ant-card-body) {
  height: calc(100% - 57px);
  padding: 0;
}

.map-wrap {
  position: relative;
  width: 100%;
  height: 100%;
}

.map-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: #999;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  text-align: center;
}

.map-placeholder p {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #666;
}

.map-placeholder span {
  font-size: 13px;
  line-height: 1.6;
}

/* 每日行程卡片 */
.days-card {
  margin-top: 20px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.day-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.day-date {
  font-size: 14px;
  color: #999;
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.info-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.info-row:last-child {
  margin-bottom: 0;
}

.info-row .label {
  font-weight: 600;
  color: #666;
  min-width: 100px;
}

.info-row .value {
  color: #333;
  flex: 1;
}

/* 卡片样式优化 */
:deep(.ant-card) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out;
}

:deep(.ant-card:hover) {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

:deep(.ant-card-head) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white !important;
  border-radius: 12px 12px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: white !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: white !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid #e8e8e8;
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: #1890ff;
}

/* 景点卡片样式 */
:deep(.ant-list-item) {
  transition: all 0.3s ease;
}

:deep(.ant-list-item:hover) {
  transform: scale(1.02);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ============ 方案选择页（阶段一） ============ */
.plan-select-wrapper {
  max-width: 1500px;
  margin: 0 auto;
  animation: fadeInUp 0.6s ease-out;
}

.select-header {
  text-align: center;
  margin-bottom: 36px;
}

.select-title {
  font-size: 32px;
  font-weight: 800;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 8px;
}

.select-subtitle {
  color: #888;
  font-size: 15px;
  margin: 0;
}

.source-badge {
  display: inline-block;
  margin-left: 10px;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  vertical-align: 1px;
}

.source-badge.seed {
  background: #fff7e6;
  color: #d48806;
  border: 1px solid #ffe1a8;
}

.source-badge.cache {
  background: #e6f4ff;
  color: #1677ff;
  border: 1px solid #bae0ff;
}

.plan-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 24px;
  align-items: stretch;
}

.plan-card {
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-radius: 20px;
  padding: 28px 24px 24px;
  box-shadow: 0 8px 30px rgba(102, 126, 234, 0.10);
  border: 2px solid transparent;
  transition: transform 0.25s ease, box-shadow 0.25s ease, border-color 0.25s ease;
}

.plan-card:hover {
  transform: translateY(-6px);
  border-color: #667eea;
  box-shadow: 0 16px 40px rgba(102, 126, 234, 0.22);
}

.plan-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.plan-style {
  font-size: 22px;
  font-weight: 700;
  color: #333;
}

.plan-budget {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: #667eea;
  background: rgba(102, 126, 234, 0.10);
  border-radius: 999px;
  padding: 4px 12px;
}

.plan-tagline {
  color: #764ba2;
  font-size: 14px;
  margin: 0 0 12px;
}

.plan-highlights {
  list-style: none;
  padding: 0;
  margin: 0 0 18px;
}

.plan-highlights li {
  font-size: 13px;
  color: #666;
  line-height: 1.9;
}

.plan-timeline {
  flex: 1;
  padding: 8px 4px 0;
  margin-bottom: 16px;
}

.timeline-day {
  font-size: 13px;
  font-weight: 600;
  color: #444;
  margin-bottom: 2px;
}

.timeline-spots {
  font-size: 13px;
  color: #999;
  line-height: 1.6;
}

.plan-detail-btn {
  height: 46px;
  border-radius: 23px;
  font-size: 15px;
  font-weight: 600;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
}

.detail-status {
  text-align: center;
  font-size: 12px;
  margin-bottom: 10px;
  min-height: 18px;
}

.status-ready {
  color: #52c41a;
  font-weight: 600;
}

.status-pending {
  color: #999;
}

.status-failed {
  color: #ff4d4f;
}

/* 详情骨架屏：未生成区块的「规划中」占位 */
.detail-pending-banner {
  margin-bottom: 20px;
  border-radius: 12px;
}

.pending-placeholder {
  padding: 20px;
  text-align: center;
  color: #999;
  font-size: 14px;
  background: repeating-linear-gradient(
    -45deg,
    #fafafa,
    #fafafa 10px,
    #f0f0f0 10px,
    #f0f0f0 20px
  );
  border-radius: 8px;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .result-container {
    padding: 20px 10px;
  }

  .page-header {
    flex-direction: column;
    gap: 16px;
  }

  .plan-cards {
    grid-template-columns: 1fr;
  }

  .select-title {
    font-size: 24px;
  }
}
</style>

