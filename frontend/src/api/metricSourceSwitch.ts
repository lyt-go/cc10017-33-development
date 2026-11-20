/**
 * 指标数据源切换计划共享接口层。
 * 数据生命周期页与实时指标页都从这里取数，保证两个业务入口读到同一份计划。
 */
import { fetchJson, request } from './client'

const BASE = '/api/metric_source_switch'

export type SwitchItem = {
  id: number
  计划编号: string
  计划id: number
  指标编号: string
  指标名称: string
  目标表名: string
  原来源表: string
  目标来源表: string
  当前来源表: string
  回退表名: string
  状态: string
  切换时间: string | null
}

export type SwitchPlan = {
  id: number
  计划编号: string
  目标表名: string
  生命周期策略编号: string
  版本: string
  处理路线: string
  热温边界时间: string
  边界计算基准: string
  热数据保留: string
  温数据窗口: string
  冷归档策略: string
  热表名: string
  温表名: string
  回退表名: string
  状态: string
  总项数: number
  已完成项数: number
  创建时间: string
  更新时间: string
  路线说明: string
  items?: SwitchItem[]
}

export type CurrentVersion = {
  计划编号: string
  目标表名: string
  版本: string
  处理路线: string
  状态: string
  热温边界时间: string
  回退表名: string
  已完成项数: number
  总项数: number
} | null

export type MetricSource = Record<string, string | number | null>

export type ValidationCheck = { 检查项: string; 结果: string; 说明: string }

export type ValidationResult = {
  ok: boolean
  target_table: string
  ttl_id: number | null
  ttl_code?: string
  status?: string
  checks: ValidationCheck[]
}

export type AuditRow = {
  id: number
  计划编号: string
  指标编号: string
  动作: string
  原来源表: string
  新来源表: string
  说明: string
  记录时间: string
}

export const ROUTE_SOURCE_FIRST = '先切换数据源'
export const ROUTE_REFRESH_FIRST = '先完成最近刷新'

export async function fetchPlans(params?: { target_table?: string; status?: string }) {
  const query = new URLSearchParams()
  if (params?.target_table) query.set('target_table', params.target_table)
  if (params?.status) query.set('status', params.status)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<{ items: SwitchPlan[]; total: number }>(`${BASE}/plans${suffix}`)
}

export async function fetchPlanDetail(planId: number) {
  return fetchJson<{ plan: SwitchPlan | null }>(`${BASE}/plans/${planId}`)
}

export async function fetchLatestPlan(targetTable?: string) {
  const suffix = targetTable ? `?target_table=${encodeURIComponent(targetTable)}` : ''
  return fetchJson<{ plan: SwitchPlan | null }>(`${BASE}/plans/latest${suffix}`)
}

export async function fetchCurrentVersion() {
  return fetchJson<{ version: CurrentVersion }>(`${BASE}/plans/current-version`)
}

export async function fetchSources() {
  return fetchJson<{ items: MetricSource[] }>(`${BASE}/sources`)
}

export async function fetchRoutes() {
  return fetchJson<{ items: string[] }>(`${BASE}/routes`)
}

export async function validatePolicy(targetTable: string) {
  return fetchJson<ValidationResult>(`${BASE}/validate/${encodeURIComponent(targetTable)}`)
}

export async function fetchPlanAudit(planId: number) {
  return fetchJson<{ items: AuditRow[] }>(`${BASE}/plans/${planId}/audit`)
}

async function postAction(path: string, body?: unknown) {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || '操作失败')
  }
  return payload as { ok: boolean; message: string; entry?: SwitchPlan }
}

export function generatePlan(payload: {
  target_table: string
  route: string
  ttl_id?: number
  metric_codes?: string[]
}) {
  return postAction(`${BASE}/plans/generate`, payload)
}

export function applyPlan(planId: number, maxItems?: number) {
  return postAction(`${BASE}/plans/${planId}/apply`, { max_items: maxItems ?? null })
}

export function interruptPlan(planId: number) {
  return postAction(`${BASE}/plans/${planId}/interrupt`)
}

export function rollbackItem(planId: number, itemId: number) {
  return postAction(`${BASE}/plans/${planId}/items/${itemId}/rollback`)
}
