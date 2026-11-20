"""实时指标 分析看板服务：汇总指标、运行分析并生成模拟结果。

指标取数来源不再写死热表名，而是读取指标数据源切换计划落账后的
「来源注册表」：每个指标只有一个当前来源表，切换计划完成后自动指向温表，
中断期间未完成项仍指向上一份完整数据源（热表）。
"""
from __future__ import annotations

import random
from typing import Any

from app.store import store
from app.services.metric_source_switch import metric_source_switch_service

MODULE = "realtime_metrics"
REQUIRED_FIELDS = ["指标编号", "指标名称", "聚合窗口"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class RealtimeMetricsService:
    def list_results(self) -> list[dict[str, Any]]:
        # 来源注册表提供每个指标当前生效的唯一来源表。
        source_view = {str(row.get("指标编号")): row for row in metric_source_switch_service.list_sources()}
        rows = store.rows(MODULE)
        results: list[dict] = []
        for row in rows:
            d = dict(row)
            registered = source_view.get(str(row.get("指标编号")))
            if registered is not None:
                d["当前来源表"] = registered.get("当前来源表")
                d["回退表名"] = registered.get("回退表名")
                d["当前计划编号"] = registered.get("当前计划编号")
                d["当前版本"] = registered.get("当前版本")
            d["指标值"] = round(random.uniform(0.65, 0.98), 3)
            d["对比基准"] = round(random.uniform(0.60, 0.92), 3)
            d["差异"] = round(d["指标值"] - d["对比基准"], 3)
            results.append(d)
        return results

    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        count = int(params.get("样本范围", params.get("并行数量", "6")))
        results: list[dict] = []
        for i in range(count):
            results.append({
                "id": i + 1,
                "分组": f"分组{(i%3)+1}",
                "指标值": round(random.uniform(0.62, 1.02), 3),
                "对比基准": round(random.uniform(0.58, 0.95), 3),
                "差异": round(random.uniform(-0.2, 0.3), 3),
            })
        return results

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        resolved = metric_source_switch_service.resolve_metric_source(entry_id)
        if resolved.get("ok"):
            view = dict(entry)
            metric = resolved["metric"]
            for field in ("当前来源表", "回退表名", "当前计划编号", "当前版本"):
                if metric.get(field) is not None:
                    view[field] = metric[field]
            return view
        return entry
