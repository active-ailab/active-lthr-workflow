---
name: lthr-offline-simulator
description: "通过仓库内固定 Python 入口对 GoMore 离线 CSV 执行配置驱动仿真，并提取 C 端结果和历史归档；也用于乳酸阈引导测试目标心率区间/百分比计算。设备操作由 watchlink-v3 提供。触发词: gomore offline csv test, data_sample, 单样本仿真, 多样本, 多轮仿真, utest, Jira回传, 乳酸阈引导测试, maxHr, restHr。"
---

# LTHR Offline Simulator

本 skill 只负责选择和调用固定脚本入口、准备调用参数并说明输出位置。仿真配置、设备部署、UTest 调用、结果校验和产物提取均由 `scripts/` 中的 Python 流程实现；不要在 skill 步骤里直接拼接 UART UTest 命令或手工重复仿真。

## 使用边界

- GoMore 离线 CSV 仿真、Jira CSV 回放、`data_sample` 多样本仿真及可选 IMG 日志提取使用本 skill。
- 乳酸阈引导测试的心率区间公式见 `references/guide_test_hr_ranges.md`。
- 通用串口、WSL bridge、端口配对、刷机和设备排障使用 `watchlink-v3`。
- 多样本仿真必须使用本地 `data_sample` 目录，并显式给出 `--mode` 与 `--rounds`。不要根据目录名推断样本是否属于同一人。

## 固定脚本入口

脚本目录为本 skill 下的 `scripts/`。默认输出目录会由脚本打印；Jira/本地 CSV 入口用现有 `--work-dir` 指定工作根目录，本地多样本入口用现有 `--output-dir` 指定产物目录。
从 `lthr-offline-simulator/` 目录运行以下示例；也可以将 `scripts/` 替换为脚本目录的绝对路径。

### Jira 附件仿真

指定 Jira 附件：

```bash
python3 scripts/run_lthr_offline_sim.py ITS-51497 --csv-name gomore_data_20261027_120000.csv --rounds 3
```

指定本地 CSV、跳过 Jira：

```bash
python3 scripts/run_lthr_offline_sim.py ITS-51497 --csv-path /path/to/gomore_data_20261027_120000.csv --skip-jira --rounds 3
```

- 单附件输入（`--csv-name` 或 `--csv-path`）使用 `single_sample`；`--rounds` 默认为 1，必须大于 0。
- Jira issue 有多个匹配 CSV 且未用 `--csv-name` 指定单个附件时，必须显式提供 `--multi-attachment-mode`：
  - `sequential`：每个附件独立生成 `single_sample` 配置并调用一次 UTest；
  - `single_person_multi_sample`：声明这些附件属于同一人，将它们写入一份配置，只调用一次 UTest。
- `--all-csvs` 选择全部匹配附件，多附件时仍须指定上述模式。若只需一个附件，使用 `--csv-name`，无需指定多附件模式。`--rounds N` 表示每份配置中的每个样本运行 N 轮。
- JIRA_CASE 输入路径保持扁平：单个 CSV 为 `data_sample/JIRA_CASE/<文件名>`；同一人多样本时为 `data_sample/JIRA_CASE/sample_<序号>_<文件名>`，避免附件同名冲突。Issue 信息保存在摘要中，不再重复嵌入目录层级。
- 仿真结果和日志直接保存在 `<work-dir>/<issue>/offline_sim_<时间戳>/`；未使用 `--skip-jira` 时，脚本将报告、JSON 摘要和原始日志打成一个 ZIP，并且只向 Jira 上传这一个 ZIP 附件，绝不逐个上传文件；随后可发表评论。

逐附件串行：

```bash
python3 scripts/run_lthr_offline_sim.py ITS-51497 --all-csvs --multi-attachment-mode sequential --rounds 3
```

同一人多样本：

```bash
python3 scripts/run_lthr_offline_sim.py ITS-51497 --all-csvs --multi-attachment-mode single_person_multi_sample --rounds 3
```

### 本地多样本仿真

```bash
python3 scripts/run_device_multi_sim.py \
  --data-dir /path/to/data_sample \
  --mode single_person_multi_sample \
  --rounds 3
```

`--data-dir` 必须指向 case 目录的父级 `data_sample`，例如 `data_sample/BASE_CASE/<sample>/gomore_data_*.csv`。如果从飞书只下载 `BASE_CASE` 文件夹的内容，先在本地 `data_sample/BASE_CASE/` 下还原这一层，再把 `data_sample/` 传给 `--data-dir`；不要直接把 `data_sample/BASE_CASE/` 传入。否则 UTest 日志会把样本标为 `ROOT_CASE`，报告侧的 `--case BASE_CASE` 不会提取到这些样本。

可选模式：

| `--mode` | 样本关系 | 执行含义 |
|---|---|---|
| `single_sample` | 单人、单样本 | 同一样本按轮次运行，轮次间时间戳推进一天 |
| `single_person_multi_sample` | 单人、多样本 | 轮次优先、每轮遍历所有样本；每个“轮次×样本”推进一天 |
| `multi_person_multi_sample` | 多人、多样本 | 样本优先、每个样本跑完所有轮次；样本切换不推进时间戳，只有轮次推进一天 |

`single_person_multi_sample` 和 `multi_person_multi_sample` 至少需要两个样本。输入样本按相对路径排序后写入配置；仅文件名匹配 `gomore_data_*.csv` 的文件进入样本清单。`base_case_updates_*` 作为元数据保留，不作为样本。
C 端在 UTest 开始时读取设备时间建立本次时间基准；具体时间戳生成由固定 C 实现负责，skill 不要求调用者手工改写样本时间戳。

### 部署式 UTest 入口

`run_utest_only.py` 是 `run_device_multi_sim.py` 的命令别名，共用同一套配置、部署、单次 UTest、结果提取和状态码逻辑。参数相同：

```bash
python3 scripts/run_utest_only.py \
  --data-dir /path/to/data_sample \
  --mode multi_person_multi_sample \
  --rounds 2
```

该入口同样要求本地 `--data-dir`、显式 `--mode` 和正整数 `--rounds`。`--img-only` 是单独的 IMG 提取模式，不启动仿真。

### Jira 批量入口

```bash
python3 scripts/run_lthr_batch.py --issues ITS-51497 ITS-51498 --rounds 3 --continue-on-error
```

批量脚本串行调用 `run_lthr_offline_sim.py`；每个 issue 都是独立外层仿真。轮次参数为 `--rounds`，旧 `--runs` 和 `--clean-between-runs` 不再使用。

## 固定仿真流程

对每次外层调用，固定脚本完成以下操作：

1. 收集本次有效 `gomore_data_*.csv`，在系统临时目录创建隔离的 `data_sample` staging；仿真结束后清理，不留在产物目录。
2. 生成唯一 `run_id` 和 `sim_config.ini`。`sample_count` 及样本顺序以写入配置为准。
3. 将 staging 内容部署到设备 `/storage/sport/gomore/data_sample/`；部署时不预清理，以免仿真失败前丢失现场。
4. 设备就绪后只发送一次 `utest gomore_offline_csv_sim_tests`，等待 C 端完成配置中全部样本和轮次。
5. 看到 UTest 结束标记后，提取 `sim_result.ini` 和本次 `run_id` 对应的 `gomore_his_<run_id>*.data`，校验结果并直接保存到本地输出目录。逐附件串行产生多次 UTest 时，仅将各次固定名结果文件放在 `runs/<run_id>/`，避免互相覆盖。
6. 仅当结果校验通过且 C 端 `overall_status=success` 时，脚本在产物提取后清空设备 `data_sample` 的全部内容，并验证目录为空。仿真部分完成、失败、未观察到 UTest 结束标记或提取校验失败时，保留输入 CSV 和配置供排查。

Python 不在样本或轮次之间重复启动 UTest，也不删除 `gomore_his.data` 或 `gomore_his.data.bak`。历史状态备份、仿真历史恢复和本次归档由 C UTest 管理。

## 结果判断

- `sim_result.ini` 的 `version`、`run_id`、`mode`、`rounds` 和 `sample_count` 必须匹配本次配置。
- 最终状态以 C 端 `overall_status` 和每样本状态为准：`success`、`partial`、`failed`。`partial`/`failed` 会保留已提取产物、输出明确错误并以非零状态退出。
- 单个多人样本异常时，C 端结果文件记录异常样本及停止轮次；脚本保留该样本状态和历史归档，并继续报告其他样本结果。
- 乳酸阈数值无效或未输出，不代表仿真执行失败。阈值字段只从最终算法 UART/IMG 日志解析；状态仍按结果文件判断。
- 脚本会在原始日志中补充报告解析器所需的样本头和轮次标记，`lthr-multi-sample-report` 用于仿真后的报告处理，不负责启动仿真。

## 日志与产物

脚本记录完整 UART 原始日志、JSON 摘要、Markdown 报告（适用时）、`sim_result.ini` 副本和历史归档副本，均直接放在本次产物目录中；临时输入副本不保留。显式传入 `run_device_multi_sim.py --output-dir` 时，该路径就是最终产物目录；省略时保留默认的 `run_id` 隔离子目录。运行命令会打印最终产物绝对路径。多样本流程可用 `--with-img-extract` 附加提取 IMG 日志；它不改变仿真入口或 UTest 次数。

## 维护

- 修改固定入口或参数时，同步维护本节命令示例。
- C 配置、状态或归档契约变化时，核对 `framework/engine/sportEngine/alg_gomore/test/alg_gomore_offline_csv_test.c` 后再维护脚本与本 skill。
- 仿真调用只从本节列出的 Python 入口发起；通用设备操作交给 `watchlink-v3`。

## 参考资料

- 报告模板：`assets/report_template.md`
- Jira 评论模板：`assets/jira_comment_template.md`
- 批量汇总模板：`assets/batch_report_template.md`
- IMG 提取模板：`assets/img_report_template.md`
- 引导测试心率范围：`references/guide_test_hr_ranges.md`
- 输出字段：`references/summary_schema.md`
- 运行时诊断码：`references/runtime_error_codes.md`
