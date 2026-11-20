import { expect, test } from '@playwright/test'

/**
 * 指标数据源切换计划跨模块验收：
 * - 数据生命周期页校验订单表 TTL 策略并生成计划；
 * - 可排列计划视图支持排序、中断/恢复、明细、审计、回退；
 * - 实时指标页核对来源表，两个业务入口共享同一份计划；
 * - 概览页展示当前业务版本。
 */
test.describe('指标数据源切换计划', () => {
  test.beforeEach(async ({ page }) => {
    page.on('pageerror', (e) => {
      throw new Error(`browser error: ${e.message}`)
    })
    page.on('response', (r) => {
      if (r.url().includes('/api/') && r.status() >= 400) {
        throw new Error(`api ${r.status()}: ${r.url()}`)
      }
    })
  })

  test('TTL页校验策略并生成切换计划，计划保留热温边界与回退表名', async ({ page }) => {
    await page.goto('/ttl_policy')
    await expect(page.getByTestId('switch-console')).toBeVisible()
    await page.getByTestId('switch-target-table').fill('orders')
    await page.getByTestId('validate-policy').click()
    await expect(page.getByTestId('validation-list')).toBeVisible()
    const checks = page.locator('[data-testid="validation-list"] .check-item')
    expect(await checks.count()).toBeGreaterThan(5)

    await page.getByTestId('generate-plan').click()
    await expect(page.getByTestId('switch-message')).toContainText('已生成')
    const firstPlanRow = page.locator('[data-testid^="ttl-plan-row-"]').first()
    await expect(firstPlanRow).toContainText('MSSP-orders-v1')
    await expect(firstPlanRow).toContainText('orders_hot')
  })

  test('可排列计划视图：排序、中断恢复、明细审计与回退', async ({ page }) => {
    // 先生成一份多指标计划，制造中断场景
    await page.request.post('/api/metric_source_switch/plans/generate', {
      data: {
        target_table: 'orders',
        route: '先切换数据源',
        metric_codes: ['REAL-0001', 'REAL-0002', 'REAL-0003'],
      },
    })

    await page.goto('/metric_source_switch')
    await expect(page.getByTestId('plan-table')).toBeVisible()
    const rows = page.locator('[data-testid^="plan-row-"]')
    const initialCount = await rows.count()
    expect(initialCount).toBeGreaterThanOrEqual(1)

    // 表头可点击排列
    await page.locator('.sortable-th', { hasText: '计划编号' }).click()
    await expect(page.locator('.sortable-th', { hasText: '计划编号' })).toContainText('↑')
    await page.locator('.sortable-th', { hasText: '计划编号' }).click()
    await expect(page.locator('.sortable-th', { hasText: '计划编号' })).toContainText('↓')

    // 查看明细，含热温边界、逐项来源与审计
    await page.getByTestId('view-plan').first().click()
    await expect(page.getByTestId('detail-modal')).toBeVisible()
    await expect(page.getByTestId('item-table')).toBeVisible()
    expect(await page.locator('[data-testid^="item-row-"]').count()).toBe(3)
    await expect(page.getByTestId('audit-table')).toContainText('生成计划')

    // 中断式执行（只切部分项），再恢复剩余项
    const latest = await page.request.get('/api/metric_source_switch/plans/latest?target_table=orders')
    const latestBody = await latest.json()
    const currentPlanId = latestBody.plan.id
    await page.request.post(`/api/metric_source_switch/plans/${currentPlanId}/apply`, { data: { max_items: 1 } })
    await page.getByTestId('apply-in-detail').click()
    await expect(page.getByTestId('detail-modal')).toBeVisible()
    const itemRows = page.locator('[data-testid^="item-row-"]')
    await expect(itemRows.first()).toContainText('已切换')

    // 已切换项可按回退表回退
    const rollbackBtn = page.getByTestId(/rollback-\d+/).first()
    if (await rollbackBtn.isVisible()) {
      await rollbackBtn.click()
      await expect(page.locator('[data-testid^="item-row-"]').first()).toContainText('待切换')
    }
  })

  test('实时指标页与TTL页共享同一计划，指标来源随计划切换', async ({ page }) => {
    // 生成并完整执行一份计划
    const gen = await page.request.post('/api/metric_source_switch/plans/generate', {
      data: { target_table: 'orders', route: '先切换数据源' },
    })
    const body = await gen.json()
    const planId = body.entry.id
    await page.request.post(`/api/metric_source_switch/plans/${planId}/apply`, { data: {} })

    await page.goto('/realtime_metrics')
    await expect(page.getByTestId('source-check-table')).toBeVisible()
    const orderRow = page.locator('[data-testid^="source-row-"]', { hasText: 'REAL-0001' })
    await expect(orderRow).toContainText('orders_warm')
    await expect(orderRow).toContainText('orders_hot')
    await expect(orderRow).toContainText('MSSP-orders-v1')
    await expect(page.getByTestId('metrics-current-version')).toContainText('MSSP-orders')

    // 切换计划视图读到的是同一份计划
    await page.goto('/metric_source_switch')
    await expect(page.locator('[data-testid^="plan-row-"]').first()).toContainText('MSSP-orders')
    await expect(page.getByTestId('version-banner')).toContainText('orders_hot')
  })

  test('概览页列出当前业务版本与切换产物', async ({ page }) => {
    await page.request.post('/api/metric_source_switch/plans/generate', {
      data: { target_table: 'orders', route: '先完成最近刷新' },
    })
    await page.goto('/')
    await expect(page.getByTestId('overview-current-version')).toBeVisible()
    await expect(page.getByTestId('overview-current-version')).toContainText('MSSP-orders')
    await expect(page.getByTestId('metric-switch-overview')).toContainText('metric_switch_plans')
  })

  test('草稿生命周期策略拒绝生成计划', async ({ page }) => {
    await page.goto('/ttl_policy')
    await page.getByTestId('switch-target-table').fill('payments')
    await page.getByTestId('validate-policy').click()
    await expect(page.getByTestId('validation-list')).toContainText('不通过')
    await page.getByTestId('generate-plan').click()
    await expect(page.getByTestId('switch-message')).toContainText('校验未通过')
  })
})
