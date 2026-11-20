<template>
  <section class="switch-console" data-testid="switch-console">
    <h3>指标数据源切换计划</h3>
    <p class="page-desc">
      冷归档前先校验订单表生命周期数据策略，再生成带版本的切换计划；计划保留热温边界与回退表名，
      中断恢复仅处理未完成项，未切换指标继续使用上一份完整数据源。
    </p>

    <div class="console-row">
      <label>
        <span>目标表名</span>
        <input v-model="targetTable" data-testid="switch-target-table" placeholder="如 orders" />
      </label>
      <label>
        <span>处理路线（热温边界跨过刷新窗口时）</span>
        <select v-model="route" data-testid="switch-route">
          <option v-for="item in routes" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="button" data-testid="validate-policy" @click="onValidate">校验生命周期策略</button>
      <button class="btn primary" type="button" data-testid="generate-plan" @click="onGenerate">生成切换计划</button>
      <RouterLink class="btn ghost" to="/metric_source_switch" data-testid="goto-switch-view">打开可排列计划视图</RouterLink>
    </div>

    <div v-if="validation" class="check-list" data-testid="validation-list">
      <div v-for="check in validation.checks" :key="check.检查项" class="check-item">
        <span>{{ check.检查项 }}：{{ check.说明 }}</span>
        <span :class="check.结果 === '通过' ? 'pass' : 'fail'">{{ check.结果 }}</span>
      </div>
    </div>

    <div v-if="currentVersion" class="version-banner" data-testid="ttl-current-version">
      <span>当前业务版本：<strong>{{ currentVersion.计划编号 }}</strong>（{{ currentVersion.版本 }}）</span>
      <span>处理路线：{{ currentVersion.处理路线 }}</span>
      <span>热温边界：{{ currentVersion.热温边界时间 }}</span>
      <span>回退表名：{{ currentVersion.回退表名 }}</span>
      <span>进度：{{ currentVersion.已完成项数 }}/{{ currentVersion.总项数 }}</span>
      <span class="banner-status" :data-status="currentVersion.状态">{{ currentVersion.状态 }}</span>
    </div>

    <table class="data-table" style="margin-top: 12px" data-testid="ttl-plan-table">
      <thead>
        <tr>
          <th>计划编号</th><th>目标表名</th><th>版本</th><th>处理路线</th>
          <th>热温边界</th><th>回退表名</th><th>状态</th><th>进度</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="plan in plans" :key="plan.id" :data-testid="`ttl-plan-row-${plan.id}`">
          <td>{{ plan.计划编号 }}</td>
          <td>{{ plan.目标表名 }}</td>
          <td>{{ plan.版本 }}</td>
          <td>{{ plan.处理路线 }}</td>
          <td>{{ plan.热温边界时间 }}</td>
          <td>{{ plan.回退表名 }}</td>
          <td><span class="status-pill" :data-status="plan.状态">{{ plan.状态 }}</span></td>
          <td>{{ plan.已完成项数 }}/{{ plan.总项数 }}</td>
        </tr>
        <tr v-if="!plans.length">
          <td colspan="8" class="empty-state">暂无切换计划</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span v-if="message" :class="messageOk ? '' : 'error-text'" data-testid="switch-message">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  fetchCurrentVersion,
  fetchPlans,
  generatePlan,
  ROUTE_SOURCE_FIRST,
  validatePolicy,
  type CurrentVersion,
  type SwitchPlan,
  type ValidationResult,
} from '@/api/metricSourceSwitch'

const targetTable = ref('orders')
const route = ref(ROUTE_SOURCE_FIRST)
const routes = ['先切换数据源', '先完成最近刷新']
const plans = ref<SwitchPlan[]>([])
const validation = ref<ValidationResult | null>(null)
const currentVersion = ref<CurrentVersion>(null)
const message = ref('')
const messageOk = ref(true)

async function reload() {
  const [planPayload, versionPayload] = await Promise.all([
    fetchPlans(),
    fetchCurrentVersion(),
  ])
  plans.value = planPayload.items
  currentVersion.value = versionPayload.version
}

async function onValidate() {
  message.value = ''
  try {
    validation.value = await validatePolicy(targetTable.value.trim())
    message.value = validation.value.ok
      ? `${targetTable.value} 生命周期策略校验通过，可以生成切换计划`
      : `${targetTable.value} 生命周期策略校验未通过，请先修复不通过项`
    messageOk.value = validation.value.ok
  } catch (error) {
    message.value = error instanceof Error ? error.message : '校验失败'
    messageOk.value = false
  }
}

async function onGenerate() {
  message.value = ''
  try {
    const payload = await generatePlan({ target_table: targetTable.value.trim(), route: route.value })
    message.value = payload.message
    messageOk.value = true
    validation.value = null
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '切换计划生成失败'
    messageOk.value = false
  }
}

onMounted(reload)
</script>
