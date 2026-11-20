"""指标数据源切换计划：数据生命周期与实时指标之间的跨模块收口。

写入链路（数据生命周期侧发起）：
    TTL 策略校验 -> 生成带版本的切换计划（计划头 + 逐项指标）-> 逐项执行切换
    -> 指标来源注册表落账 -> 实时指标侧按注册表读取当前来源表。

关键约束：
- 每个指标在来源注册表里只声明一个「当前来源表」，切换以整项为单位原子提交；
- 计划中断后已完成项保留、未完成项不动，恢复时只处理未完成项，
  因此未切换完成前实时指标读到的仍是上一份完整数据源；
- 计划头保留热温边界与回退表名，归档后仍可回查；
- 同一目标表的「先切换数据源 / 先完成最近刷新」两条处理路线各自形成独立数据版本。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.store import store

PLAN_MODULE = "metric_switch_plans"
ITEM_MODULE = "metric_switch_items"
AUDIT_MODULE = "metric_switch_audit"
REGISTRY_MODULE = "metric_source_registry"

PLAN_STATUS_DRAFT = "待执行"
PLAN_STATUS_RUNNING = "切换中"
PLAN_STATUS_DONE = "已完成"
PLAN_STATUS_INTERRUPTED = "已中断"
ITEM_PENDING = "待切换"
ITEM_DONE = "已切换"

# 两条处理路线：热温边界跨过指标刷新窗口时的两种取舍，落到不同数据版本。
ROUTE_SOURCE_FIRST = "先切换数据源"
ROUTE_REFRESH_FIRST = "先完成最近刷新"
ROUTES = [ROUTE_SOURCE_FIRST, ROUTE_REFRESH_FIRST]

# 目标表 -> 明确受影响的指标编号，避免启发式匹配把同名指标漏掉。
TABLE_METRIC_HINTS: dict[str, list[str]] = {
    "orders": ["REAL-0001"],
    "users": ["REAL-0002"],
    "payments": ["REAL-0003"],
}
# 指标编号 -> 默认热表，用于首次初始化来源注册表。
METRIC_DEFAULT_TABLE: dict[str, str] = {
    "REAL-0001": "orders",
    "REAL-0002": "users",
    "REAL-0003": "payments",
}

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([dh])\s*$", re.IGNORECASE)
# 演示环境统一以种子水位时间为“现在”，保证热温边界可复现。
SEED_NOW = datetime(2026, 11, 16, 10, 0, 0)


def parse_duration_hours(text: Any) -> int | None:
    """把 7d / 24h 这类保留时长解析成小时数；无法识别时返回 None。"""
    if text is None:
        return None
    match = _DURATION_RE.match(str(text))
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2).lower()
    return value * 24 if unit == "d" else value


class MetricSourceSwitchService:
    """切换计划的生成、执行、中断恢复与来源表读取都收口在这个服务里。"""

    # ─────────────────────────────────────
    # 来源注册表：每个指标只允许一个当前来源表
    # ─────────────────────────────────────

    def _ensure_registry(self) -> dict[str, dict[str, Any]]:
        """懒加载来源注册表；首次访问时用指标定义的默认热表初始化。"""
        rows = store.rows(REGISTRY_MODULE)
        if rows:
            return {str(row["指标编号"]): row for row in rows}
        for metric in store.rows("realtime_metrics"):
            code = str(metric.get("指标编号", ""))
            table = METRIC_DEFAULT_TABLE.get(code)
            if table is None:
                continue
            rows.append({
                "指标编号": code,
                "指标名称": metric.get("指标名称"),
                "当前来源表": f"{table}_hot",
                "回退表名": f"{table}_hot",
                "当前计划编号": None,
                "当前版本": None,
                "更新时间": None,
            })
        return {str(row["指标编号"]): row for row in rows}

    def current_source(self, metric_code: str) -> dict[str, Any] | None:
        return self._ensure_registry().get(str(metric_code))

    def list_sources(self) -> list[dict[str, Any]]:
        registry = self._ensure_registry()
        results: list[dict[str, Any]] = []
        for metric in store.rows("realtime_metrics"):
            code = str(metric.get("指标编号", ""))
            registered = registry.get(code)
            view = dict(metric)
            if registered is not None:
                view["当前来源表"] = registered["当前来源表"]
                view["回退表名"] = registered["回退表名"]
                view["当前计划编号"] = registered["当前计划编号"]
                view["当前版本"] = registered["当前版本"]
                view["来源更新时间"] = registered["更新时间"]
            else:
                view["当前来源表"] = None
                view["回退表名"] = None
            results.append(view)
        return results

    def resolve_metric_source(self, metric_id: int) -> dict[str, Any]:
        """实时指标侧取数入口：指标定义 + 来源注册表合并后的当前取数视图。"""
        metric = store.find("realtime_metrics", metric_id)
        if metric is None:
            return {"ok": False, "error": f"指标定义 {metric_id} 不存在"}
        code = str(metric.get("指标编号", ""))
        registered = self._ensure_registry().get(code)
        view = dict(metric)
        if registered is not None:
            view["当前来源表"] = registered["当前来源表"]
            view["回退表名"] = registered["回退表名"]
            view["当前计划编号"] = registered["当前计划编号"]
            view["当前版本"] = registered["当前版本"]
        return {"ok": True, "metric": view}

    # ─────────────────────────────────────
    # TTL 策略校验（订单表生命周期数据策略）
    # ─────────────────────────────────────

    def validate_ttl_policy(self, target_table: str, ttl_id: int | None = None) -> dict[str, Any]:
        """校验目标表的生命周期策略是否具备生成切换计划的条件。"""
        ttl_rows = store.rows("ttl_policy")
        ttl = None
        if ttl_id is not None:
            ttl = store.find("ttl_policy", ttl_id)
        else:
            ttl = next((row for row in ttl_rows if str(row.get("目标表名")) == str(target_table)), None)

        checks: list[dict[str, str]] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"检查项": name, "结果": "通过" if ok else "不通过", "说明": detail})

        if ttl is None:
            check("生命周期策略存在", False, f"目标表 {target_table} 未登记生命周期策略，无法生成切换计划")
            return {"ok": False, "target_table": target_table, "ttl_id": ttl_id, "checks": checks}

        check("生命周期策略存在", True, f"策略 {ttl.get('策略编号')} 命中目标表 {target_table}")
        required = ["热数据保留", "温数据窗口", "冷归档策略", "过期动作", "分区粒度", "恢复超时"]
        for field in required:
            check(f"{field}已配置", bool(str(ttl.get(field) or "").strip()),
                  f"{field}={ttl.get(field) or '空'}")
        check("热数据保留格式合法", parse_duration_hours(ttl.get("热数据保留")) is not None,
              "期望 7d / 24h 这类带单位时长")
        check("温数据窗口格式合法", parse_duration_hours(ttl.get("温数据窗口")) is not None,
              "期望 30d / 12h 这类带单位时长")
        check("冷归档策略受支持", str(ttl.get("冷归档策略") or "") in
              ("s3_glacier", "s3_standard_ia", "s3_deep_archive"),
              "支持 s3_glacier / s3_standard_ia / s3_deep_archive")
        check("过期动作受支持", str(ttl.get("过期动作") or "") in ("delete", "archive"),
              "过期动作只允许 delete 或 archive")
        check("分区粒度受支持", str(ttl.get("分区粒度") or "") in ("hour", "day", "month"),
              "分区粒度只允许 hour / day / month")
        if str(target_table) == "orders":
            # 订单表是核心实时链路，冷归档策略缺失会让热分区删除后指标无表可取。
            check("订单表冷归档策略必填", bool(str(ttl.get("冷归档策略") or "").strip()),
                  "订单表进入冷归档前必须声明归档介质，供指标来源回退")
        check("策略状态已生效", ttl.get("status") == "已生效",
              f"当前状态={ttl.get('status')}，仅已生效策略可驱动切换计划")

        passed = all(item["结果"] == "通过" for item in checks)
        return {
            "ok": passed,
            "target_table": target_table,
            "ttl_id": ttl.get("id"),
            "ttl_code": ttl.get("策略编号"),
            "status": ttl.get("status"),
            "checks": checks,
        }

    # ─────────────────────────────────────
    # 计划生成：保留热温边界与回退表名，按路线产出新版本
    # ─────────────────────────────────────

    def _hot_warm_boundary(self, ttl: dict[str, Any]) -> dict[str, str]:
        """热温边界 = 当前时间 - 热数据保留；边界内为热分区，之外转温/冷。"""
        hot_hours = parse_duration_hours(ttl.get("热数据保留")) or 24 * 7
        boundary = SEED_NOW - timedelta(hours=hot_hours)
        return {
            "热温边界时间": boundary.isoformat(timespec="minutes"),
            "边界计算基准": SEED_NOW.isoformat(timespec="minutes"),
            "热数据保留": str(ttl.get("热数据保留")),
            "温数据窗口": str(ttl.get("温数据窗口")),
        }

    def _affected_metrics(self, target_table: str, metric_codes: list[str] | None) -> list[dict[str, Any]]:
        codes = list(metric_codes or [])
        for code in TABLE_METRIC_HINTS.get(str(target_table), []):
            if code not in codes:
                codes.append(code)
        # 启发式兜底：指标输出表名/过滤条件里直接引用目标表名。
        for metric in store.rows("realtime_metrics"):
            code = str(metric.get("指标编号", ""))
            haystack = " ".join(str(metric.get(field, "")) for field in
                                ("输出表名", "过滤条件", "指标名称"))
            if code not in codes and re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(target_table))}(?![A-Za-z0-9_])", haystack):
                codes.append(code)
        metrics = []
        for metric in store.rows("realtime_metrics"):
            if str(metric.get("指标编号", "")) in codes:
                metrics.append(metric)
        return metrics

    def generate_plan(
        self,
        target_table: str,
        route: str = ROUTE_SOURCE_FIRST,
        ttl_id: int | None = None,
        metric_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """生成一份带版本的切换计划；校验不过时拒绝生成并返回检查明细。"""
        validation = self.validate_ttl_policy(target_table, ttl_id)
        if not validation["ok"]:
            return {"ok": False, "error": "生命周期策略校验未通过，拒绝生成切换计划", "validation": validation}
        if route not in ROUTES:
            return {"ok": False, "error": f"处理路线「{route}」不受支持，可选：{'、'.join(ROUTES)}"}

        ttl = store.find("ttl_policy", validation["ttl_id"])
        plans = store.rows(PLAN_MODULE)
        next_version = 1 + sum(1 for plan in plans if str(plan.get("目标表名")) == str(target_table))
        plan_id = max((int(plan.get("id", 0)) for plan in plans), default=0) + 1
        plan_code = f"MSSP-{target_table}-v{next_version}"
        boundary = self._hot_warm_boundary(ttl)

        hot_table = f"{target_table}_hot"
        warm_table = f"{target_table}_warm"
        plan: dict[str, Any] = {
            "id": plan_id,
            "计划编号": plan_code,
            "目标表名": target_table,
            "生命周期策略编号": ttl.get("策略编号"),
            "版本": f"v{next_version}",
            "处理路线": route,
            "热温边界时间": boundary["热温边界时间"],
            "边界计算基准": boundary["边界计算基准"],
            "热数据保留": boundary["热数据保留"],
            "温数据窗口": boundary["温数据窗口"],
            "冷归档策略": ttl.get("冷归档策略"),
            "热表名": hot_table,
            "温表名": warm_table,
            "回退表名": hot_table,
            "状态": PLAN_STATUS_DRAFT,
            "总项数": 0,
            "已完成项数": 0,
            "创建时间": datetime.now().isoformat(timespec="seconds"),
            "更新时间": datetime.now().isoformat(timespec="seconds"),
            "路线说明": self._route_description(route),
        }
        plans.append(plan)

        items = store.rows(ITEM_MODULE)
        item_id = max((int(row.get("id", 0)) for row in items), default=0)
        affected = self._affected_metrics(target_table, metric_codes)
        if not affected:
            return {"ok": False, "error": f"目标表 {target_table} 没有可切换的实时指标，未生成计划项",
                    "validation": validation}
        for metric in affected:
            item_id += 1
            items.append({
                "id": item_id,
                "计划编号": plan_code,
                "计划id": plan_id,
                "指标编号": str(metric.get("指标编号")),
                "指标名称": metric.get("指标名称"),
                "目标表名": target_table,
                "原来源表": hot_table,
                "目标来源表": warm_table,
                "当前来源表": hot_table,
                "回退表名": hot_table,
                "状态": ITEM_PENDING,
                "切换时间": None,
            })
        plan["总项数"] = len(affected)
        self._audit(plan_code, "-", "生成计划", "-", "-",
                    f"路线={route}；热温边界={plan['热温边界时间']}；回退表={hot_table}")
        return {"ok": True, "plan": self.get_plan(plan_id)}

    @staticmethod
    def _route_description(route: str) -> str:
        if route == ROUTE_SOURCE_FIRST:
            return "热温边界跨过刷新窗口时先切换指标来源表，再补跑最近一次刷新；窗口内看板短暂显示温分区范围。"
        return "热温边界跨过刷新窗口时先完成最近一次热表刷新，再切换指标来源表；切换前看板保持热分区口径。"

    # ─────────────────────────────────────
    # 计划执行 / 中断 / 恢复
    # ─────────────────────────────────────

    def _audit(self, plan_code: str, metric_code: str, action: str,
               old_source: Any, new_source: Any, remark: str) -> None:
        store.rows(AUDIT_MODULE).append({
            "id": max((int(row.get("id", 0)) for row in store.rows(AUDIT_MODULE)), default=0) + 1,
            "计划编号": plan_code,
            "指标编号": metric_code,
            "动作": action,
            "原来源表": old_source,
            "新来源表": new_source,
            "说明": remark,
            "记录时间": datetime.now().isoformat(timespec="seconds"),
        })

    def apply_plan(self, plan_id: int, max_items: int | None = None) -> dict[str, Any]:
        """执行（或恢复）计划。

        - 只处理「待切换」项，已完成项原样保留，恢复时天然跳过；
        - max_items 用于模拟中断：本轮最多切换多少项，剩余项继续挂起；
        - 每一项切换成功后才更新来源注册表，中断不会留下半截来源表。
        """
        plan = store.find(PLAN_MODULE, plan_id)
        if plan is None:
            return {"ok": False, "error": f"切换计划 {plan_id} 不存在"}
        if plan["状态"] == PLAN_STATUS_DONE:
            return {"ok": True, "plan": self.get_plan(plan_id), "message": "计划已全部完成，无需重复切换"}

        items = [row for row in store.rows(ITEM_MODULE) if row.get("计划id") == plan_id]
        pending = [row for row in items if row["状态"] == ITEM_PENDING]
        registry = self._ensure_registry()
        plan["状态"] = PLAN_STATUS_RUNNING
        switched: list[str] = []
        interrupted = False
        for index, item in enumerate(pending):
            if max_items is not None and index >= max_items:
                interrupted = True
                break
            metric_code = str(item["指标编号"])
            old_source = item["当前来源表"]
            new_source = item["目标来源表"]
            # 以整项为单位提交：先改计划项，再改来源注册表，保证一个指标只有一个当前来源表。
            item["状态"] = ITEM_DONE
            item["当前来源表"] = new_source
            item["切换时间"] = datetime.now().isoformat(timespec="seconds")
            registered = registry.get(metric_code)
            if registered is not None:
                registered["当前来源表"] = new_source
                registered["回退表名"] = item["回退表名"]
                registered["当前计划编号"] = plan["计划编号"]
                registered["当前版本"] = plan["版本"]
                registered["更新时间"] = item["切换时间"]
            switched.append(metric_code)
            self._audit(plan["计划编号"], metric_code, "切换来源表", old_source, new_source,
                        f"处理路线={plan['处理路线']}")

        done_count = sum(1 for row in items if row["状态"] == ITEM_DONE)
        plan["已完成项数"] = done_count
        plan["更新时间"] = datetime.now().isoformat(timespec="seconds")
        if done_count == plan["总项数"]:
            plan["状态"] = PLAN_STATUS_DONE
            self._audit(plan["计划编号"], "-", "计划完成", "-", "-",
                        f"全部 {done_count} 项指标已切换至 {plan['温表名']}")
        elif interrupted:
            plan["状态"] = PLAN_STATUS_INTERRUPTED
            self._audit(plan["计划编号"], "-", "切换中断", "-", "-",
                        f"已完成 {done_count}/{plan['总项数']}，未完成项保留上一份完整数据源")
        return {
            "ok": True,
            "plan": self.get_plan(plan_id),
            "switched": switched,
            "remaining": plan["总项数"] - done_count,
            "interrupted": interrupted,
        }

    def interrupt_plan(self, plan_id: int) -> dict[str, Any]:
        """显式把运行中的计划标记为中断（模拟故障场景），已切换项不回滚。"""
        plan = store.find(PLAN_MODULE, plan_id)
        if plan is None:
            return {"ok": False, "error": f"切换计划 {plan_id} 不存在"}
        if plan["状态"] == PLAN_STATUS_DONE:
            return {"ok": False, "error": "计划已完成，不能标记中断"}
        plan["状态"] = PLAN_STATUS_INTERRUPTED
        plan["更新时间"] = datetime.now().isoformat(timespec="seconds")
        self._audit(plan["计划编号"], "-", "人工标记中断", "-", "-", "保留已完成项，等待恢复")
        return {"ok": True, "plan": self.get_plan(plan_id)}

    def rollback_item(self, plan_id: int, item_id: int) -> dict[str, Any]:
        """单项回退：按计划保留的回退表名把指标来源切回热表。"""
        plan = store.find(PLAN_MODULE, plan_id)
        item = store.find(ITEM_MODULE, item_id)
        if plan is None or item is None or item.get("计划id") != plan_id:
            return {"ok": False, "error": "计划项不存在或不属于该计划"}
        if item["状态"] != ITEM_DONE:
            return {"ok": False, "error": "仅已切换项允许回退"}
        registry = self._ensure_registry()
        old_source = item["当前来源表"]
        fallback = item["回退表名"]
        item["当前来源表"] = fallback
        item["状态"] = ITEM_PENDING
        item["切换时间"] = None
        registered = registry.get(str(item["指标编号"]))
        if registered is not None:
            registered["当前来源表"] = fallback
            registered["更新时间"] = datetime.now().isoformat(timespec="seconds")
        plan["已完成项数"] = sum(1 for row in store.rows(ITEM_MODULE)
                                if row.get("计划id") == plan_id and row["状态"] == ITEM_DONE)
        plan["状态"] = PLAN_STATUS_INTERRUPTED
        plan["更新时间"] = datetime.now().isoformat(timespec="seconds")
        self._audit(plan["计划编号"], str(item["指标编号"]), "回退来源表", old_source, fallback,
                    f"按回退表名 {fallback} 恢复")
        return {"ok": True, "plan": self.get_plan(plan_id)}

    # ─────────────────────────────────────
    # 查询
    # ─────────────────────────────────────

    def list_plans(self, *, target_table: str | None = None,
                   status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(PLAN_MODULE)
        if target_table:
            rows = [row for row in rows if target_table.casefold() in str(row.get("目标表名", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return rows

    def get_plan(self, plan_id: int) -> dict[str, Any] | None:
        plan = store.find(PLAN_MODULE, plan_id)
        if plan is None:
            return None
        detail = dict(plan)
        detail["items"] = [dict(row) for row in store.rows(ITEM_MODULE) if row.get("计划id") == plan_id]
        return detail

    def latest_plan(self, target_table: str | None = None) -> dict[str, Any] | None:
        rows = self.list_plans(target_table=target_table)
        if not rows:
            return None
        latest = max(rows, key=lambda row: int(row["id"]))
        return self.get_plan(int(latest["id"]))

    def list_audit(self, plan_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(AUDIT_MODULE)
        if plan_code:
            rows = [row for row in rows if row.get("计划编号") == plan_code]
        return rows

    def current_business_version(self) -> dict[str, Any] | None:
        """概览/看板展示的当前业务版本：取最新一份计划（完成或进行中均可）。"""
        rows = store.rows(PLAN_MODULE)
        if not rows:
            return None
        latest = max(rows, key=lambda row: int(row["id"]))
        return {
            "计划编号": latest["计划编号"],
            "目标表名": latest["目标表名"],
            "版本": latest["版本"],
            "处理路线": latest["处理路线"],
            "状态": latest["状态"],
            "热温边界时间": latest["热温边界时间"],
            "回退表名": latest["回退表名"],
            "已完成项数": latest["已完成项数"],
            "总项数": latest["总项数"],
        }


metric_source_switch_service = MetricSourceSwitchService()
