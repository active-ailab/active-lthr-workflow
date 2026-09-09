---
name: lthr-jira-analyzer
description: 分析乳酸阈值日志（LTHR）并回传 Jira。适用于单个 Jira 问题或批量问题：自动检查附件是否含 txt/log、必要时走 zepp feedback 下载、解压日志、调用 gomore_log_to_csv.py 生成 CSV、上传 CSV、写 Jira 评论摘要；批量模式额外生成每个 Jira 的单问题描述总结报告。
---

# LTHR Jira Analyzer

使用场景：
- 用户要求“乳酸阈值日志分析 / lthr log 解析 / gomore 日志转样本”。
- 需要对一个或多个 Jira issue 批量执行“下载日志 -> 解析 -> 上传 -> 评论”。
- 批量完成后需要输出每个 Jira 的总结报告。

## 执行脚本

主脚本：
- `skills/lthr/lthr-jira-analyzer/scripts/run_lt_log_analysis.py`

依赖脚本：
- `skills/lthr/lthr-jira-analyzer/scripts/gomore_log_to_csv.py`
- `skills/lthr/lthr-jira-analyzer/scripts/csv_deep_analyzer.py` (Feature 3：深度CSV分析报告)

批量模板与一键入口：
- `skills/lthr/lthr-jira-analyzer/scripts/issues.txt`
- `skills/lthr/lthr-jira-analyzer/scripts/run.bat`

## 用法

单问题分析：
```bash
python3 skills/lthr/lthr-jira-analyzer/scripts/run_lt_log_analysis.py ITS-51120
```

批量分析（命令行传多个 key）：
```bash
python3 skills/lthr/lthr-jira-analyzer/scripts/run_lt_log_analysis.py ITS-51120 ITS-50805 ITS-50755
```

批量分析（从文件读 key，一行一个）：
```bash
python3 skills/lthr/lthr-jira-analyzer/scripts/run_lt_log_analysis.py --issue-file /path/issues.txt
```

指定批量报告输出路径：
```bash
python3 skills/lthr/lthr-jira-analyzer/scripts/run_lt_log_analysis.py --issue-file /path/issues.txt --report /tmp/topic_ota/lthr_batch_report.md
```

Windows PowerShell 示例：
```powershell
python .\\skills\\lthr-jira-analyzer\\scripts\\run_lt_log_analysis.py ITS-51120
python .\\skills\\lthr-jira-analyzer\\scripts\\run_lt_log_analysis.py --issue-file .\\issues.txt
```

Windows 双击批量运行：
1. 编辑 `skills\\lthr-jira-analyzer\\scripts\\issues.txt`（每行一个 Jira key）
2. 双击 `skills\\lthr-jira-analyzer\\scripts\\run.bat`

## 参数说明

- `issue_keys`：一个或多个 Jira key。
- `--issue-file`：key 列表文件（每行一个，支持 `#` 注释行）。
- `--work-dir`：工作目录根路径（默认 `/tmp/topic_ota`）。
- `--txt-log`：显式指定日志文件路径（支持 `*.txt` / `*.log`），仅单 issue 模式可用。
- `--model-name`：评论签名中的模型名称（可选，不传则优先从环境变量读取）。
- `--reasoning-effort`：评论签名中的推理强度等级（可选，不传则优先从环境变量读取）。
- `--no-comment`：仅上传 CSV，不写 Jira 评论。
- `--collect-sample`：将样本 CSV 收录到飞书云盘 `GomoreSDK_Report/data_sample/JIRA_CASE/` 目录下（按 `{issue_key}_{原始文件名}` 命名）。
- `--plot`：为每个样本 CSV 生成 HR/SPEED 曲线 SVG，并与 CSV 一起上传 Jira（仅使用 Python 标准库）。
- `--report`：批量模式报告输出路径（默认自动生成 `lthr_batch_report_YYYYMMDD_HHMMSS.md`）。

## 关键领域知识

### 乳酸阈算法触发条件

GoMore 乳酸阈（LTHR）算法**仅在以下运动类型下生效**：
- **户外跑步**（workoutType=1，含操场跑圈）
- **户外跑步**（workoutType=31）

以下常见运动类型**不会输出乳酸阈值**：
- 户外步行（workoutType=2）
- 跑步机（workoutType=4）
- 越野跑（workoutType=15）
- 室内骑行 / 户外骑行等

> 因此分析前应先检查 Jira 中的运动类型。如果运动类型不是户外跑步，算法未输出乳酸阈值属于预期行为，无需进一步排查。

## 标准流程

1. 读取 Jira 元信息与附件列表。
2. 按 Jira 附件优先级执行：
3. 先检查并处理 `*.txt` / `*.log` 附件。
4. 若无上述附件，再检查并处理 `*.zip` 附件。
5. 若前两步“无可解析结果”（无 txt/log 或解析结果为空），再执行 `zepp feedback download` 并继续解析。
6. 自动解压 zip（含嵌套 zip），搜索 `*.txt` / `*.log`，优先取最大文件作为源日志。
7. 若 Jira 附件里原本没有 `*.zip`，则会把通过 `zepp feedback download` 下载到的 `*.zip` 压缩包，以及 `*.jpg` 图片自动回传到该 Jira。
7. 调用 `gomore_log_to_csv.py --json` 解析日志，生成样本 CSV。
8. 使用 `--plot` 时，为每个 CSV 生成 `*_hr_speed.svg`；上传 CSV、SVG、解析产生的日志文件（`*.log` / `*.txt`，不含 `hmble_log.log`）和 `*.jpg` 图片到对应 Jira。
9. 写入 Jira 评论摘要（可通过 `--no-comment` 关闭）。
10. 批量模式下生成汇总报告，包含每个 Jira 的单问题描述总结。

### 出值条件分析（基础分析）

主流程在 Jira 评论中同时输出 GoMore 出值条件检查，不把“满足运动条件”和“最终出值”混为一谈：

- 出值基础条件：`HR > 70%HRR`、`SPEED > 6 kph`，且同时满足时间超过 `720 s`。
- `70%HRR = (MaxHR - RestHR) × 70% + RestHR`。
- `[GoMoRe][EHisPd][maxHr]` 会记录最近最多 7 次 `maxHR`，最新记录在最右侧；只有 `-1` 是无效数据，其他数值均为有效记录。只有 1 个有效记录时，说明历史仍使用默认值，本次使用默认公式 `220 - age`；有 2 个或以上有效记录时，取最右侧前一个记录作为本次运动使用的 `MaxHR`。`SPT_S_SY` 或界面侧 `maxhr` 不参与此取值。
- `gomore_log_to_csv.py` 生成的样本 CSV 中 `SPEED` 按日志原值处理；当前工具链中该值为 km/h，可直接与 6 kph 比较。直接读取原始 FIT 时，需先确认其字段单位，不能混用两种口径。
- 原始 CSV/FIT 的逐点统计和 SDK 日志中的 `valueCount` 分开记录，不能相互替代。日志 `[GoMoRe][fit][valueCount]=A B` 中，第一个值 `A` 才是本次实际有效点数，第二个值 `B` 是本次运动评分；随后 `[GoMoRe][fitApp]=` 的评分是综合历史数据后的综合评分，需单独记录，不能把它们混为点数或 `MaxHR`。
- 评论中必须明确说明：根据 CSV/FIT 逐点计算出的 `HR > 70%HRR`、`SPEED > 6 kph`、同时满足时间超过 `720 s` 的条件，以及由此得到的同时达标点数，均属于 SDK 外部辅助判定；同时达标点数只能作为参考有效点数，不能宣称为 SDK 最终有效点数。实际有效点数必须以 SDK 日志 `[GoMoRe][fit][valueCount]` 的第一个值为准；若未解析到 `valueCount`，应明确写出“无法确认”，不能用外部统计补齐。
- 同时核对 GoMore 结果行中的 `[ltPace]=`、`[ltHr]=`、`Gomore output`、`guideResult` 以及处理状态。不要把 `SPT_S_SY _queryLtHr <value>` 的界面查询值当成日志结束 `ltHr`。

分析顺序：确认样本对应关系 → 读取 age/restHR → 读取 `[GoMoRe][EHisPd][maxHr]` 历史序列 → 仅 1 个记录时使用 `220-age`，否则取最右侧前一个记录作为本次 `MaxHR` → 计算外部辅助 HR 门槛 → 统计外部辅助 HR/速度/同时达标点（标为参考有效点数）→ 以 SDK 日志 `valueCount` 第一个值确认实际有效点数 → 分开核对本次评分、`fitApp` 综合历史评分和最终输出 → 再判断未出值原因。

结论边界：外部辅助条件满足及参考有效点数达到要求，不等于 SDK 一定出值；实际有效点数以 SDK 日志 `valueCount` 为准，还需结合 SDK 内部有效性、历史乳酸阈状态和实际处理结果。`SPT_S_SY` 中的运动心率区间 `maxhr` 不能替代 `EHisPd[maxHr]`。

## 输出

### Jira 附件
- 样本 `*.csv`。

### Jira 评论
结构化摘要（Issue/标题/样本数量/每个结果的 ltSpeed、pace、lh、mh、rows、userInfo），并在可获取到模型信息时于标题 `【乳酸阈值日志分析结果】` 后追加签名行 `（by <model-name> | reasoning=<effort>）`。
若未显式传参，脚本会依次尝试环境变量与 `~/.codex/config.toml`（`model`、`model_reasoning_effort`）；若仍无法解析则跳过签名行，不报错。

**数值分析（Feature 1）**：自动计算每个样本 CSV 的 maxHR（最大心率）和 avgSpeed（平均速度），并与算法输出进行以下辅助检查，异常项会以 ⚠️ 标注在评论中。该检查不能替代上面的出值条件分析：
- 乳酸阈心率 < maxHR × 0.9 → 阈心率偏低
- 乳酸阈心率 >= maxHR → 阈心率异常偏高
- 有氧阈上限(mh) < maxHR → 有氧上限未覆盖到最大心率
- 乳酸阈配速 < 平均速度 → 阈配速慢于平均速度

### 样本收录（Feature 2）
使用 `--collect-sample` 参数时，将样本 CSV 上传至飞书云盘 `GomoreSDK_Report/data_sample/JIRA_CASE/` 目录，按 `{issue_key}_{原始文件名}` 命名。自动创建不存在的目录层级。

### 深度 CSV 分析（Feature 3 — csv_deep_analyzer.py）

自动生成结构化 Markdown 报告，包含每公里分段、巡航vs冲刺段对比、HR-配速映射、乳酸阈合理性评估。

**用法：**

```bash
# 基本用法：自动分析 CSV
python3 csv_deep_analyzer.py gomore_data_20260506_112517.csv

# 提供用户年龄和设备LT输出（用于对比）
python3 csv_deep_analyzer.py gomore_data_20260506_112517.csv \
  --age 44 --lt-hr 160 --lt-pace-sec 301 --lt-mh 184

# 提供用户描述辅助分段
python3 csv_deep_analyzer.py data.csv --user-desc "最后2km提速"

# 指定输出文件
python3 csv_deep_analyzer.py data.csv --output ANALYSIS_REPORT.md
```

**参数说明：**
- `csv`：GoMore 样本 CSV 文件路径（必须，位置参数）
- `--age`：用户年龄（用于 HRmax = 220-age 估算，可选）
- `--lt-hr`：设备输出的乳酸阈心率 (bpm)，如 `160`
- `--lt-pace-sec`：设备输出的乳酸阈配速 (秒/km)，如 `301` 表示 5'01"/km
- `--lt-mh`：算法输出的最大心率 mh (bpm)，如 `195`
- `--user-desc`：用户反馈描述文本，用于辅助巡航/冲刺分段检测
- `--workout-type`：运动类型编码（默认从 CSV 读取）
- `-o/--output`：输出文件路径（默认输出到 stdout）

### ⚠️ 已知陷阱：csv_deep_analyzer.py 读取算法输出

`csv_deep_analyzer.py` **无法**从 CSV 文件中直接读取设备 GoMore 算法输出的乳酸阈值（LT HR、LT pace）。原因是：

- CSV 末尾三列（`EndOutput.pace0`、`EndOutput.pace1`、`EndOutput.hrEstorOut`）在很多情况下为 `-1.00`（算法结果未写入 CSV）
- 当未传 `--lt-hr` 参数时，脚本 fallback 逻辑为 `resolved_lt_hr = lt_hr or hr_max`，即**使用 CSV 中观测到的最大 HR 作为乳酸阈心率**，这通常偏高
- 真正的算法输出（`lh`、`ltSpeed`、`mh`）由 `gomore_log_to_csv.py` 从**原始日志**中通过独立解析路径提取

**解决方案**：
- 使用 `csv_deep_analyzer.py` 时**必须**从 `gomore_log_to_csv.py --json` 或 `run_lt_log_analysis.py` 的输出中获取真实的 `lh`/`ltSpeed` 值，通过 `--lt-hr` 和 `--lt-pace-sec` 传入
- 示例：`python3 csv_deep_analyzer.py data.csv --lt-hr 174 --lt-pace-sec 429`
- AI agent 在调用 `csv_deep_analyzer.py` 时，如果缺少 `--lt-hr` 参数，应主动从之前步骤的解析结果中获取，而非留空

### 深度 CSV 分析报告模板（Feature 3）

当 CSV 样本已经生成且需要进一步解读算法输出与用户实际运动表现之间的匹配度时，AI agent 应按以下模板生成 **结构化分析报告**，作为 Jira 评论的详细补充或独立附件（`*_ANALYSIS_REPORT.md`）。

> **触发条件**：Jira 用户反馈描述中包含"乳酸阈值不合理"、"配速不准"、"HR 和配速不匹配"等质疑算法输出的场景。

#### 分析流程

```python
# CSV 列索引（gomore_data_YYYYMMDD_HHMMSS.csv）
COL_HR = 15      # HR (bpm)
COL_SPEED = 16   # SPEED (km/h) — 注意：CSV 中速度单位为 km/h，非 m/s！
COL_USERINFO = 23 # userInfo 字符串（0-based index 23，第24列；注意不是 24，索引24 是 EndInput.workoutDate）
COL_TIMESTAMP = 0 # 时间戳 (Unix epoch, second)
```

#### 报告模板（精简大纲）

AI agent 按以下结构输出 Markdown 报告。具体数据由 `csv_deep_analyzer.py` 自动生成，agent 只需组织逻辑并补充判断。

```markdown
# {JIRA_KEY} 乳酸阈值 — CSV 数据分析报告

## 概述
运动类型/时间/距离/设备。

## CSV 数据特征
用户信息(userInfo)、数据质量(样本数/HR范围/平均)、SPEED单位确认(km/h)。

## 运动节奏分析
- 每公里分段(HR范围/平均+时间点+热身/高强度/节奏变化标注)
- 巡航段 vs 冲刺段(距离/HR均值峰值/配速/速度+差异)

## 心率-配速映射
10bpm步长[130-170]区间统计平均HR→配速。

## 乳酸阈值分析
- 设备LT值(HR/配速)+占HRmax比例(220-age)+占算法mh比例
- 实际运动数据对比(同等HR下配速/冲刺段配速)
- 偏差分析：LT合理性(85-92%HRmax)、配速偏保守/偏激进、可能原因(用户信息/算法滤波)

## 结论
整体评估 + 修正建议(如需要)。
```

### 数据处理要点

1. **SPEED 单位**：`gomore_log_to_csv.py` 生成的 CSV 中 SPEED 按 km/h 处理；配速计算使用 `pace_min_km = 60.0 / speed_kmh`。直接 FIT 的速度单位需单独确认。
2. **距离累积**：工具生成 CSV 按 km/h 处理，使用 `cum_dist += speed_kmh / 3600.0`（每行约 1 秒）。如果输入明确为 m/s，才使用 `speed_mps / 1000.0`。
3. **userInfo 字段**：CSV 第 24 列（0-based index **23**，注意不是 24），格式 `"{age}.0,{gender}.0,{height}.0,{weight}.0,-1.0,{hr_rest}.0,-1.0"`。各字段含义：
   - `age` — 年龄，用于 HRmax 估算（220 - age）
   - `gender` — 性别，1=男，0=女
   - `height` — 身高（cm）
   - `weight` — 体重（kg）
   - 第 5 位（-1.0）— 保留
   - `hr_rest` — 静息心率（bpm）
   - 第 7 位（-1.0）— 保留
   全为 `0` 表示用户信息缺失（算法将使用默认参数）。若不为零则可用于精确分析。
4. **默认 HRmax 公式**：`HRmax = 220 - age`。对 `[GoMoRe][EHisPd][maxHr]` 只有一个记录的情况，本次运动使用该默认值。
5. **乳酸阈典型范围**：最大心率的 85-92%。
6. **分段分析**：巡航段 vs 冲刺段的分界点根据用户反馈（如"最后 2km 提速"）或 HR 曲线突变点确定。

### 批量报告

`*.md`，逐 issue 汇总本次处理结果与评论内容。批量模式下每个 issue 的深度分析应作为子章节嵌入批量报告。
