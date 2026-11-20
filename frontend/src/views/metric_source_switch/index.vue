<template>
  <section class="page" data-module="metric_source_switch">
    <header class="page-head">
      <div>
        <h2>指标数据源切换计划</h2>
        <p class="page-desc">
          数据生命周期跨模块写入的版本化切换计划：保留热温边界与回退表名，逐项收口生命周期与指标数据源的冲突，
          中断恢复只处理未完成项。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh-plans" type="button" @click="reload">刷新计划</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">计划总数</span>
        <strong class="stat-value" data-testid="stat-total">{{ plans.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">切换中/已中断</span>
        <strong class="stat-value" data-testid="stat-running">{{ runningCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已完成</span>
        <strong class="stat-value" data-testid="stat-done">{{ doneCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">当前业务版本</span>
        <strong class="stat-value stat-version" data-testid="stat-version">
          {{ currentVersion ? `${currentVersion.版本}·${currentVersion.处理路线}` : '暂无版本' }}
        </strong>
      </article>
    </div>

    <div v-if="currentVersion" class="version-banner" data-testid="version-banner">
      <span>当前业务版本：<strong>{{ currentVersion.计划编号 }}</strong>（{{ currentVersion.版本 }}）</span>
      <span>目标表：{{ currentVersion.目标表名 }}</span>
      <span>处理路线：{{ currentVersion.处理路线 }}</span>
      <span>热温边界：{{ currentVersion.热温边界时间 }}</span>
      <span>回退表名：{{ currentVersion.回退表名 }}</span>
      <span>进度：{{ currentVersion.已完成项数 }}/{{ currentVersion.总项数 }}</span>
      <span class="banner-status" :data-status="currentVersion.状态">{{ currentVersion.状态 }}</span>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>目标表名</span>
        <input v-model="tableFilter" data-testid="filter-table" placeholder="如 orders" />
      </label>
      <label class="filter-item">
        <span>计划状态</span>
        <select v-model="statusFilter" data-testid="filter-status">
          <option value="">全部</option>
          <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table sortable-table" data-testid="plan-table">
      <thead>
        <tr>
          <th v-for="col in columns" :key="col.key" class="sortable-th" @click="toggleSort(col.key)">
            {{ col.label }}
            <span class="sort-mark">{{ sortMark(col.key) }}</span>
          </th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="plan in sortedPlans" :key="plan.id" :data-testid="`plan-row-${plan.id}`" :data-status="plan.状态">
          <td>{{ plan.计划编号 }}</td>
          <td>{{ plan.目标表名 }}</td>
          <td>{{ plan.版本 }}</td>
          <td>{{ plan.处理路线 }}</td>
          <td>{{ plan.热温边界时间 }}</td>
          <td>{{ plan.回退表名 }}</td>
          <td><span class="status-pill" :data-status="plan.状态">{{ plan.状态 }}</span></td>
          <td>{{ plan.已完成项数 }}/{{ plan.总项数 }}</td>
          <td>{{ plan.创建时间 }}</td>
          <td class="row-actions">
            <button class="link" data-testid="view-plan" type="button" @click="openDetail(plan)">查看明细</button>
            <button
              v-if="plan.状态 !== '已完成'"
              class="link"
              type="button"
              :data-testid="`apply-${plan.id}`"
              @click="onApply(plan.id)"
            >
              {{ plan.状态 === '已中断' || plan.状态 === '切换中' ? '恢复切换' : '执行切换' }}
            </button>
            <button
              v-if="plan.状态 === '切换中' || plan.状态 === '待执行'"
              class="link"
              type="button"
              :data-testid="`interrupt-${plan.id}`"
              @click="onInterrupt(plan.id)"
            >
              标记中断
            </button>
          </td>
        </tr>
        <tr v-if="!sortedPlans.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无切换计划，可在数据生命周期页生成</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ sortedPlans.length }} 份切换计划 · 点击表头可按列排列</span>
      <span v-if="errorMessage" class="error-text" data-testid="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="detailPlan" class="modal-backdrop" data-testid="detail-modal" @click.self="closeDetail">
      <div class="modal detail-modal">
        <h3>计划明细 · {{ detailPlan.计划编号 }}</h3>
        <div class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detailPlan[field] ?? '—' }}</dd>
          </template>
        </div>        <p class="route-note">{{ detailPlan.路线说明 }}</p>

        <h4>逐项指标来源（每个指标仅一个当前来源表）</h4>
        <table class="data-table item-table" data-testid="item-table">
          <thead>
            <tr>
              <th v-for="col in itemColumns" :key="col">{{ col }}</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in detailItems" :key="item.id" :data-testid="`item-row-${item.id}`" :data-status="item.状态">
              <td>{{ item.指标编号 }}</td>
              <td>{{ item.指标名称 }}</td>
              <td>{{ item.原来源表 }}</td>
              <td>{{ item.目标来源表 }}</td>
              <td>{{ item.当前来源表 }}</td>
              <td>{{ item.回退表名 }}</td>
              <td><span class="status-pill" :data-status="item.状态">{{ item.状态 }}</span></td>
              <td>
                <button
                  v-if="item.状态 === '已切换'"
                  class="link"
                  type="button"
                  :data-testid="`rollback-${item.id}`"
                  @click="onRollback(item)"
                >
                  按回退表回退
                </button>
                <span v-else class="muted-text">待切换</span>
              </td>
            </tr>
          </tbody>
        </table>

        <h4>操作审计（归档后可回查热温边界）</h4>
        <table class="data-table audit-table" data-testid="audit-table">
          <thead>
            <tr><th>记录时间</th><th>动作</th><th>指标编号</th><th>原来源表</th><th>新来源表</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in auditRows" :key="row.id">
              <td>{{ row.记录时间 }}</td>
              <td>{{ row.动作 }}</td>
              <td>{{ row.指标编号 }}</td>
              <td>{{ row.原来源表 || '—' }}</td>
              <td>{{ row.新来源表 || '—' }}</td>
              <td>{{ row.说明 }}</td>
            </tr>
            <tr v-if="!auditRows.length">
              <td colspan="6" class="empty-state">暂无审计记录</td>
            </tr>
          </tbody>
        </table>

        <div class="page-actions modal-actions">
          <button class="btn" type="button" @click="closeDetail">关闭</button>
          <button
            v-if="detailPlan.状态 !== '已完成'"
            class="btn primary"
            type="button"
            data-testid="apply-in-detail"
            @click="onApply(detailPlan.id)"
          >
            {{ detailPlan.状态 === '已中断' ? '恢复未完成项' : '执行切换' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  applyPlan,
  fetchCurrentVersion,
  fetchPlanAudit,
  fetchPlanDetail,
  fetchPlans,
  interruptPlan,
  rollbackItem,
  type AuditRow,
  type CurrentVersion,
  type SwitchItem,
  type SwitchPlan,
} from '@/api/metricSourceSwitch'

type SortKey = 'id' | '计划编号' | '目标表名' | '版本' | '处理路线' | '热温边界时间' | '回退表名' | '状态' | '已完成项数' | '创建时间'
type SortDirection = 'asc' | 'desc'

const columns: { key: SortKey; label: string }[] = [
  { key: '计划编号', label: '计划编号' },
  { key: '目标表名', label: '目标表名' },
  { key: '版本', label: '数据版本' },
  { key: '处理路线', label: '处理路线' },
  { key: '热温边界时间', label: '热温边界' },
  { key: '回退表名', label: '回退表名' },
  { key: '状态', label: '状态' },
  { key: '已完成项数', label: '切换进度' },
  { key: '创建时间', label: '创建时间' },
]
const itemColumns = ['指标编号', '指标名称', '原来源表', '目标来源表', '当前来源表', '回退表名', '状态']
const detailFields: (keyof SwitchPlan)[] = [
  '计划编号', '目标表名', '生命周期策略编号', '版本', '处理路线',
  '热温边界时间', '边界计算基准', '热数据保留', '温数据窗口', '冷归档策略',
  '热表名', '温表名', '回退表名', '状态', '总项数', '已完成项数', '创建时间', '更新时间',
]
const statusOptions = ['待执行', '切换中', '已中断', '已完成']

const plans = ref<SwitchPlan[]>([])
const currentVersion = ref<CurrentVersion>(null)
const tableFilter = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const sortKey = ref<SortKey>('id')
const sortDirection = ref<SortDirection>('desc')
const detailPlan = ref<SwitchPlan | null>(null)
const detailItems = ref<SwitchItem[]>([])
const auditRows = ref<AuditRow[]>([])

const runningCount = computed(() => plans.value.filter((p) => p.状态 === '切换中' || p.状态 === '已中断').length)
const doneCount = computed(() => plans.value.filter((p) => p.状态 === '已完成').length)

const filteredPlans = computed(() => {
  const keyword = tableFilter.value.trim().toLowerCase()
  return plans.value.filter((plan) => {
    const matchTable = !keyword || String(plan.目标表名).toLowerCase().includes(keyword)
    const matchStatus = !statusFilter.value || plan.状态 === statusFilter.value
    return matchTable && matchStatus
  })
})

const sortedPlans = computed(() => {
  const rows = [...filteredPlans.value]
  const factor = sortDirection.value === 'asc' ? 1 : -1
  rows.sort((a, b) => {
    const left = a[sortKey.value]
    const right = b[sortKey.value]
    if (typeof left === 'number' && typeof right === 'number') return (left - right) * factor
    return String(left ?? '').localeCompare(String(right ?? ''), 'zh-Hans-CN') * factor
  })
  return rows
})

function sortMark(key: SortKey) {
  if (sortKey.value !== key) return '⇅'
  return sortDirection.value === 'asc' ? '↑' : '↓'
}

function toggleSort(key: SortKey) {
  if (sortKey.value === key) {
    sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDirection.value = 'asc'
  }
}

function resetFilters() {
  tableFilter.value = ''
  statusFilter.value = ''
  void reload()
}

async function reload() {
  errorMessage.value = ''
  try {
    const [planPayload, versionPayload] = await Promise.all([
      fetchPlans(),
      fetchCurrentVersion(),
    ])
    plans.value = planPayload.items
    currentVersion.value = versionPayload.version
    if (detailPlan.value) {
      const refreshed = plans.value.find((p) => p.id === detailPlan.value?.id)
      if (refreshed) await openDetail(refreshed)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '切换计划读取失败'
  }
}

async function openDetail(plan: SwitchPlan) {
  errorMessage.value = ''
  try {
    const [detailPayload, audit] = await Promise.all([
      fetchPlanDetail(plan.id),
      fetchPlanAudit(plan.id),
    ])
    if (!detailPayload.plan) throw new Error('计划明细不存在')
    detailPlan.value = detailPayload.plan
    detailItems.value = detailPayload.plan.items ?? []
    auditRows.value = audit.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计划明细读取失败'
  }
}

function closeDetail() {
  detailPlan.value = null
  detailItems.value = []
  auditRows.value = []
}

async function onApply(planId: number) {
  errorMessage.value = ''
  try {
    await applyPlan(planId)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '切换执行失败'
  }
}

async function onInterrupt(planId: number) {
  errorMessage.value = ''
  try {
    await interruptPlan(planId)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '中断操作失败'
  }
}

async function onRollback(item: SwitchItem) {
  if (!detailPlan.value) return
  errorMessage.value = ''
  try {
    await rollbackItem(detailPlan.value.id, item.id)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回退失败'
  }
}

onMounted(reload)
</script>
