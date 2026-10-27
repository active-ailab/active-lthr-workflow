# Summary Schema

自动报告消费端优先读取统一的公共字段，而不是依赖每个脚本各自的历史字段名。

## 顶层公共字段

- `schema_version`: 当前统一摘要结构版本，现为 `lthr-summary-v1`
- `workflow`: 工作流类型，当前包括 `single_sample`、`multi_sample`、`img_extract`
- `status`: 当前工作流状态；配置驱动仿真为 `success`、`partial` 或 `failed`，其他工作流保留各自状态
- `primary_error_code`: 主错误码，正常完成时为空
- `error_codes`: 诊断命中的全部错误码列表
- `runtime_hint`: 合并后的 `[HINT][E_*]` 提示文本
- `timestamps.generated_at`: 摘要生成时间
- `artifacts`: 标准化产物路径集合
- `metrics`: 标准化指标集合
- `issue`: 仅单样本/Jira 链路需要时填充
- `attachment_run_mode`: Jira 多附件处理方式；`single_attachment`、`sequential` 或 `single_person_multi_sample`
- `simulation_mode`: C 端配置模式，例如 `single_sample` 或 `single_person_multi_sample`
- `metrics.utest_invocations`: 本次外层工作流实际启动的 UTest 次数；deploy-only 为 0

## 兼容策略

- 历史字段仍然保留，例如 `samples`、`has_trend_analysis`、`data_sample_dir`、`img_extract_status`。
- 自动报告或新的聚合器优先读取公共字段，再按需读取 workflow 特有字段。
- 分阶段错误诊断用 `phase_diagnostics` 补充，不覆盖主流程的顶层错误码。

## 当前建议读取顺序

1. 先看 `workflow` 和 `status`
2. 再看 `primary_error_code` / `error_codes` / `runtime_hint`
3. 再看 `artifacts` 和 `metrics`
4. 最后才看各脚本保留的历史字段

补充说明：配置驱动仿真以 `sim_result.ini` 中校验后的状态和样本条目为准。新 UTest 日志标记为 `GoMore Offline CSV Simulation Start/End`；未观察到结束标记时，Python runner 不挂载提取正在写入的设备产物。

## 当前 artifact 约定

- `single_sample`: `report_path` / `json_path` / `log_path`
- `multi_sample`: `json_path` / `log_path`，如果带 IMG 提取则额外有 `img_report_path` / `img_summary_json_path` / `img_batches_json_path`
- `img_extract`: `report_path` / `summary_json_path` / `batches_json_path`
