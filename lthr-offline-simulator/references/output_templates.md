# 输出模板维护说明

## 文件位置

- `assets/report_template.md`: 单样本离线仿真 Markdown 报告骨架
- `assets/jira_comment_template.md`: Jira 评论骨架
- `assets/batch_report_template.md`: 批量汇总报告骨架
- `assets/utest_summary_template.md`: UART-only 快速摘要骨架
- `assets/img_report_template.md`: IMG 提取结果 Markdown 报告骨架

## 维护边界

- `assets/` 放脚本直接读取的模板正文，适合调整标题、段落顺序、固定文案。
- `scripts/_output_templates.py` 负责模板加载与占位符替换；各脚本只负责生成自己的 section 内容。
- `scripts/run_lthr_offline_sim.py` 负责计算统计值、构造表格、拼接样本 section，不负责维护整篇报告骨架。
- `scripts/run_lthr_batch.py` 负责汇总 issue 级结果，不负责维护批量报告骨架。
- `scripts/run_utest_only.py` 负责收集 UART-only 结果和错误摘要，不负责维护摘要骨架。
- `SKILL.md` 只保留输出物类型和入口说明，不再内联完整模板。

## 当前模板占位符

### report_template.md

- `signature_block`: 签名块，已包含末尾空行
- `generated_at`: 报告生成时间
- `issue_key`: Jira key 或本地 issue 标识
- `issue_link_line`: 可选 Link 行，已包含换行
- `issue_title_line`: 可选 Title 行，已包含换行
- `sample_count`: 样本数量
- `raw_log_name`: 原始日志文件名
- `sample_sections`: 各样本详情 section

### jira_comment_template.md

- `signature_block`: 可选签名块，已包含换行
- `issue_line`: Issue 行，已包含换行
- `title_line`: 可选标题行，已包含换行
- `sample_count`: 样本数量
- `sample_lines`: 每个样本的摘要块
- `attachment_line`: 可选附件行，已包含换行

### batch_report_template.md

- `generated_at`: 报告生成时间
- `total_duration`: 批量总耗时
- `issue_count`: 总 issue 数
- `success_count`: 成功 issue 数
- `fail_count`: 失败 issue 数
- `summary_section`: 汇总表格 section
- `detail_section`: 每个 issue 的详情 section
- `generator_name`: 生成脚本名

### utest_summary_template.md

- `generated_at`: 摘要生成时间
- `uart_port`: 实际使用的 UART 端口
- `started`: 是否命中 start marker
- `ended`: 是否命中 end marker
- `chunk_count`: 监听 chunk 数
- `run_duration`: 总运行时长
- `raw_log_name`: 原始日志文件名
- `error_code_line`: 可选主错误码行，已包含换行
- `result_section`: 结果 section
- `error_section`: 错误统计 section
- `relevant_section`: 相关日志摘录 section

### img_report_template.md

- `generated_at`: 报告生成时间
- `source_mode`: 结果来源，例 `device_img_extract` / `local_log_parse`
- `source_detail_line`: 可选来源细节行，已包含换行
- `mount_root_line`: 可选挂载点行，已包含换行
- `log_file_count`: 复制出的日志文件数量
- `log_deleted_count`: 从设备删除的日志文件数量
- `batch_count`: 解析出的批次数量
- `batch_sections`: 各批次详情 section

## 修改建议

- 需要改结构或文案时，先改 `assets/` 模板，再看脚本里是否需要新增占位符。
- 需要新增统计字段时，先在脚本里生成 section 内容，再决定是否上升为新的模板占位符。
- 保持模板只承载固定结构，不把循环、条件判断重新塞回 Markdown 文档。
- 运行时错误码单独维护在 `references/runtime_error_codes.md`，不要把错误码定义塞进模板文件。