// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface Attraction {
  name: string
  address: string
  location?: Location | null
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
}

export interface TripFormData {
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
}

// ============ 方案大纲（两阶段生成：先大纲后详情） ============

export interface OutlineSpot {
  name: string
  address: string
  location?: Location | null
  category?: string
}

export interface OutlineDay {
  date: string
  day_index: number
  title: string
  spots: OutlineSpot[]
}

export interface PlanOutline {
  plan_id: number
  style: string
  tagline: string
  highlights: string[]
  estimated_budget: number
  days: OutlineDay[]
}

export interface TripOutlineData {
  city: string
  start_date: string
  end_date: string
  plans: PlanOutline[]
  weather_info: WeatherInfo[]
  /** 来源: llm/cache/seed（空=LLM 新生成） */
  source?: string
  /** 历史行程记录 ID（详情请求带上可回放已存详情） */
  record_id?: number | null
}

export interface TripOutlineResponse {
  success: boolean
  message: string
  data?: TripOutlineData
}

