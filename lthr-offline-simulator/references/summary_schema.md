# Summary Schema

自动报告消费端优先读取统一的公共字段，而不是依赖每个脚本各自的历史字段名。

## 顶层公共字段

- `schema_version`: 当前统一摘要结构版本，现为 `lthr-summary-v1`
- `workflow`: 工作流类型，当前包括 `single_sample`、`multi_sample`、`utest_only`、`img_extract`
- `status`: 当前工作流状态，如 `completed`、`deploy_only_completed`、`test_timeout`
- `primary_error_code`: 主错误码，正常完成时为空
- `error_codes`: 诊断命中的全部错误码列表
- `runtime_hint`: 合并后的 `[HINT][E_*]` 提示文本
- `timestamps.generated_at`: 摘要生成时间
- `artifacts`: 标准化产物路径集合
- `metrics`: 标准化指标集合
- `issue`: 仅单样本/Jira 链路需要时填充

## 兼容策略

- 历史字段仍然保留，例如 `samples`、`has_trend_analysis`、`data_sample_dir`、`img_extract_status`。
- 自动报告或新的聚合器优先读取公共字段，再按需读取 workflow 特有字段。
- 分阶段错误诊断用 `phase_diagnostics` 补充，不覆盖主流程的顶层错误码。

## 当前建议读取顺序

1. 先看 `workflow` 和 `status`
2. 再看 `primary_error_code` / `error_codes` / `runtime_hint`
3. 再看 `artifacts` 和 `metrics`
4. 最后才看各脚本保留的历史字段

补充说明：`multi_sample` 消费端应优先信任标准化后的 `status` / `metrics`，不要自行用 raw UART log 是否命中 `GoMore Offline CSV Test Start` 来重判。真实设备日志可能缺失 start marker，但解析器会根据样本进度、样本结果、summary 或 `GoMore Offline CSV Test End` 识别测试活动。

## 当前 artifact 约定

- `single_sample`: `report_path` / `json_path` / `log_path`
- `multi_sample`: `json_path` / `log_path`，如果带 IMG 提取则额外有 `img_report_path` / `img_summary_json_path` / `img_batches_json_path`
- `utest_only`: `summary_path` / `json_path` / `log_path`
- `img_extract`: `report_path` / `summary_json_path` / `batches_json_path`