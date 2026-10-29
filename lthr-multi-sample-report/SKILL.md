---
name: lthr-multi-sample-report
description: "优先用于：多样本仿真报告、乳酸阈多样本仿真报告、LTHR 多样本仿真报告。用于乳酸阈(LTHR)多样本报告生成、三源对比、单版仿真、JIRA 对比、SDK 版本对比、Lark Base 读写、飞书发布和归档。触发词: 多样本仿真报告, 乳酸阈多样本仿真报告, LTHR多样本仿真报告, 重新输出多样本仿真报告, 输出多样本仿真报告, 多样本报告, 乳酸阈报告, 仿真报告, 对比报告, SDK版本对比, 三源对比, Garmin DUT SDK 对比, garmin对比, 多轮仿真一致性, BASE_CASE, JIRA_CASE, Lark Base, 飞书文档发布, 群通知, 归档发布。"
metadata:
  version: "3.2.14"
  requires:
    bins: ["lark-cli", "python3"]
---

# LTHR Multi-Sample Report

> 前置条件：CWD 在 Lyon 项目根目录。所有脚本参数详 `--help`。
> JIRA 评论读取以使用者本地环境为准：先检查当前 PATH 中的 CLI；有 `zepp` 时使用 `zepp jira comment list`，没有 `zepp` 或其调用失败时，使用本机已安装并已配置的 `jira` CLI（`jira issue view --comments 100 --raw`）。`zepp` 不是必需依赖，不要仅为运行本 Skill 要求用户安装它；两种 CLI 都不可用时再报告缺少可用工具。只有能确认评论结果属于目标旧 SDK 版本时，才可作为版本基准。

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

### 新仿真与报告的衔接

- 本 Skill 的整体流程可以先调用 `lthr-offline-simulator` 生成仿真结果，再调用本目录的 `run_pipeline.py` 做解析、对比和发布。`run_pipeline.py` 只消费仿真 summary/log，不会自行启动 UTest。
- 每次由本 Skill 发起的新仿真，固定通过离线仿真入口传入 `--rounds 3`；仿真模式必须按样本关系显式选择，不能从 CSV 数量或目录名推断。时间戳推进沿用离线仿真代码规则：单样本按轮次推进；单人多样本按“轮次优先、样本其次”推进；多人多样本按“样本优先、轮次其次”遍历，切换样本不推进时间戳。同一模式的详细起始时间戳和步进规则以 `lthr-offline-simulator` Skill 为准。
- Jira 附件仿真使用 `run_lthr_offline_sim.py`；多附件必须按离线仿真 Skill 的约定显式选 `sequential` 或 `single_person_multi_sample`。本地 `data_sample` 仿真使用 `run_device_multi_sim.py`，显式传 `--mode` 与 `--rounds 3`。
- 仿真结束后，以入口输出的 `[OK] json=...` 或 `[完成] JSON 摘要: ...` 路径作为 `--offline-summary`，不要猜测或拼接 summary 文件名。summary 内的 `artifacts.log_path` 是日志解析输入；Jira issue key 从 Jira 仿真 summary 的 `issue.key` 获取。
- 如果用户只要求重出已有仿真报告，直接使用指定的 summary 或日志，不重新运行仿真。
- 旧的单轮仿真日志和 Jira 评论仍可用于历史报告。新发起的仿真固定三轮；报告模板最多呈现前三轮，仿真状态以 C 端 summary 为准。没有有效 LTHR 结果表示本轮没有可对比阈值，不单独据此判为仿真失败。

仿真入口的模式按样本关系选择：

| `--mode` | 样本关系 | 时间戳遍历 |
|---|---|---|
| `single_sample` | 一人一个样本 | 轮次递增时推进一天 |
| `single_person_multi_sample` | 多个样本属于同一人 | 轮次优先，每轮遍历全部样本；每个样本轮次推进一天 |
| `multi_person_multi_sample` | 多个样本各属于不同人 | 样本优先，每个样本完成所有轮次；切换样本不推进，轮次推进一天 |

例如，多附件 Jira 单人多样本仿真由离线入口显式指定模式和三轮；之后将入口打印出的 JSON 路径传给报告 pipeline：

```bash
python3 /home/zepp/workspace/active-lab/active-lthr-workflow/lthr-offline-simulator/scripts/run_lthr_offline_sim.py \
  ITS-51497 --all-csvs \
  --multi-attachment-mode single_person_multi_sample --rounds 3

python3 scripts/run_pipeline.py --offline-summary "<上一步输出的 JSON 路径>"
```

```bash
# 一键全流程（简化版，凭证已固化到 skill 中）
python3 scripts/run_pipeline.py \
  --offline-summary "<仿真入口输出的 JSON 路径>"

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
  --offline-summary "<Jira 仿真入口输出的 JSON 路径>" \
  --steps 11-14

# SDK 版本对比：先从上一版 JIRA 报告提取与 --prev-ver 匹配的逐轮基准
python3 scripts/extract_jira_report_baseline.py \
  --report "<上一版 JIRA 对比报告.md>" --expected-version 1.0.20.2 \
  -o /tmp/jira_baseline_1.0.20.2.json
python3 scripts/run_pipeline.py \
  --offline-summary "<本次 Jira 仿真 summary JSON>" --prev-ver 1.0.20.2 \
  --jira-baseline /tmp/jira_baseline_1.0.20.2.json \
  --jira-baseline-source "上一版 JIRA 对比报告：<链接>" --steps 11-13

# JIRA CASE + BASE CASE 全流程
python3 scripts/run_pipeline.py \
  --offline-summary "<包含 BASE_CASE 和 JIRA_CASE 的仿真 summary 路径>" \
  --base-token X --table-id Y \
  --steps 1-14

# 干跑预览
python3 scripts/run_pipeline.py --sdk-ver 1.0.15.0 --dry-run

# 局部步骤
python3 scripts/run_pipeline.py --sdk-ver 1.0.15.0 --json data.json --steps 4-6

# 直接消费 lthr-offline-simulator 多样本输出
python3 scripts/run_pipeline.py \
  --offline-summary "<仿真入口输出的 JSON 路径>" \
  --steps 1,4-6

# 自动识别 summary 对应日志里的 BASE_CASE / JIRA_CASE，未显式传 --steps 时自动选择 1-10 / 11-14 / 1-14
python3 scripts/run_pipeline.py \
  --offline-summary "<仿真入口输出的 JSON 路径>"
```

说明：传 `--offline-summary` 时，pipeline 会优先读取 summary 里的 `artifacts.log_path` 作为解析输入，并从仿真日志自动提取 SDK 版本。若日志没有 SDK 版本行，才手动传 `--sdk-ver`。报告和中间 JSON 默认写入系统临时目录下按 summary 文件名生成的稳定任务目录，例如 `/tmp/lthr-multi-sample-report/summary_lthr_offline_sim_<issue>_<stamp>_<hash>/`；重复使用同一个输入时会复用该目录，便于分阶段生成和发布。可用 `--output-dir` 指定其他位置。

说明：未传 `--output-dir` 时，`data_sample/` 仍作为自动查找已有样本 JSON/Base JSON 的备用输入目录，不再作为新报告和中间 JSON 的默认输出位置。

说明：`--offline-summary` 模式下支持 summary 的 `single_sample`、`multi_sample` 工作流。如果没有显式传 `--steps`，pipeline 会先扫描 raw log 中的 `CASE=BASE_CASE` / `CASE=JIRA_CASE`：

- 只有 `BASE_CASE`：自动执行 `1-10`
- 只有 `JIRA_CASE`：自动执行 `11-14`
- 同时包含两者：自动执行 `1-14`

说明：Jira 仿真 summary 的 `source.kind=jira_attachment` 且含 `issue.key` 时，pipeline 会自动恢复 issue key，并将扁平 `JIRA_CASE` 样本映射成 `ISSUE_KEY/原始CSV文件名`，去掉暂存时添加的 `sample_NNN_` 前缀。无 summary 时仍可从已有 Jira JSON 或旧日志推断；直接传新式扁平 Jira 日志时，请显式传唯一的 `--jira-issues ISSUE_KEY`。

## 实战约定（2026-05-18 已验证）

### 常用模板

- mixed log 只生成本地报告：

```bash
python3 scripts/run_pipeline.py \
  --offline-summary "<仿真入口输出的 JSON 路径>" \
  --steps 1,4,5,11-13
```

只会生成 BASE 三源报告、单版本仿真报告和 JIRA 对比报告，不会写 Base、发布飞书或发送群通知。

- mixed log 只发布飞书并归档，不发群通知：

```bash
python3 scripts/run_pipeline.py \
  --offline-summary "<仿真入口输出的 JSON 路径>" \
  --steps 2,7,9,14
```

步骤 2 写 Base，步骤 7 发布 BASE 三源报告，步骤 9 归档日志，步骤 14 发布 JIRA 对比报告；不要带步骤 10。

### 关键规则

- 2026-05-21: 步骤 2 往新复制的 Base 写记录时，日志解析结果里可能带有模板中不存在的字段（例如 `仿真1_状态 / 仿真2_状态 / 仿真3_状态`）。若不先过滤，`base +record-upsert` 会整条失败，最终表现为“目录里有 bitable，但记录数为 0”。当前脚本已在写入前自动读取目标表字段并过滤不存在字段。

- 2026-05-22 交付口径补充：默认最终交付保留 7 项：汇总报告、日志压缩包、Base 数据表、三源对比报告、单版仿真报告、JIRA 对比报告、版本对比报告；`IMG 仿真报告` 默认视为中间产物，若用户未明确要求，不要上传到最终归档目录。步骤 5 的“单版本仿真报告”表示“单个 SDK 版本下的多样本汇总”，不是“单样本报告”。汇总报告不是自动步骤，应基于 `device_multi_sim_*.json`、最终飞书目录和各报告链接整理上传；若用户要求版本验证/对比，应至少同时生成并发布步骤 5 单版报告、步骤 6 版本对比报告和汇总报告。JIRA 对比报告仅在本次有 JIRA_CASE 数据时交付。

- 2026-10-29 历史输入与 JIRA 基准校验：用户提供历史数据目录时，先列出所有 case 子目录和 CSV 数量，至少检查 `BASE_CASE` 与 `JIRA_CASE`；不得因本次只向仿真器传入一个 case 子目录，就推断另一个 case 不存在。对每个实际存在且属于本次范围的 case 完成仿真、报告与归档；两类 case 需要不同仿真模式时可分开运行，但要在汇总和证据包中关联各自 run。SDK 对比中的 JIRA 基准必须与 `--prev-ver` 一致：优先从上一版已发布的 JIRA 对比报告抽取该版三轮结果，也可使用明确标注目标版本的 Jira 评论；若无法区分版本，不得拿较早版本结果冒充上一版基准。已有报告可通过 `scripts/extract_jira_report_baseline.py --report <md> --expected-version <prev-ver> -o <json>` 转成三轮基准，并以 `--jira-baseline`、`--prev-ver` 和 `--jira-baseline-source` 传给 pipeline。raw log 可能漏写重复 `SAMPLE[...]` 标头；若同时有离线仿真 summary，JIRA 结果按 `samples[].sample_path` 和 `results[].run` 与 C 端逐轮结果对齐，分别保留 `result_missing` 和 `invalid_result` 状态。

- 若当前项目或运行环境要求在报告中加入负责人/组织，通过 pipeline 的 `--report-owner` 与 `--report-organization` 传入；Skill 不内置个人身份值。

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

- 当前使用的仿真 summary JSON（以仿真入口实际打印出的路径为准）
- 本次 `run_id`、仿真 `mode`、配置 `rounds`、样本数及 C 端 summary `status`
- 当前 SDK 版本 `sdk_ver`
- 如需步骤 6，上一版 `prev_ver` 是什么
- 当前发布目录 `folder token`
- 当前 Base `base token` / `table_id`

后续所有生成、发布、校验，都只围绕这一组状态锚点进行。

### 3. 生成阶段和发布阶段必须分离

- 先本地生成：用 `--offline-summary` + 明确的 `--steps` 只出本地产物
- 再统一发布：确认本地产物齐全后，再跑发布/归档步骤
- mixed log 默认：先 `--steps 1,4,5,11-13`，再 `--steps 2,7,9,14`
- 非版本对比的普通多样本任务不默认执行步骤 6；SDK 版本验证/对比任务必须生成步骤 6。步骤 10 仅在用户要求群通知时执行；汇总报告仍需单独整理。

SDK 版本验证/对比请求本身即要求步骤 5 与步骤 6，不得只生成版本差异报告。发布到指定目录时，需分别发布单版仿真报告、版本对比报告，并创建汇总报告列出运行状态、对比范围、输入可比性事项和实际交付链接。步骤 7 当前只发布三源报告，不代表步骤 5/6 的文档已发布；每份文档均须用 `docs +fetch` 回读，并在目录审计中确认。

SDK 版本验证/对比的最终交付清单固定为：汇总报告、步骤 5 单版仿真报告、步骤 6 版本对比报告、步骤 4 三源对比报告、新建并写入本次样本的 Base 数据表、本次运行日志压缩包；本次存在 `JIRA_CASE` 时还需交付 JIRA 对比报告。用户指定了 Drive 目录时，以上六项（有 JIRA 时为七项）都必须在该目录，不能只把文档留在飞书、把 Base 或日志留在另一个归档目录。步骤 8 复制 Base 后必须核对表字段并写入当前运行样本；记录数应与本次解析样本数一致，不能把“目录里有空 Base”视为完成。步骤 9 的归档必须有本次运行文件，不得复用旧版本日志包。

发布完成后先按清单逐项列出 Drive 目录：无 JIRA 输入时应有 6 项，有 JIRA 输入且需要报告时应有 7 项。逐项核对标题、类型和链接；Base 回读全部记录并核对样本数；下载或列出日志包内容，确认包含本次设备日志、设备 JSON summary、仿真配置/结果、每个样本的历史快照及可用的输入 CSV。只有本次实际产生的 IMG 日志才放入包中；未产生的文件不得补造。汇总报告必须链接 Base 和日志包，并说明本次不含 JIRA 时不交付 JIRA 报告。若目录项目数或 Base 记录数不符，修复后重新审计，不能以旧目录的文件名推断新版产物已齐。

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
| `extract_jira_report_baseline.py` | `skills/lthr-*/scripts/` | 从指定旧版 JIRA 报告提取该版逐轮基准 |
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
| 11 | JIRA | 从日志解析 JIRA_CASE 样本 | 优先使用 summary 的 `issue.key` 自动归属；无 summary 时可传 `--jira-issues ISSUE_KEY` |
| 12 | JIRA | 选取版本匹配的旧版基准 | 显式基准 JSON 优先；否则读取已确认版本的 Jira 评论 |
| 13 | JIRA | 生成新旧 SDK 对比报告 | 自动执行 `generate_jira_report.py` |
| 14 | JIRA | 发布对比报告到飞书文档 | pipeline 自动执行 |

### 注意事项

- **JIRA CASE 样本名**: 报告 JSON key 为 `ISSUE_KEY/原始CSV文件名不含扩展名`（如 `COLOGNE-3107/gomore_data_20260320_110740`）。Jira 离线仿真的暂存文件可能带 `sample_NNN_` 前缀；解析时会根据 summary 的 `issue.key` 还原 issue 和原始文件名，以匹配 Jira 评论基准。
- **轮次口径**: 本 Skill 发起的新仿真固定传 `--rounds 3`，轮次数由仿真入口写入配置并由 C 端执行；报告使用仿真1-3字段，不从样本数、日志长度或基准评论动态决定仿真轮数。历史单轮日志及旧版单结果 Jira 评论仍兼容，报告按可用数据呈现。
- **仿真状态与阈值结果**: `success` / `partial` / `failed` 来自 C 端 summary。`partial`、`failed` 会在 pipeline 输出警告并写入 Jira 对比报告状态；没有有效 LTHR 结果只表示没有可比较的阈值，不能单独作为仿真失败依据。各轮结果先从最终设备日志提取；若有离线 summary，则以 C 端逐样本逐轮结果校准。
- **SDK 版本**: 默认从实际设备日志中的 `SDK Version` 行提取；不使用手工猜测的安装包版本。日志未包含版本信息时，才显式传 `--sdk-ver`。
- **Jira 附件**: 离线仿真回传 Jira 时由仿真入口打包并上传单个 ZIP；本 Skill 通过 Jira 评论读取基准结果，不逐个下载或回传仿真产物。
- **MAT-13034 特殊格式**: 该 Issue 的评论中 lh/mh 值含有 Jira Wiki 标记 `{color:#DE350B}`，提取脚本已自动兼容处理。
- **WIN-1957**: 该 Issue 评论中无 `Result #` 行（旧版未出值），报告将显示"无基准数据"。
- **基准值提取**: `fetch_jira_baseline.py` 按当前使用者本机 PATH 选择工具：优先使用可用的 `zepp jira comment list`，缺少或调用失败时回退到本机已安装并已配置的 `jira issue view --comments 100 --raw`；不要求固定安装 Zepp CLI。评论中若有多个 SDK 版本，需先核对版本归属。版本对比优先用上一版 JIRA 报告生成逐轮 JSON 基准，不确定版本的评论不可直接作为基准。

## 数据文件约定

| 类型 | 路径 |
|------|------|
| 日志 | `framework/engine/sportEngine/alg_gomore/data_sample/` |
| 本地配置仿真摘要 | 默认 `/tmp/tmp_lthr_out/lthr_multi_sim/<run_id>/device_multi_sim.json`；显式 `--output-dir` 时为该目录下 `device_multi_sim.json` |
| Jira 附件仿真摘要 | 默认 `/tmp/tmp_lthr_out/topic_ota/<issue>/offline_sim_<timestamp>/lthr_offline_sim_<issue>_<timestamp>.json` |
| 输入样本 JSON | Lyon 项目的 `data_sample/`（自动搜索备用目录） |
| 样本 JSON | 默认任务输出目录下的 `samples_*.json` / `base_data_*.json` |
| 三源报告 | 默认任务输出目录下的 `garmin_dut_sim_compare_report_*.md` |
| 仿真报告 | 默认任务输出目录下的 `sdk_*_sim_report.md` |
| 版本对比 | 默认任务输出目录下的 `sdk_*_vs_*_compare_report.md` |
| JIRA 样本 / 基准 JSON | 默认任务输出目录下的 `jira_samples_*.json` / `jira_baseline_*.json` |
| JIRA 对比报告 | 默认任务输出目录下的 `jira_compare_report_*.md` |

使用 `--offline-summary` 时，上述 `samples_*.json` / `base_data_*.json` / `sdk_*_sim_report.md` 默认输出到系统临时目录下按 summary 文件名生成的稳定任务目录。其他输入按输入类型和文件名生成对应目录。自动查找输入 JSON 时优先搜索输出目录，再回退搜索 Lyon 项目的 `data_sample/`；显式传入 `--output-dir` 时，输出目录和首选搜索目录都使用该路径。

在 `BASE_CASE + JIRA_CASE` 混合日志场景下，推荐直接用 `--offline-summary` 且不传 `--steps`，让 pipeline 自动串起两套报告；只有在你明确只想跑其中一侧时，才显式写 `--steps 1-10` 或 `--steps 11-14`。BASE/JIRA 样本应放在 `data_sample/BASE_CASE/`、`data_sample/JIRA_CASE/` 下；根目录 CSV 会标记为 `ROOT_CASE`，不会自动归入这两类报告。

如果从飞书只下载了 `BASE_CASE` 或 `JIRA_CASE` 文件夹的内容，本地仍要保留对应 case 层级，例如 `data_sample/BASE_CASE/<sample>/gomore_data_*.csv`；`--data-dir` 指向 `data_sample/` 父目录，不要直接指向 `BASE_CASE/`。否则日志会标成 `ROOT_CASE`，`parse_gomore_samples.py --case BASE_CASE|JIRA_CASE` 将不匹配。若已有日志只有预期的 `ROOT_CASE` 样本，才可用 `--case ALL` 显式提取；解析后确认 `samples_<sdk>.json` 存在且样本数非零，再继续步骤 6。

## FAQ

**Q: Base 写入 ignored_fields？** 字段来自同步数据源为只读，需在飞书网页端取消同步后再写入。

**Q: table_id 怎么看？** Base 网页 URL 中 `?table=` 参数的值。或 `lark-cli base +field-list --base-token X`。

## 权限

> 使用 `lark-cli` 操作，需要 `base:record` / `docs:document` / `im:message` 等 scope。详见 lark-shared skill。
