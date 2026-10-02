# shared/ — V1 冻结契约（D31，Phase 0.5 定稿）

三个契约以 sidecar 的 Python Pydantic 模型为**单一事实源**，由
`scripts/export_contracts.py` 生成两份产物（改模型后必须重新导出）：

| 契约 | 源模型 | 产物 |
|---|---|---|
| ① 任务事件流（SSE） | `sidecar/contracts.py`（StageChanged / Progress / TaskError / Done + STAGES/TASK_STATUSES） | `schemas/task-events.schema.json`、`types.ts` |
| ② taxonomy 结构 | `sidecar/taxonomy/__init__.py`（Taxonomy/TaxonomyType，name_en 稳定键 D43） | `schemas/taxonomy.schema.json`、`types.ts` |
| ③ observations schema | `sidecar/observations.py`（21 个受控标签 + free_note，D51） | `schemas/observations.schema.json`、`types.ts` |

- SSE 载荷：`event: stage_changed|progress|error|done` + `data: <JSON>`；
  时间戳一律 UTC ISO-8601（D53）。
- 报告 JSON Schema 属 V2，**不在本冻结范围**（D30/D31：V2 开工前冻结）。
- 改契约 = 改 Pydantic 模型 → 重跑导出脚本 → `shared/types.ts` 变更进 commit。
