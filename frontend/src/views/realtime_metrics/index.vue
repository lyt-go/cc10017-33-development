<template>
  <section class="page" data-module="realtime_metrics">
    <header class="page-head">
      <div><h2>实时指标</h2><p class="page-desc">维护基于流式数据的Tumbling/Hopping/Session窗口聚合指标定义；指标取数来源跟随指标数据源切换计划，每个指标只声明一个当前来源表。</p></div>
      <div class="page-actions">
        <RouterLink class="btn" to="/metric_source_switch" data-testid="goto-switch-view">打开切换计划视图</RouterLink>
      </div>
    </header>
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>

    <div v-if="currentVersion" class="version-banner" data-testid="metrics-current-version">
      <span>当前业务版本：<strong>{{ currentVersion.计划编号 }}</strong>（{{ currentVersion.版本 }}）</span>
      <span>目标表：{{ currentVersion.目标表名 }}</span>
      <span>处理路线：{{ currentVersion.处理路线 }}</span>
      <span>热温边界：{{ currentVersion.热温边界时间 }}</span>
      <span>回退表名：{{ currentVersion.回退表名 }}</span>
      <span>进度：{{ currentVersion.已完成项数 }}/{{ currentVersion.总项数 }}</span>
      <span class="banner-status" :data-status="currentVersion.状态">{{ currentVersion.状态 }}</span>
    </div>

    <h3 style="font-size:15px;margin:14px 0 8px">来源表核对（与数据生命周期页共享同一份切换计划）</h3>
    <table class="data-table source-table" data-testid="source-check-table">
      <thead>
        <tr>
          <th>指标编号</th><th>指标名称</th><th>聚合窗口</th><th>输出表名</th>
          <th>当前来源表</th><th>回退表名</th><th>当前计划</th><th>当前版本</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in sourceRows" :key="String(row.id)" :data-testid="`source-row-${row.id}`">
          <td>{{ row['指标编号'] }}</td>
          <td>{{ row['指标名称'] }}</td>
          <td>{{ row['聚合窗口'] }}</td>
          <td>{{ row['输出表名'] }}</td>
          <td :class="String(row['当前来源表']).includes('warm') ? 'warm-source' : 'hot-source'">
            {{ row['当前来源表'] ?? '—' }}
          </td>
          <td>{{ row['回退表名'] ?? '—' }}</td>
          <td>{{ row['当前计划编号'] ?? '未切换' }}</td>
          <td>{{ row['当前版本'] ?? '—' }}</td>
        </tr>
        <tr v-if="!sourceRows.length">
          <td colspan="8" class="empty-state">暂无指标来源数据</td>
        </tr>
      </tbody>
    </table>

    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
        <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
        <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
import { fetchCurrentVersion, fetchSources, type CurrentVersion, type MetricSource } from '@/api/metricSourceSwitch'
const ENDPOINT = '/api/realtime_metrics'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const sourceRows = ref<MetricSource[]>([])
const currentVersion = ref<CurrentVersion>(null)
const paramFields = ["指标编号", "指标名称", "聚合窗口", "聚合函数", "过滤条件", "分组维度", "输出表名", "指标版本"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["指标编号", "指标名称", "聚合窗口", "聚合函数", "过滤条件", "分组维度", "输出表名", "指标版本"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function fetchSourceCheck() {
  try {
    const [sourcePayload, versionPayload] = await Promise.all([
      fetchSources(),
      fetchCurrentVersion(),
    ])
    sourceRows.value = sourcePayload.items
    currentVersion.value = versionPayload.version
  } catch (e) {
    sourceRows.value = []
  }
}
async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}
onMounted(() => {
  void fetchSummary()
  void fetchSourceCheck()
})
</script>
