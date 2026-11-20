<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <div v-if="currentVersion" class="version-banner" data-testid="overview-current-version">
      <span>指标数据源当前业务版本：<strong>{{ currentVersion.计划编号 }}</strong>（{{ currentVersion.版本 }}）</span>
      <span>目标表：{{ currentVersion.目标表名 }}</span>
      <span>处理路线：{{ currentVersion.处理路线 }}</span>
      <span>热温边界：{{ currentVersion.热温边界时间 }}</span>
      <span>回退表名：{{ currentVersion.回退表名 }}</span>
      <span>切换进度：{{ currentVersion.已完成项数 }}/{{ currentVersion.总项数 }}</span>
      <span class="banner-status" :data-status="currentVersion.状态">{{ currentVersion.状态 }}</span>
      <RouterLink class="link" to="/metric_source_switch">查看切换计划</RouterLink>
    </div>

    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>

    <h3 style="font-size:15px;margin:16px 0 8px">指标数据源切换产物</h3>
    <table class="data-table" data-testid="metric-switch-overview">
      <thead>
        <tr><th>产物</th><th>数量</th></tr>
      </thead>
      <tbody>
        <tr v-for="(count, name) in metricSwitch" :key="name">
          <td>{{ name }}</td>
          <td>{{ count }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import { fetchCurrentVersion, type CurrentVersion } from '@/api/metricSourceSwitch'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
  metric_switch?: Record<string, number>
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const metricSwitch = ref<Record<string, number>>({})
const currentVersion = ref<CurrentVersion>(null)

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
    metricSwitch.value = payload.metric_switch ?? {}
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "主题注册", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流式计算", "created": 0, "pending": 0, "abnormal": 0}, {"name": "消息接入", "created": 0, "pending": 0, "abnormal": 0}, {"name": "Schema演进", "created": 0, "pending": 0, "abnormal": 0}, {"name": "增量合并", "created": 0, "pending": 0, "abnormal": 0}, {"name": "湖上分区", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流式质量", "created": 0, "pending": 0, "abnormal": 0}, {"name": "Iceberg管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "水位追踪", "created": 0, "pending": 0, "abnormal": 0}, {"name": "死信处理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "物化查询", "created": 0, "pending": 0, "abnormal": 0}, {"name": "CDC捕获", "created": 0, "pending": 0, "abnormal": 0}, {"name": "合并清理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流式血缘", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流式补数", "created": 0, "pending": 0, "abnormal": 0}, {"name": "下沉分发", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据生命周期", "created": 0, "pending": 0, "abnormal": 0}, {"name": "实时指标", "created": 0, "pending": 0, "abnormal": 0}]
  }
  try {
    const versionPayload = await fetchCurrentVersion()
    currentVersion.value = versionPayload.version
  } catch {
    currentVersion.value = null
  }
})
</script>
