# lthr-multi-sample-report

LTHR 多样本报告编排与发布工作流。

这个 skill 负责把仿真结果进一步整理成：

- Garmin / DUT / SDK 三源对比报告
- 单版本仿真报告
- 新旧 SDK 对比报告
- JIRA baseline 对比报告
- 飞书文档发布结果
- 群通知与归档产物

## 适用场景

- 生成仿真报告
- 做 SDK 版本对比
- 做 Garmin / DUT / SDK 三源对比
- 发布飞书文档
- 归档 Base / 报告 / 日志
- 从 `lthr-offline-simulator` 的多样本 summary 直接继续出报告

## 目录结构

```text
lthr-multi-sample-report/
├── SKILL.md
├── README.md
└── scripts/
    ├── run_pipeline.py
    ├── base_steps.py
    ├── jira_steps.py
    ├── _pipeline_common.py
    ├── _report_utils.py
    ├── parse_gomore_samples.py
    ├── generate_sim_report.py
    ├── compare_base_samples.py
    ├── fetch_jira_baseline.py
    ├── generate_jira_report.py
    ├── generate_garmin_dut_compare_report.py
    └── tests/
```

## 主要入口

```bash
python3 scripts/run_pipeline.py --help
```

## 使用示例

### 1. 一键完整流程

```bash
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log \
  --base-token <token> --table-id <id> \
  --base-template <源token> --gomore-folder <文件夹token> \
  --chat-id oc_xxxx
```

### 2. 只跑部分步骤

```bash
python3 scripts/run_pipeline.py --sdk-ver 1.0.15.0 --json data.json --steps 4-6
```

### 3. 直接消费多样本 summary

```bash
python3 scripts/run_pipeline.py \
  --offline-summary /tmp/lthr_multi_sim/device_multi_sim_20260512_101530.json
```

### 4. JIRA CASE 流程

```bash
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log \
  --sdk-ver 1.0.16.0 \
  --jira-issues COLOGNE-3107 MAT-13034 MOD-7798 WIN-1957 \
  --steps 11-14
```

### 5. BASE CASE + JIRA CASE 混合流程

```bash
python3 scripts/run_pipeline.py \
  --log-file /path/to/lyon_xxx.log \
  --sdk-ver 1.0.16.0 \
  --base-token X --table-id Y \
  --jira-issues COLOGNE-3107 MAT-13034 MOD-7798 WIN-1957 \
  --steps 1-14
```

## 管线拆分

近几轮重构后，主要结构是：

- `run_pipeline.py`：顶层入口和参数编排
- `base_steps.py`：BASE_CASE 1-10 步
- `jira_steps.py`：JIRA_CASE 11-14 步
- `_pipeline_common.py`：公共 helper
- `_report_utils.py`：报告域 helper

这让主入口更薄，也更适合后续 GitHub 维护。

## 输入输出

常见输入：

- raw multi-sample log
- `device_multi_sim_<stamp>.json`
- 已解析的 `samples_*.json`

常见输出：

- `garmin_dut_sim_compare_report_*.md`
- `sdk_*_sim_report.md`
- `sdk_*_vs_*_compare_report.md`
- `jira_compare_report_<sdk_ver>.md`

## 依赖

### 必需依赖

- `Python 3.8+`
  - 用途：运行 pipeline、解析脚本和测试。
- `lark-cli`
  - 用途：飞书 Base / 文档 / 归档 / 消息发布。
  - 说明：如果只做纯本地解析，不发布飞书，部分流程可以跳过，但完整 pipeline 默认依赖它。
- `zepp`
  - 用途：JIRA CASE 路径中的 issue 信息、评论和基准值获取。

### 上游输入依赖

- `lthr-offline-simulator` 输出的 raw log 或 `device_multi_sim_<stamp>.json`
  - 用途：作为本 skill 最常见的输入来源。
  - 说明：虽然也能直接吃手工准备的日志或 JSON，但团队主路径建议与 offline-simulator 串联使用。

## 验证

```bash
cd scripts/tests
python3 -m unittest discover -s . -v
python3 scripts/run_pipeline.py --help
```

## 说明

- `README.md` 面向 GitHub / 团队成员
- `SKILL.md` 面向 agent 路由与触发词
- 这层主要是“报告和发布”，不是“设备控制”
