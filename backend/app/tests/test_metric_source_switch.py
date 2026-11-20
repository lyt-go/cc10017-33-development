"""指标数据源切换计划跨模块测试。

覆盖场景：
1. 订单表生命周期数据策略校验（通过/不通过）；
2. 切换计划生成保留热温边界与回退表名；
3. 两条处理路线形成不同数据版本；
4. 切换中断后只处理未完成项，保留上一份完整数据源；
5. 每个指标在来源注册表里只声明一个当前来源表；
6. 回退表名可把指标来源切回热表；
7. 实时指标侧按计划版本读取来源表；
8. 计划与审计产物写入共享数据仓库，两个业务入口读到同一份结果。
"""
from __future__ import annotations

import unittest
from typing import Any

from app.services.metric_source_switch import (
    ITEM_DONE,
    ITEM_PENDING,
    PLAN_STATUS_DONE,
    PLAN_STATUS_INTERRUPTED,
    ROUTE_REFRESH_FIRST,
    ROUTE_SOURCE_FIRST,
    MetricSourceSwitchService,
)
from app.services.realtime_metrics import RealtimeMetricsService
from app.store import store

ALL_METRICS = ["REAL-0001", "REAL-0002", "REAL-0003"]


class TestTtlValidation(unittest.TestCase):
    """订单表生命周期数据策略校验。"""

    def setUp(self) -> None:
        self.svc = MetricSourceSwitchService()

    def test_orders_policy_passes_validation(self) -> None:
        result = self.svc.validate_ttl_policy("orders")
        self.assertTrue(result["ok"])
        # 订单表必须校验冷归档策略，防止热分区删除后指标无表可取。
        names = [c["检查项"] for c in result["checks"]]
        self.assertIn("订单表冷归档策略必填", names)
        failed = [c for c in result["checks"] if c["结果"] == "不通过"]
        self.assertEqual(failed, [])

    def test_draft_policy_rejected(self) -> None:
        # 种子中 payments 的 TTL 策略是草稿，校验必须不通过。
        result = self.svc.validate_ttl_policy("payments")
        self.assertFalse(result["ok"])
        self.assertTrue(any(c["检查项"] == "策略状态已生效" and c["结果"] == "不通过"
                            for c in result["checks"]))
        # 校验不过时拒绝生成计划。
        gen = self.svc.generate_plan("payments", route=ROUTE_SOURCE_FIRST)
        self.assertFalse(gen["ok"])
        self.assertIn("校验未通过", gen["error"])

    def test_missing_policy_rejected(self) -> None:
        result = self.svc.validate_ttl_policy("table_without_ttl")
        self.assertFalse(result["ok"])
        self.assertTrue(any(c["检查项"] == "生命周期策略存在" for c in result["checks"]))

    def test_illegal_route_rejected(self) -> None:
        gen = self.svc.generate_plan("orders", route="先删库再跑路")
        self.assertFalse(gen["ok"])
        self.assertIn("处理路线", gen["error"])


class TestPlanGeneration(unittest.TestCase):
    """计划生成：热温边界、回退表名、版本与路线。"""

    def setUp(self) -> None:
        self.svc = MetricSourceSwitchService()

    def test_plan_keeps_boundary_and_fallback(self) -> None:
        result = self.svc.generate_plan(
            "orders", route=ROUTE_SOURCE_FIRST, metric_codes=list(ALL_METRICS))
        self.assertTrue(result["ok"], msg=result.get("error"))
        plan = result["plan"]
        # 热温边界与回退表名必须随计划落盘，归档后仍可回查。
        self.assertTrue(plan["热温边界时间"])
        self.assertEqual(plan["回退表名"], "orders_hot")
        self.assertEqual(plan["热表名"], "orders_hot")
        self.assertEqual(plan["温表名"], "orders_warm")
        self.assertEqual(plan["目标表名"], "orders")
        self.assertEqual(plan["总项数"], len(ALL_METRICS))
        for item in plan["items"]:
            self.assertEqual(item["原来源表"], "orders_hot")
            self.assertEqual(item["目标来源表"], "orders_warm")
            self.assertEqual(item["回退表名"], "orders_hot")
            self.assertEqual(item["状态"], ITEM_PENDING)

    def test_two_routes_produce_distinct_versions(self) -> None:
        v1 = self.svc.generate_plan("orders", route=ROUTE_SOURCE_FIRST)
        v2 = self.svc.generate_plan("orders", route=ROUTE_REFRESH_FIRST)
        self.assertTrue(v1["ok"] and v2["ok"])
        self.assertNotEqual(v1["plan"]["版本"], v2["plan"]["版本"])
        self.assertNotEqual(v1["plan"]["计划编号"], v2["plan"]["计划编号"])
        self.assertEqual(v1["plan"]["处理路线"], ROUTE_SOURCE_FIRST)
        self.assertEqual(v2["plan"]["处理路线"], ROUTE_REFRESH_FIRST)
        # 两条路线保留同一条热温边界与同一个回退表名。
        self.assertEqual(v1["plan"]["热温边界时间"], v2["plan"]["热温边界时间"])
        self.assertEqual(v1["plan"]["回退表名"], v2["plan"]["回退表名"])


class TestInterruptAndResume(unittest.TestCase):
    """中断恢复：只处理未完成项，未完成指标保留上一份完整数据源。"""

    def setUp(self) -> None:
        self.svc = MetricSourceSwitchService()
        gen = self.svc.generate_plan(
            "orders", route=ROUTE_SOURCE_FIRST, metric_codes=list(ALL_METRICS))
        self.plan_id = gen["plan"]["id"]
        self.plan_code = gen["plan"]["计划编号"]
        # 记录执行前每个指标的来源（上一份完整数据源）。
        self.before = {code: self.svc.current_source(code)["当前来源表"]
                       for code in ALL_METRICS}

    def _items(self) -> list[dict[str, Any]]:
        return [row for row in store.rows("metric_switch_items")
                if row.get("计划id") == self.plan_id]

    def test_partial_apply_marks_interrupted(self) -> None:
        result = self.svc.apply_plan(self.plan_id, max_items=2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["plan"]["状态"], PLAN_STATUS_INTERRUPTED)
        self.assertEqual(result["plan"]["已完成项数"], 2)
        self.assertEqual(result["remaining"], 1)

    def test_pending_metrics_keep_previous_complete_source(self) -> None:
        self.svc.apply_plan(self.plan_id, max_items=2)
        done_codes = {i["指标编号"] for i in self._items() if i["状态"] == ITEM_DONE}
        pending_codes = {i["指标编号"] for i in self._items() if i["状态"] == ITEM_PENDING}
        self.assertEqual(len(done_codes), 2)
        self.assertEqual(len(pending_codes), 1)
        for item in self._items():
            registered = self.svc.current_source(item["指标编号"])
            if item["状态"] == ITEM_DONE:
                self.assertEqual(registered["当前来源表"], "orders_warm")
            else:
                # 未切换项：来源注册表仍是上一份完整数据源，计划项自身也未被改写。
                self.assertEqual(registered["当前来源表"], self.before[item["指标编号"]])
                self.assertEqual(item["当前来源表"], item["原来源表"])

    def test_resume_only_processes_pending_items(self) -> None:
        self.svc.apply_plan(self.plan_id, max_items=2)
        resumed = self.svc.apply_plan(self.plan_id)
        self.assertEqual(resumed["plan"]["状态"], PLAN_STATUS_DONE)
        # 恢复时只补切了剩余的 1 项。
        self.assertEqual(resumed["switched"], ["REAL-0003"])
        self.assertEqual(len(resumed["switched"]), resumed["plan"]["总项数"] - 2)
        for item in self._items():
            self.assertEqual(item["状态"], ITEM_DONE)
            self.assertEqual(self.svc.current_source(item["指标编号"])["当前来源表"],
                             "orders_warm")

    def test_each_metric_has_single_current_source(self) -> None:
        self.svc.apply_plan(self.plan_id, max_items=2)
        # 来源注册表每个指标只有一行，即只允许一个当前来源表。
        codes = [row["指标编号"] for row in store.rows("metric_source_registry")]
        self.assertEqual(len(codes), len(set(codes)))
        for code in ALL_METRICS:
            self.assertIsInstance(self.svc.current_source(code)["当前来源表"], str)

    def test_explicit_interrupt_and_idempotent_apply(self) -> None:
        self.svc.apply_plan(self.plan_id, max_items=1)
        marked = self.svc.interrupt_plan(self.plan_id)
        self.assertEqual(marked["plan"]["状态"], PLAN_STATUS_INTERRUPTED)
        # 完成后重复执行应幂等，不再产生新切换。
        self.svc.apply_plan(self.plan_id)
        again = self.svc.apply_plan(self.plan_id)
        self.assertEqual(again["plan"]["状态"], PLAN_STATUS_DONE)
        self.assertIn("无需重复切换", again["message"])


class TestRollback(unittest.TestCase):
    """回退表名：单项回退切回热表。"""

    def setUp(self) -> None:
        self.svc = MetricSourceSwitchService()
        gen = self.svc.generate_plan(
            "orders", route=ROUTE_SOURCE_FIRST, metric_codes=list(ALL_METRICS))
        self.plan_id = gen["plan"]["id"]
        self.svc.apply_plan(self.plan_id)

    def test_rollback_restores_fallback_table(self) -> None:
        items = [row for row in store.rows("metric_switch_items")
                 if row.get("计划id") == self.plan_id]
        target = items[0]
        result = self.svc.rollback_item(self.plan_id, target["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.svc.current_source(target["指标编号"])["当前来源表"],
                         target["回退表名"])
        # 回退后该项重新进入待切换，计划回到中断态等待恢复。
        self.assertEqual(target["状态"], ITEM_PENDING)
        self.assertEqual(result["plan"]["状态"], PLAN_STATUS_INTERRUPTED)

    def test_rollback_pending_item_rejected(self) -> None:
        items = [row for row in store.rows("metric_switch_items")
                 if row.get("计划id") == self.plan_id]
        target = items[0]
        self.svc.rollback_item(self.plan_id, target["id"])
        again = self.svc.rollback_item(self.plan_id, target["id"])
        self.assertFalse(again["ok"])


class TestMetricsReadSide(unittest.TestCase):
    """实时指标侧按计划版本取数。"""

    def setUp(self) -> None:
        self.svc = MetricSourceSwitchService()
        self.metrics_svc = RealtimeMetricsService()

    def test_metric_service_reads_switched_source(self) -> None:
        # 默认来源是热表（即将删除的热分区口径）。
        before = next(r for r in self.metrics_svc.list_results()
                      if r["指标编号"] == "REAL-0001")
        self.assertEqual(before["当前来源表"], "orders_hot")

        gen = self.svc.generate_plan("orders", route=ROUTE_SOURCE_FIRST)
        self.svc.apply_plan(gen["plan"]["id"])

        after = next(r for r in self.metrics_svc.list_results()
                     if r["指标编号"] == "REAL-0001")
        # 订单吞吐量指标的来源表已随计划切到温表。
        self.assertEqual(after["当前来源表"], "orders_warm")
        self.assertEqual(after["当前计划编号"], gen["plan"]["计划编号"])
        self.assertEqual(after["当前版本"], gen["plan"]["版本"])
        self.assertEqual(after["回退表名"], "orders_hot")

    def test_resolve_metric_source_unknown_id(self) -> None:
        result = self.svc.resolve_metric_source(9999)
        self.assertFalse(result["ok"])

    def test_pipeline_compute_uses_current_source(self) -> None:
        from app.pipeline_engine import pipeline_engine
        gen = self.svc.generate_plan("orders", route=ROUTE_REFRESH_FIRST)
        self.svc.apply_plan(gen["plan"]["id"])
        result = pipeline_engine.compute_realtime_metrics(1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["source_table"], "orders_warm")
        self.assertEqual(result["result"]["source_plan"], gen["plan"]["计划编号"])
        self.assertEqual(result["result"]["source_version"], gen["plan"]["版本"])


class TestSharedPlanAndAudit(unittest.TestCase):
    """两个业务入口共享同一份计划，操作留痕可回查热温边界。"""

    def setUp(self) -> None:
        self.svc = MetricSourceSwitchService()

    def test_latest_plan_and_audit_consistent(self) -> None:
        gen = self.svc.generate_plan(
            "orders", route=ROUTE_SOURCE_FIRST, metric_codes=list(ALL_METRICS))
        plan_id = gen["plan"]["id"]
        plan_code = gen["plan"]["计划编号"]
        self.svc.apply_plan(plan_id, max_items=1)
        self.svc.apply_plan(plan_id)

        # 两个入口读取的是同一份计划头与同一份计划项。
        latest = self.svc.latest_plan("orders")
        detail = self.svc.get_plan(plan_id)
        self.assertEqual(latest["计划编号"], detail["计划编号"])
        self.assertEqual(latest["热温边界时间"], detail["热温边界时间"])
        self.assertEqual(latest["回退表名"], detail["回退表名"])

        version = self.svc.current_business_version()
        self.assertEqual(version["计划编号"], plan_code)
        self.assertEqual(version["状态"], PLAN_STATUS_DONE)

        audit = self.svc.list_audit(plan_code)
        actions = [row["动作"] for row in audit]
        self.assertIn("生成计划", actions)
        self.assertIn("切换来源表", actions)
        self.assertIn("计划完成", actions)
        # 审计与计划产物都落在共享数据仓库。
        self.assertTrue(any(row.get("计划编号") == plan_code
                            for row in store.rows("metric_switch_audit")))
        self.assertTrue(any(row.get("计划编号") == plan_code
                            for row in store.rows("metric_switch_items")))

    def test_list_filter_by_status_and_table(self) -> None:
        gen = self.svc.generate_plan(
            "orders", route=ROUTE_SOURCE_FIRST, metric_codes=list(ALL_METRICS))
        plan_id = gen["plan"]["id"]
        self.svc.apply_plan(plan_id, max_items=1)
        interrupted = self.svc.list_plans(status=PLAN_STATUS_INTERRUPTED)
        self.assertTrue(any(p["id"] == plan_id for p in interrupted))
        orders_plans = self.svc.list_plans(target_table="orders")
        self.assertTrue(all(p["目标表名"] == "orders" for p in orders_plans))
        other = self.svc.list_plans(target_table="no_such_table")
        self.assertEqual(other, [])


if __name__ == "__main__":
    unittest.main()
