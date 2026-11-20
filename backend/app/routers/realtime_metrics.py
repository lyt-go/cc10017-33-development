"""实时指标 分析看板接口：汇总指标、参数运行与结果导出。"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.realtime_metrics import RealtimeMetricsService
from app.services.metric_source_switch import metric_source_switch_service

router = APIRouter(prefix="/api/realtime_metrics", tags=["实时指标"])
service = RealtimeMetricsService()
LIST_FIELDS = ["指标编号", "指标名称", "聚合窗口", "聚合函数", "过滤条件", "分组维度", "输出表名", "指标版本"]
STATUSES = ["草稿", "待校验", "已生效", "已废弃"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    current_version = metric_source_switch_service.current_business_version()
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ], "current_version": current_version}


@router.get("/source-check")
def source_check() -> dict[str, Any]:
    """实时指标页核对来源表：每个指标唯一当前来源表 + 当前业务版本。"""
    return {
        "items": metric_source_switch_service.list_sources(),
        "current_version": metric_source_switch_service.current_business_version(),
    }


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": service.list_results()}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=realtime_metrics.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"指标定义 {entry_id} 不存在")
    return entry
