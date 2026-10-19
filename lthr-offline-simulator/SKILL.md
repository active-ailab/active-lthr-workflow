---
name: lthr-offline-simulator
description: 在真机上对GoMore离线CSV进行仿真并取日志，也适用于乳酸阈引导测试目标心率区间/百分比计算。设备操作由 watchlink-v3 提供。触发词: gomore offline csv test, WL+DISK=FS, data_sample, 单样本仿真, 批量仿真, 多样本, utest, Jira回传, 乳酸阈引导测试, 引导测试百分比, 目标心率区间, maxHr, restHr。
---

# LTHR Offline Simulator

在真机上对 GoMore 离线 CSV 做单样本、批量和多样本仿真，并生成可回传 Jira 的结果文件。通用的 WSL bridge、端口探测、UART/MGR 路由和 watchlink 入口能力由 watchlink-v3 skill 提供；本 skill 只保留 LTHR 工作流入口、输出物和专项参数。

## 使用边界

- 适用场景：GoMore offline CSV 真机仿真、Jira CSV 附件回放、`data_sample` 多样本批跑、IMG 日志提取。
- 适用场景：根据 `maxHr` / `restHr` 计算乳酸阈引导测试各阶段目标心率区间。
- 不适用场景：纯 watchlink 串口发命令、刷机、monkey、端口选择问题；这类直接走 watchlink-v3 skill。
- 如需设备操作帮助，请优先查看 `agent_skills/skills/device/watchlink-v3/SKILL.md` 的触发词列表。
- WSL 主流程默认省略 `--disk-root`，让脚本走 Windows data-root 稳定路径。
- 多设备或端口歧义时显式传 `--mgr-port` 和 `--uart-port`。
- 共享 helper 已内建 bridge 异常、COM 口占用、post-unmount reset、shell-ready 提示；skill 文档不再重复长篇排障清单。
- 引导测试心率区间的阶段百分比、公式和示例见 `references/guide_test_hr_ranges.md`。

| 工作流 | 脚本 | 数据源 | 产出 |
|--------|------|--------|------|
| **单样本** | `run_lthr_offline_sim.py` | Jira CSV / 本地文件 | log+json+md → Jira |
| **批量** | `run_lthr_batch.py` | 多个 Jira | 逐 issue 独立回传 + 聚合报告 |
| **多样本** | `run_device_multi_sim.py` | PC `data_sample/` | log → pipeline 出报告 |
| **多样本(mount-log)** | `run_device_multi_sim.py --with-mount-log` | PC `data_sample/` | 完整日志 → pipeline 出完整报告 |
| **快速 utest** | `run_utest_only.py` | 设备上已有数据 | 仅 UART utest 日志 |

**依赖**: watchlink-v3 runtime 入口（`wlctl.sh` / `wlctl.ps1` + co-located Python SDK）+ `zepp jira` + Python 3.8  
**通道路由**: 基于 watchlink-v3 runtime 的 `--role UART/MGR` 机制切换；当前仿真 skill 已不再依赖 `v3dl com` 选口流程  
**跨平台**: WSL 通过 Windows data-root + WLINK 端口对工作；Win `D:`–`Z:` / `.ps1` 自动包装

---

## 一、单样本 (`run_lthr_offline_sim.py`)

流程: 下载CSV → 下发设备并清理旧状态 → 拷CSV → 重启 → `utest` × `--runs` 次 → 提取结果 → Jira回传。多轮默认沿用设备历史状态；传 `--clean-between-runs` 可在每轮间清理历史并重启设备。

```bash
python3 scripts/run_lthr_offline_sim.py <ISSUE_KEY> --mgr-port COM37 --uart-port COM36   # WSL 推荐: 在线下载→仿真→回传
python3 scripts/run_lthr_offline_sim.py <ISSUE_KEY> --csv-path /tmp/s.csv --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_offline_sim.py <ISSUE_KEY> --csv-path /tmp/s.csv --runs 5 --clean-between-runs --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_offline_sim.py <ISSUE_KEY> --all-csvs --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_offline_sim.py <ISSUE_KEY> --csv-path /tmp/s.csv --skip-jira --deploy-only --mgr-port COM37
# 只做挂盘/清理/复制/校验/卸盘健康检查，不进入 UART utest
```

### 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `ISSUE_KEY`（位置参数） | 必填 | Jira issue key，如 `ITS-51497` |
| `--runs` | 1 | 仿真次数；多轮用于趋势分析 |
| `--clean-between-runs` | 关闭 | 多轮时在每轮之间清理历史文件并重启设备，提升各轮独立性 |
| `--csv-path` | — | 本地 CSV 文件（隐含 `--skip-jira`） |
| `--csv-name` | — | 指定 CSV 附件名或 ID |
| `--all-csvs` | — | 跑所有 `gomore_data_*.csv` 附件 |
| `--disk-root` | 自动检测 | 仅非 WSL 或已手工挂载场景使用；当前 WSL 稳定路径建议留空 |
| `--mgr-port` / `--uart-port` | 自动检测 | 基于 Windows started WLINK 端口对自动识别；多设备时显式指定串口 |
| `--deploy-only` | — | 只做部署健康检查，跳过 UART utest |
| `--skip-jira` | — | 跳过 Jira 通信，仅存本地 |
| `--no-comment` | — | 不上传 Jira 评论（仍上传附件） |
| `--boot-timeout-sec` | 180 | 设备启动等待超时 |
| `--run-timeout-sec` | 2400 | 单次仿真超时 |
| `--deploy-attempts` | 3 | 下发设备阶段（挂盘/清理/复制/卸载）失败时的自动重试次数 |
| `--model-name` | 自动检测 | AI 模型名称（用于报告签名，默认从环境变量/config 读取） |
| `--reasoning-effort` | 自动检测 | 推理强度等级（用于报告签名，默认从环境变量/config 读取） |

### 运行约束

- 前一轮残留 `gomore_his.data` / `lactateData.data` 会污染下一轮结果；完整脚本会在下发阶段主动清理并校验。
- `utest` 按文件 mtime 取最新 CSV；脚本会把非标准命名自动标准化成 `gomore_data_YYYYMMDD_HHMMSS.csv`，并校验设备根层只剩一个 CSV。
- WSL 下如果直接看到 bridge timeout、UNC、COM 口 denied、ready timeout，先看脚本打印出的 `[HINT]`，不要再回到 skill 文档里手工拼排障步骤。

---

### 输出产物

`<temp>/topic_ota/<ISSUE_KEY>/offline_sim_<stamp>/`:

| 文件 | 说明 |
|------|------|
| `report_<stamp>.md` | 可读报告 + 趋势分析(均值/标准差/CV/斜率) |
| `results_<stamp>.json` | 机器可读仿真结果 |

模板与维护入口：

- 报告模板：`assets/report_template.md`
- Jira 评论模板：`assets/jira_comment_template.md`
- 批量汇总模板：`assets/batch_report_template.md`
- UART-only 摘要模板：`assets/utest_summary_template.md`
- IMG 提取模板：`assets/img_report_template.md`
- 占位符与维护规则：`references/output_templates.md`
- 运行时错误码：`references/runtime_error_codes.md`
- 统一摘要结构：`references/summary_schema.md`

报告签名格式为 `（by {model} | reasoning={effort}）`，自动从环境变量或 `~/.codex/config.toml` 读取。Jira 评论固定包含仿真样本与结果摘要，不包含模型签名、Issue 行或标题行。

---

## 二、批量 (`run_lthr_batch.py`)

逐个 issue 串行调用 `run_lthr_offline_sim.py`，每个独立走完下载→仿真→回传，产出聚合报告。

```bash
python3 scripts/run_lthr_batch.py --issues MOD-7923 MOD-7924 --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_batch.py --issues-file batch.txt --runs 10 --continue-on-error
python3 scripts/run_lthr_batch.py --issues MOD-7923 MOD-7924 --csv-path /tmp/sample.csv
```

| 参数 | 说明 |
|------|------|
| `--issues` / `--issues-file` | Issue key 列表 或 文件(每行一个，`#`注释) |
| `--continue-on-error` | 跳过失败继续 |
| 其余 | 透传 `run_lthr_offline_sim.py` 参数 |

产出：`batch_dir/<issue>/artifacts/` + `batch_<stamp>_consolidated.md` 汇总结果。

---

## 三、多样本 (`run_device_multi_sim.py`)

流程：挂 data 盘拷 `data_sample` → 等启动 → 监听 `multi_tests` → 可选提取 IMG 日志。

**data_sample 来源**：优先从飞书云盘 `HYS7fstGll8lGedAVKFc6RNxnAb` 拉取；若失败则需显式指定 `--data-dir`（不再默认使用 Lyon 本地路径）。

```bash
python3 scripts/run_device_multi_sim.py --mgr-port COM37 --uart-port COM36                                       # 基础（从云盘拉取 data_sample）
python3 scripts/run_device_multi_sim.py --data-dir /path/to/data_sample --with-img-extract                        # 指定本地目录+提取完整日志
python3 scripts/run_device_multi_sim.py --with-img-extract --pre-run-delay-sec 60                                 # 等待60秒再发命令
python3 scripts/run_device_multi_sim.py --img-only                                                                # 仅提取已有日志

# 多样本完成后，直接把 summary 交给报告 skill
python3 ~/.agents/skills/lthr-multi-sample-report/scripts/run_pipeline.py \
	--offline-summary /tmp/lthr_multi_sim/device_multi_sim_20260512_101530.json \
	--steps 1,4-6

# 如果这次 multi log 同时包含 BASE_CASE 和 JIRA_CASE，不传 --steps 让报告 pipeline 自动分流
python3 ~/.agents/skills/lthr-multi-sample-report/scripts/run_pipeline.py \
	--offline-summary /tmp/lthr_multi_sim/device_multi_sim_20260512_101530.json
```

### 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--data-dir` | 飞书云盘 `HYS7...xnAb`（自动拉取） | PC 端 data_sample 目录。不传时尝试从飞书云盘拉取，失败时报错要求指定 |
| `--mgr-port` / `--uart-port` | 自动检测 | 基于 Windows started WLINK 端口对自动识别；多设备时显式指定串口 |
| `--run-timeout-sec` | 72000 | 仿真超时(20h) |
| `--pre-run-delay-sec` | 30 | 设备就绪后等 N 秒再发测试命令，避免日志溢出 |
| `--with-img-extract` / `--img-only` | — | **推荐** IMG 日志提取（获取完整日志替代 UART），报告 pipeline 自动优先使用 |
| `--keep-device-logs` | — | 提取后保留设备日志 |

说明：`img_log_extractor.py` 在未显式传 `--output-dir` 时，默认输出到系统临时工作目录，而不是 `skills/lthr-offline-simulator` 目录。

对接报告 skill 时，优先把 `run_device_multi_sim.py` 的 `device_multi_sim_<stamp>.json` 作为输入，而不是手工再找 raw log。报告 pipeline 会从该 summary 的 `artifacts.log_path` 自动恢复解析输入，并默认把生成的 `samples_*.json` 和 Markdown 报告放到同一目录。

如果同一份 multi log 里同时有 `BASE_CASE` 和 `JIRA_CASE`，报告 pipeline 在未显式传 `--steps` 时会自动探测并切到 `1-14`，同时从 raw log 或 `jira_samples_<sdk>.json` 自动恢复 `--jira-issues`，不需要再手工抄 issue 列表。

多样本状态判定以脚本输出的 summary JSON 为准，不再要求 raw UART log 一定出现 `GoMore Offline CSV Test Start`。在 Lyon 真机上，multi run 可能缺失 start marker，但只要已经出现样本进度、样本 PASS/FAIL、summary 或 `GoMore Offline CSV Test End`，`run_device_multi_sim.py` 就会按“测试已启动/已完成”处理，而不是误报 `test_not_started`。

### `data_sample` 结构

```
data_sample/
├── BASE_CASE/01lyh~13.../
├── JIRA_CASE/{COLOGNE-3107,WIN-1957}/
├── ROOT_CASE/gomore_data_*.csv
└── base_case_updates_*.csv / *.json
```

---

## 四、快速模式 (`run_utest_only.py`)

数据已在设备上时，跳过磁盘操作直跑 utest。**无需 sudo**。

```bash
python3 scripts/run_utest_only.py --uart-port COM36   # 前提: data_sample/已有CSV，状态已清理
```

**适用场景**: 重跑 / sudo不可用 / 多Jira第二轮 / 快速调试  
**原理**: `send_uart_cmd()` → `listen_uart()` 循环匹配 START/END marker → 正则提取结果

| 参数 | 默认 | 说明 |
|------|------|------|
| `--uart-port` | `COM36` | UART 串口 |
| `--run-timeout-sec` | 300 | 等待超时 |

**与完整脚本对比**: 磁盘操作❌ / 状态清理❌ / 重启❌ / 趋势分析❌ / Jira回传❌ / sudo依赖**无** ✅。耗时约 5 分钟，对比完整流程约 30 分钟。

---

## 维护规则

- 通用串口、bridge、端口配对和 watchlink 快检规则，统一保留在 watchlink-v3 skill，不在本 skill 复制。
- 输出文档骨架放 `assets/`，模板占位符和修改约束放 `references/`，不要再把完整模板内联回 `SKILL.md`。
- 运行时下一步动作优先写进共享 Python helper 或 argparse help，而不是继续扩写 markdown 排障章节。
