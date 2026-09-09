# lthr-jira-analyzer

LTHR Jira 日志分析与回传工具。

这个 skill 适合处理“一个或多个 Jira issue 的日志分析、样本生成、附件回传、评论总结”这一类场景。

核心能力：

- 从 Jira 附件下载 `txt/log/zip`
- 必要时调用 `zepp feedback download`
- 自动解压并递归查找日志
- 调用 `gomore_log_to_csv.py` 生成样本 CSV
- 上传 CSV / 日志 / 图片回 Jira
- 写 Jira 评论摘要
- 批量模式生成汇总 Markdown 报告

## 目录结构

```text
lthr-jira-analyzer/
├── SKILL.md
├── README.md
└── scripts/
    ├── run_lt_log_analysis.py
    ├── gomore_log_to_csv.py
    ├── csv_deep_analyzer.py
    ├── issues.txt
    └── run.bat
```

## 主要脚本

- `scripts/run_lt_log_analysis.py`：主入口
- `scripts/gomore_log_to_csv.py`：日志转 GoMore CSV
- `scripts/csv_deep_analyzer.py`：CSV 深度分析

## 使用示例

### 1. 单个 Jira 问题分析

```bash
python3 scripts/run_lt_log_analysis.py ITS-51120
```

### 2. 批量分析多个 Jira

```bash
python3 scripts/run_lt_log_analysis.py ITS-51120 ITS-50805 ITS-50755
```

### 3. 从 issue 文件批量分析

```bash
python3 scripts/run_lt_log_analysis.py --issue-file scripts/issues.txt
```

### 4. 指定批量报告输出路径

```bash
python3 scripts/run_lt_log_analysis.py \
  --issue-file scripts/issues.txt \
  --report /tmp/topic_ota/lthr_batch_report.md
```

### 5. 指定本地日志文件，仅分析单 issue

```bash
python3 scripts/run_lt_log_analysis.py ITS-51120 --txt-log /tmp/sample.log
```

### 6. 只上传 CSV，不写 Jira 评论

```bash
python3 scripts/run_lt_log_analysis.py ITS-51120 --no-comment
```

### 7. 收录样本到 JIRA_CASE 目录

```bash
python3 scripts/run_lt_log_analysis.py ITS-51120 --collect-sample
```

### 8. Windows 示例

```powershell
python .\scripts\run_lt_log_analysis.py ITS-51120
python .\scripts\run_lt_log_analysis.py --issue-file .\scripts\issues.txt
```

## 标准流程

1. 读取 Jira 元信息与附件列表
2. 优先尝试 Jira 附件中的 `*.txt` / `*.log`
3. 若无可解析文本日志，再尝试 `*.zip`
4. 若仍无可解析结果，再调用 `zepp feedback download`
5. 自动解压 zip 和嵌套 zip
6. 选择合适日志作为源文件
7. 调用 `gomore_log_to_csv.py --json` 生成样本 CSV
8. 上传 CSV、日志、图片到 Jira
9. 写 Jira 评论摘要
10. 批量模式输出汇总 Markdown 报告

## 基础出值分析

Jira 评论除了回传 CSV 和算法结果，还会按 GoMore 的基本出值条件做初步分析：

```text
HR > 70%HRR
SPEED > 6 kph
同时满足时间 > 720 s
```

其中：

```text
70%HRR = (MaxHR - RestHR) × 70% + RestHR
```

`[GoMoRe][EHisPd][maxHr]` 会记录最近最多 7 次 `maxHR`，最新记录在最右侧；只有 `-1` 是无效数据，其他数值均为有效记录。只有一个有效记录时，本次运动使用默认 `220 - age`；有两个或以上有效记录时，取最右侧前一个记录作为本次运动使用的 `MaxHR`。`SPT_S_SY` 或界面侧 `maxhr` 不参与此取值。`gomore_log_to_csv.py` 生成的 CSV 中 SPEED 按 km/h 处理，可直接与 6 kph 比较；直接读取 FIT 时需先确认单位。

分析结果会分别记录：

- HR 有效点、HR 达标点
- 速度有效点、速度达标点
- HR 与速度同时达标点
- SDK 日志中的 `valueCount`
- `ltPace`、`ltHr`、`ltSpeed`、`lh`、`mh`、`guideResult`

原始 CSV/FIT 的逐点统计不等于 SDK 内部 `valueCount`。即使 HR 和速度同时达标超过 720 秒，也只能说明满足基础条件，仍需结合 SDK 内部有效性、历史状态和最终输出判断是否应出值。

## 子脚本示例

### 日志转 CSV

```bash
python3 scripts/gomore_log_to_csv.py /path/to/log.log
python3 scripts/gomore_log_to_csv.py /path/to/log.log /tmp/
python3 scripts/gomore_log_to_csv.py /path/to/log.log /tmp/my.csv
```

### CSV 深度分析

```bash
python3 scripts/csv_deep_analyzer.py gomore_data_20260506_112517.csv
python3 scripts/csv_deep_analyzer.py gomore_data_20260506_112517.csv --age 44 --lt-hr 160 --lt-pace-sec 301
python3 scripts/csv_deep_analyzer.py gomore_data_20260506_112517.csv --output ANALYSIS_REPORT.md
```

## 依赖

### 必需依赖

- `Python 3.8+`
  - 用途：运行主分析脚本、日志转 CSV 脚本和深度分析脚本。
- `zepp`
  - 用途：读取 Jira 元信息、列附件、下载附件、上传 CSV / 日志 / 图片、写 Jira 评论。
- Jira 附件访问权限
  - 用途：没有附件读写权限时，脚本无法完成“下载 -> 解析 -> 回传”闭环。

### 按场景需要

- `zepp feedback download`
  - 场景：Jira 附件本身没有可解析 `txt/log/zip`，需要回退到 feedback 下载时。
  - 说明：这是兜底路径，不是每次都必走。

## 输出

- Jira 附件中的样本 `*.csv`
- Jira 评论中的结构化分析摘要
- 批量模式下的汇总 Markdown 报告
- 可选的深度 CSV 分析报告

## 说明

- `README.md` 面向 GitHub / 团队成员
- `SKILL.md` 面向 agent 路由与触发词
- 这个 skill 聚焦“日志分析并回传 Jira”，不负责真机仿真编排
