"""指标数据源切换计划接口：连接数据生命周期与实时指标的跨模块入口。

数据生命周期页与实时指标页都通过这组接口访问同一份计划，保证两个业务入口结果一致。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.schemas import ActionResult
from app.services.metric_source_switch import (
    ROUTES,
    ROUTE_SOURCE_FIRST,
    metric_source_switch_service as service,
)

router = APIRouter(prefix="/api/metric_source_switch", tags=["指标数据源切换"])


class GeneratePayload(BaseModel):
    target_table: str
    route: str = ROUTE_SOURCE_FIRST
    ttl_id: int | None = None
    metric_codes: list[str] | None = None


class ApplyPayload(BaseModel):
    # 本轮最多切换多少项；缺省为全部完成。传入较小值可模拟切换中断。
    max_items: int | None = None


@router.get("/plans")
def list_plans(
    target_table: str | None = Query(default=None, description="按目标表名过滤"),
    status: str | None = Query(default=None, description="待执行、切换中、已中断、已完成"),
) -> dict[str, Any]:
    """切换计划列表（概览用，不含逐项明细）。"""
    items = service.list_plans(target_table=target_table, status=status)
    return {"items": items, "total": len(items)}


@router.get("/plans/latest")
def get_latest_plan(target_table: str | None = Query(default=None)) -> dict[str, Any]:
    """读取最新版本计划；两个业务入口都读这里，天然共享同一结果。"""
    plan = service.latest_plan(target_table)
    return {"plan": plan}


@router.get("/plans/current-version")
def get_current_version() -> dict[str, Any]:
    """概览与看板展示的当前业务版本。"""
    return {"version": service.current_business_version()}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int) -> dict[str, Any]:
    """计划明细：含热温边界、回退表名与逐项指标切换状态。"""
    plan = service.get_plan(plan_id)
    if plan is None:
        return {"plan": None, "message": f"切换计划 {plan_id} 不存在"}
    return {"plan": plan}


@router.get("/plans/{plan_id}/audit")
def get_plan_audit(plan_id: int) -> dict[str, Any]:
    """计划操作审计：生成、逐项切换、中断、回退都留痕。"""
    plan = service.get_plan(plan_id)
    if plan is None:
        return {"items": [], "message": f"切换计划 {plan_id} 不存在"}
    return {"items": service.list_audit(plan["计划编号"])}


@router.get("/validate/{target_table}")
def validate_policy(target_table: str, ttl_id: int | None = Query(default=None)) -> dict[str, Any]:
    """校验目标表（如订单表 orders）的生命周期数据策略。"""
    return service.validate_ttl_policy(target_table, ttl_id)


@router.post("/plans/generate", response_model=ActionResult)
def generate_plan(payload: GeneratePayload) -> ActionResult:
    """数据生命周期侧生成带版本的切换计划，保留热温边界与回退表名。"""
    result = service.generate_plan(
        payload.target_table, route=payload.route,
        ttl_id=payload.ttl_id, metric_codes=payload.metric_codes,
    )
    if not result.get("ok"):
        return ActionResult(ok=False, message=result.get("error", "切换计划生成失败"), entry=result)
    return ActionResult(ok=True, message=f"切换计划 {result['plan']['计划编号']} 已生成", entry=result["plan"])


@router.post("/plans/{plan_id}/apply", response_model=ActionResult)
def apply_plan(plan_id: int, payload: ApplyPayload) -> ActionResult:
    """执行/恢复计划：只处理待切换项，已完成项跳过；max_items 可模拟中断。"""
    result = service.apply_plan(plan_id, max_items=payload.max_items)
    if not result.get("ok"):
        return ActionResult(ok=False, message=result.get("error", "切换执行失败"))
    plan = result["plan"]
    if result.get("interrupted"):
        message = f"计划 {plan['计划编号']} 本轮中断，已完成 {plan['已完成项数']}/{plan['总项数']}，待恢复"
    elif plan["状态"] == "已完成":
        message = f"计划 {plan['计划编号']} 全部 {plan['总项数']} 项切换完成"
    else:
        message = f"计划 {plan['计划编号']} 已切换 {plan['已完成项数']}/{plan['总项数']} 项"
    return ActionResult(ok=True, message=message, entry=plan)


@router.post("/plans/{plan_id}/interrupt", response_model=ActionResult)
def interrupt_plan(plan_id: int) -> ActionResult:
    """人工标记中断：已切换项保留，未完成项继续指向上一份完整数据源。"""
    result = service.interrupt_plan(plan_id)
    if not result.get("ok"):
        return ActionResult(ok=False, message=result.get("error", "中断失败"))
    return ActionResult(ok=True, message="计划已标记中断，可随时恢复", entry=result["plan"])


@router.post("/plans/{plan_id}/items/{item_id}/rollback", response_model=ActionResult)
def rollback_item(plan_id: int, item_id: int) -> ActionResult:
    """按回退表名把单个指标来源切回热表。"""
    result = service.rollback_item(plan_id, item_id)
    if not result.get("ok"):
        return ActionResult(ok=False, message=result.get("error", "回退失败"))
    return ActionResult(ok=True, message="指标来源已按回退表名恢复", entry=result["plan"])


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """实时指标侧：每个指标当前声明的唯一来源表与回退表。"""
    return {"items": service.list_sources()}


@router.get("/routes")
def list_routes() -> dict[str, Any]:
    """热温边界跨过刷新窗口时的两条处理路线。"""
    return {"items": ROUTES}
