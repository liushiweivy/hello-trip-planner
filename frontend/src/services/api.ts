import axios from 'axios'
import type { PlanOutline, TripFormData, TripOutlineResponse, TripPlanResponse } from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// 行程归属 key（无鉴权阶段的轻量归属）：localStorage 持久一个随机 client_id，
// 随 X-Owner-Key 头携带，历史行程按它过滤。鉴权上线后由用户 ID 替换。
const OWNER_KEY_STORAGE = 'trip_client_id'

export function getOwnerKey(): string {
  let key = localStorage.getItem(OWNER_KEY_STORAGE)
  if (!key) {
    key = (crypto.randomUUID?.() || `ck-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`)
    localStorage.setItem(OWNER_KEY_STORAGE, key)
  }
  return key
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 600000, // 10分钟超时（推理模型生成多日行程较慢）
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    config.headers['X-Owner-Key'] = getOwnerKey()
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

/**
 * 阶段一：生成 3 套方案大纲
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripOutlineResponse> {
  try {
    const response = await apiClient.post<TripOutlineResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成方案大纲失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成方案大纲失败')
  }
}

/**
 * 阶段二：生成选定方案的完整详情
 */
export async function generateTripDetail(
  formData: TripFormData,
  outline: PlanOutline,
  recordId?: number | null
): Promise<TripPlanResponse> {
  try {
    // 长行程按 5 天/段分段生成：14 天 = 3 段串行，单套详情可能接近 10 分钟
    const response = await apiClient.post<TripPlanResponse>('/api/trip/detail', {
      request: formData,
      outline,
      record_id: recordId ?? null
    }, { timeout: 900000 })
    return response.data
  } catch (error: any) {
    console.error('生成方案详情失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成方案详情失败')
  }
}

// ---------- 历史行程 ----------

export interface PlanSummary {
  id: number
  city: string
  travel_days: number
  start_date: string
  end_date: string
  prefs: string
  styles: string
  source: string
  created_at: string
}

export interface PlanRecord extends PlanSummary {
  request: TripFormData
  outlines: any
}

export async function listPlans(): Promise<PlanSummary[]> {
  const response = await apiClient.get('/api/plans')
  return response.data.data || []
}

export async function getPlan(recordId: number): Promise<PlanRecord> {
  const response = await apiClient.get(`/api/plans/${recordId}`)
  return response.data.data
}

export async function deletePlan(recordId: number): Promise<void> {
  await apiClient.delete(`/api/plans/${recordId}`)
}

// ---------- 异步任务（提交→轮询，替代同步长连接） ----------

export interface JobStatus {
  job_id: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  result?: any
  error?: string | null
}

/** 提交规划任务（幂等键由调用方生成：双击/重试复用同一任务，不重复烧 token；
 *  budget 任务的幂等键由服务端按路线内容重写，客户端传任意合法 key 即可） */
export async function submitJob(
  kind: 'outline' | 'detail' | 'budget',
  payload: any,
  idempotencyKey: string
): Promise<{ job_id: string; created: boolean; status: string }> {
  const response = await apiClient.post('/api/jobs', {
    kind, payload, idempotency_key: idempotencyKey
  })
  return response.data
}

export async function getJob(jobId: string): Promise<JobStatus> {
  const response = await apiClient.get(`/api/jobs/${jobId}`)
  return response.data
}

/** 轮询任务直到终态（默认 3s 间隔、30 分钟上限——推理模型长行程较慢） */
export async function pollJob(
  jobId: string,
  onTick?: (status: string, elapsedSec: number) => void,
  intervalMs = 3000,
  timeoutMs = 30 * 60 * 1000
): Promise<any> {
  const t0 = Date.now()
  for (;;) {
    const job = await getJob(jobId)
    if (job.status === 'succeeded') return job.result
    if (job.status === 'failed') throw new Error(job.error || '任务执行失败')
    if (Date.now() - t0 > timeoutMs) throw new Error('任务等待超时，请稍后在历史行程中查看')
    onTick?.(job.status, Math.floor((Date.now() - t0) / 1000))
    await new Promise(r => setTimeout(r, intervalMs))
  }
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient

