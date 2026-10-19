---
name: lthr-multi-sample-report
version: 3.2.7
description: "优先用于：多样本仿真报告、乳酸阈多样本仿真报告、LTHR 多样本仿真报告。用于乳酸阈(LTHR)多样本报告生成、三源对比、单版仿真、JIRA 对比、SDK 版本对比、Lark Base 读写、飞书发布和归档。触发词: 多样本仿真报告, 乳酸阈多样本仿真报告, LTHR多样本仿真报告, 重新输出多样本仿真报告, 输出多样本仿真报告, 多样本报告, 乳酸阈报告, 仿真报告, 对比报告, SDK版本对比, 三源对比, Garmin DUT SDK 对比, garmin对比, 多轮仿真一致性, BASE_CASE, JIRA_CASE, Lark Base, 飞书文档发布, 群通知, 归档发布。"
metadata:
  requires:
    bins: ["lark-cli", "zepp", "python3"]
---

# LTHR Multi-Sample Report

> 前置条件：CWD 在 Lyon 项目根目录。所有脚本参数详 `--help`。
> 新增：JIRA CASE 工作流需安装 zepp CLI（`curl -fsSL https://upload-cdn.zepp.com/other/zepp-cli/install.sh | bash`）。

## 使用场景

- 用户明确说“输出多样本仿真报告”“重新输出多样本仿真报告”“乳酸阈多样本仿真报告”时，优先使用本 skill。
- 用户要求“生成仿真报告”“出对比报告”“做 SDK 版本对比”。
- 用户要求“做三源对比”“看 garmin 对比”“分析多轮仿真一致性”。
- 用户要求“把 BASE_CASE/JIRA_CASE 数据写到 Lark Base”“从 Base 拉数据”。
- 用户要求“把报告发飞书”“发群通知”“做归档发布”。

## 凭证固化（免重复输入）

Base token、table-id 等常用凭证已固化到 `scripts/_pipeline_config.py` 中，来自此前对话的实际值。你可以直接运行以下简化命令，无需每次传递 `--base-token` / `--table-id` / `--base-template`：

如需更新凭证（如补全 `--gomore-folder` / `--chat-id`），编辑 `scripts/_pipeline_config.py` 即可。

## 快速启动

```bash
# 一键全流程（简化版，凭证已固化到 skill 中）
python3 scripts/run_pipeline.py \
  --offline-summary /tmp/lthr_multi_sim/device_multi_sim_<stamp>.json

# 或者指定日志文件
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log

# 全量显式版（可覆盖固化配置）
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log \
  --base-token <token> --table-id <id> \
  --base-template <源token> --gomore-folder <文件夹token> \
  --chat-id oc_xxxx

# JIRA CASE 全流程（步骤11-14）
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log --sdk-ver 1.0.16.0 \
  --jira-issues COLOGNE-3107 MAT-13034 MOD-7798 MOD-8699 WIN-1957 WIN-2552 \
  --steps 11-14

# JIRA CASE + BASE CASE 全流程
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log --sdk-ver 1.0.16.0 \
  --base-token X --table-id Y \
  --jira-issues COLOGNE-3107 MAT-13034 MOD-7798 MOD-8699 WIN-1957 WIN-2552 \
  --steps 1-14

# 干跑预览
python3 scripts/run_pipeline.py --sdk-ver 1.0.15.0 --dry-run

# 局部步骤
python3 scripts/run_pipeline.py --sdk-ver 1.0.15.0 --json data.json --steps 4-6

# 直接消费 lthr-offline-simulator 多样本输出
python3 scripts/run_pipeline.py \
  --offline-summary /tmp/lthr_multi_sim/device_multi_sim_20260512_101530.json \
  --steps 1,4-6

# 自动识别 summary 对应日志里的 BASE_CASE / JIRA_CASE，未显式传 --steps 时自动选择 1-10 / 11-14 / 1-14
python3 scripts/run_pipeline.py \
  --offline-summary /tmp/lthr_multi_sim/device_multi_sim_20260512_101530.json
```

说明：传 `--offline-summary` 时，pipeline 会优先读取 summary 里的 `artifacts.log_path` 作为解析输入。报告和中间 JSON 默认写入系统临时目录下按 summary 文件名生成的稳定任务目录，例如 `/tmp/lthr-multi-sample-report/summary_device_multi_sim_<stamp>_<hash>/`；重复使用同一个输入时会复用该目录，便于分阶段生成和发布。可用 `--output-dir` 指定其他位置。

说明：未传 `--output-dir` 时，`data_sample/` 仍作为自动查找已有样本 JSON/Base JSON 的备用输入目录，不再作为新报告和中间 JSON 的默认输出位置。

说明：`--offline-summary` 模式下，如果没有显式传 `--steps`，pipeline 会先扫描 raw log 中的 `CASE=BASE_CASE` / `CASE=JIRA_CASE`：

- 只有 `BASE_CASE`：自动执行 `1-10`
- 只有 `JIRA_CASE`：自动执行 `11-14`
- 同时包含两者：自动执行 `1-14`

说明：JIRA 路径在未显式传 `--jira-issues` 时，会优先从 `jira_samples_<sdk>.json` 推断；若还没有该文件，则回退到 raw log 的 `JIRA_CASE SAMPLE_DIR=` 记录自动恢复 issue 列表。

## 实战约定（2026-05-18 已验证）

### 常用模板

- mixed log 只生成本地报告：

```bash
python3 scripts/run_pipeline.py \
  --offline-summary /tmp/lthr_multi_sim/device_multi_sim_<stamp>.json \
  --steps 1,4,5,11-13
```

只会生成 BASE 三源报告、单版本仿真报告和 JIRA 对比报告，不会写 Base、发布飞书或发送群通知。

- mixed log 只发布飞书并归档，不发群通知：

```bash
python3 scripts/run_pipeline.py \
  --offline-summary /tmp/lthr_multi_sim/device_multi_sim_<stamp>.json \
  --steps 2,7,9,14
```

步骤 2 写 Base，步骤 7 发布 BASE 三源报告，步骤 9 归档日志，步骤 14 发布 JIRA 对比报告；不要带步骤 10。

### 关键规则

- 2026-05-21: 步骤 2 往新复制的 Base 写记录时，日志解析结果里可能带有模板中不存在的字段（例如 `仿真1_状态 / 仿真2_状态 / 仿真3_状态`）。若不先过滤，`base +record-upsert` 会整条失败，最终表现为“目录里有 bitable，但记录数为 0”。当前脚本已在写入前自动读取目标表字段并过滤不存在字段。

- 2026-05-22 交付口径补充：默认最终交付保留 7 项：汇总报告、日志压缩包、Base 数据表、三源对比报告、单版仿真报告、JIRA 对比报告、版本对比报告；`IMG 仿真报告` 默认视为中间产物，若用户未明确要求，不要上传到最终归档目录。另，汇总报告不是自动步骤，用户明确要求时需基于 `device_multi_sim_*.json`、最终飞书目录和各报告链接额外整理上传；步骤 5 的“单版本仿真报告”表示“单个 SDK 版本下的多样本汇总”，不是“单样本报告”。

- GoMore multi-sample 日志里的用户性别编码按模板 Base 校验后应解释为：`gender=0 -> 男`、`gender=1 -> 女`。不要沿用常见的 `1=男` 映射，否则 SDK 仿真报告和 JIRA 对比报告中的性别会整体反掉。

## 执行检查单（2026-05-22 新增）

当用户要求“重新输出多样本仿真报告”“重新发布报告”“补齐交付物”时，按下面顺序执行；不要边跑边补口径。

### 1. 先冻结交付 manifest

第一次生成或发布之前，先确认这次最终需要什么，不需要什么：

- 是否需要：三源对比报告
- 是否需要：单版本仿真报告（步骤 5）
- 是否需要：版本对比报告（步骤 6）
- 是否需要：JIRA 对比报告
- 是否需要：汇总报告
- 是否需要：Base 数据表
- 是否需要：日志压缩包
- 是否需要：IMG 仿真报告
- 是否需要：群通知

如果用户没有明确说明，按上面的“2026-05-22 交付口径补充”默认值处理。

### 2. 固定这次任务的唯一状态锚点

开始执行前，明确并记录：

- 当前使用的 `device_multi_sim_<stamp>.json`
- 当前 SDK 版本 `sdk_ver`
- 如需步骤 6，上一版 `prev_ver` 是什么
- 当前发布目录 `folder token`
- 当前 Base `base token` / `table_id`

后续所有生成、发布、校验，都只围绕这一组状态锚点进行。

### 3. 生成阶段和发布阶段必须分离

- 先本地生成：用 `--offline-summary` + 明确的 `--steps` 只出本地产物
- 再统一发布：确认本地产物齐全后，再跑发布/归档步骤
- mixed log 默认：先 `--steps 1,4,5,11-13`，再 `--steps 2,7,9,14`
- 只有用户明确要求时，才额外补步骤 6、步骤 10；汇总报告仍需单独整理

不要在本地产物还没核对完时，就穿插手工 `drive +import` 去补单个文档；否则很容易出现“本地一套、飞书一套、用户当前看到又是一套”的漂移。

如果用户不要步骤 5 或步骤 6，必须在 manifest 里先删掉，再执行，不要先生成后解释。

### 4. 发布前检查

发布前至少核对：

- 本地 `samples_<sdk>.json` 是否存在
- 本地 `base_data_<sdk>.json` 是否存在；若不存在，确认会触发自动补拉逻辑
- 如需步骤 6，旧版 `base_data_<prev_ver>.json` 是否已存在；若不存在，先从旧版 Base 拉取
- 当前 Markdown 里的 Base 链接是否指向新 Base，而不是模板 URL
- 当前目录 token 是否就是用户正在看的那个目录

### 5. 发布后目录审计

发布完成后，以当前目录 token 为准核对最终目录清单是否与 manifest 完全一致：

- 文件数是否正确
- 文档标题是否正确
- 是否存在用户明确说不要的交付物
- 汇总报告中的链接和目录实际内容是否一致

如果目录审计和 manifest 不一致，以目录审计为准继续修正，不要继续叠加新上传。

## 脚本速查

| 脚本 | 位置 | 功能 |
|------|------|------|
| `run_pipeline.py` | `skills/lthr-*/scripts/` | 全流程编排器（推荐入口） |
| `parse_gomore_samples.py` | `skills/lthr-*/scripts/` | 日志→JSON/CSV（支持 BASE_CASE / JIRA_CASE） |
| `feishu_base_utils.py` | `skills/lthr-*/scripts/` | Base 读写封装 |
| `generate_garmin_dut_compare_report.py` | `skills/lthr-*/scripts/` | 三源 (Garmin/DUT/SDK) 对比报告 |
| `generate_sim_report.py` | `skills/lthr-*/scripts/` | 单版本仿真报告 |
| `compare_base_samples.py` | `skills/lthr-*/scripts/` | 两版 SDK 对比报告 |
| `fetch_jira_baseline.py` | `skills/lthr-*/scripts/` | 从 JIRA 评论提取旧版 SDK 基准值 |
| `generate_jira_report.py` | `skills/lthr-*/scripts/` | 生成 JIRA CASE 新旧 SDK 对比报告 |

## 14 步速查

BASE CASE 用步骤 1-10，JIRA CASE 用步骤 11-14。

| 步骤 | 场景 | 操作 | 关键输入/说明 |
|------|------|------|---------------|
| 1 | BASE | 解析日志提取 BASE_CASE 样本 | `--log-file path --case BASE_CASE` |
| 2 | BASE | 写入飞书 Base | `--base-token X --table-id Y` |
| 3 | BASE | 拉取 Base 全表 | pipeline 自动执行 |
| 4 | BASE | 三源对比报告 | `scripts/generate_garmin_dut_compare_report.py --help` |
| 5 | BASE | 单版本仿真报告 | `scripts/generate_sim_report.py --help` |
| 6 | BASE | 版本对比报告 | `--prev-ver 1.0.9` |
| 7 | BASE | 发布飞书文档 | pipeline 自动执行 |
| 8 | BASE | 复制并归档 Base | `--base-template X` |
| 9 | BASE | 归档日志 | `--gomore-folder Y` |
| 10 | BASE | 群通知 | `--chat-id oc_xxx` |
| 11 | JIRA | 从日志解析 JIRA_CASE 样本 | `--jira-issues ISSUE1 ISSUE2 ...` |
| 12 | JIRA | 从 JIRA 评论提取旧版 SDK 基准值 | 自动执行 `fetch_jira_baseline.py` |
| 13 | JIRA | 生成新旧 SDK 对比报告 | 自动执行 `generate_jira_report.py` |
| 14 | JIRA | 发布对比报告到飞书文档 | pipeline 自动执行 |

### 注意事项

- **JIRA CASE 样本名**: 格式为 `ISSUE_KEY/filename`（如 `COLOGNE-3107/gomore_data_20260320_110740`），一个 Issue 可对应多个 CSV 文件。
- **单轮次仿真**: 大多数 JIRA CASE 只有 1 轮仿真结果（日志中有且仅有 `Round 1` 输出），报告会自动调整对比格式。
- **MAT-13034 特殊格式**: 该 Issue 的评论中 lh/mh 值含有 Jira Wiki 标记 `{color:#DE350B}`，提取脚本已自动兼容处理。
- **WIN-1957**: 该 Issue 评论中无 `Result #` 行（旧版未出值），报告将显示"无基准数据"。
- **基准值提取**: `fetch_jira_baseline.py` 通过 `zepp jira comment list` 提取，确保已安装 zepp CLI 并登录。

## 数据文件约定

| 类型 | 路径 |
|------|------|
| 日志 | `framework/engine/sportEngine/alg_gomore/data_sample/` |
| offline-simulator 多样本摘要 | `device_multi_sim_<stamp>.json` |
| 输入样本 JSON | Lyon 项目的 `data_sample/`（自动搜索备用目录） |
| 样本 JSON | 默认任务输出目录下的 `samples_*.json` / `base_data_*.json` |
| 三源报告 | 默认任务输出目录下的 `garmin_dut_sim_compare_report_*.md` |
| 仿真报告 | 默认任务输出目录下的 `sdk_*_sim_report.md` |
| 版本对比 | 默认任务输出目录下的 `sdk_*_vs_*_compare_report.md` |
| JIRA 样本 / 基准 JSON | 默认任务输出目录下的 `jira_samples_*.json` / `jira_baseline_*.json` |
| JIRA 对比报告 | 默认任务输出目录下的 `jira_compare_report_*.md` |

使用 `--offline-summary` 时，上述 `samples_*.json` / `base_data_*.json` / `sdk_*_sim_report.md` 默认输出到系统临时目录下按 summary 文件名生成的稳定任务目录。其他输入按输入类型和文件名生成对应目录。自动查找输入 JSON 时优先搜索输出目录，再回退搜索 Lyon 项目的 `data_sample/`；显式传入 `--output-dir` 时，输出目录和首选搜索目录都使用该路径。

在 `BASE_CASE + JIRA_CASE` 混合日志场景下，推荐直接用 `--offline-summary` 且不传 `--steps`，让 pipeline 自动串起两套报告；只有在你明确只想跑其中一侧时，才显式写 `--steps 1-10` 或 `--steps 11-14`。

## FAQ

**Q: Base 写入 ignored_fields？** 字段来自同步数据源为只读，需在飞书网页端取消同步后再写入。

**Q: table_id 怎么看？** Base 网页 URL 中 `?table=` 参数的值。或 `lark-cli base +field-list --base-token X`。

## 权限

> 使用 `lark-cli` 操作，需要 `base:record` / `docs:document` / `im:message` 等 scope。详见 lark-shared skill。
