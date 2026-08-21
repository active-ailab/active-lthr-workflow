# active-lthr-workflow

## 项目简介

`active-lthr-workflow` 是一个面向 LTHR（乳酸阈）相关研发、验证和问题分析的工作流集合仓库。当前仓库不是单一脚本，而是由三个相互衔接的子工作流组成：

- `lthr-jira-analyzer`：从 Jira 附件或 feedback 日志中提取可分析内容，生成 CSV、回传附件并写评论。
- `lthr-offline-simulator`：在真机设备上执行单样本、多样本和批量 Jira 离线仿真。
- `lthr-multi-sample-report`：把仿真结果进一步整理成对比报告、Jira baseline 报告、飞书文档和群通知。

因此，这个仓库的核心价值是把“日志分析 → 仿真 → 报告发布”串成一条可复用链路，而不是只解决单一步骤。

## 核心功能

- **Jira 日志分析**：下载 `txt/log/zip` 附件，必要时调用 `zepp feedback download`，递归解压并生成 GoMore CSV。
- **真机离线仿真**：支持单样本 CSV 仿真、批量 Jira 仿真、多样本 `data_sample/` 仿真，以及只跑 UART `utest`。
- **多样本报告生成**：输出 Garmin / DUT / SDK 三源对比、单版本仿真报告、新旧 SDK 对比和 Jira baseline 对比报告。
- **飞书发布闭环**：通过 `lark-cli` 发布文档、归档 Base、发送群通知。
- **结构化输出**：统一产出 Markdown、JSON、Jira 评论草稿和批量汇总报告。

## 适用场景

- 分析一个或多个 LTHR 相关 Jira issue 的日志与附件。
- 在真机上复现 LTHR / GoMore 样本行为，验证算法或流程。
- 对多个样本进行批量仿真后，汇总生成可共享的对比报告。
- 把分析或仿真结果同步发布到飞书文档、Jira 评论和群消息。

## 目录结构

```text
active-lthr-workflow/
├── lthr-jira-analyzer/          # Jira 附件日志分析与回传
├── lthr-offline-simulator/      # 真机离线仿真与批跑
├── lthr-multi-sample-report/    # 多样本报告生成与飞书发布
└── .github/workflows/feishu-notify.yml
```

## 使用方法

### 1. 分析单个或多个 Jira 日志

```bash
python3 lthr-jira-analyzer/scripts/run_lt_log_analysis.py ITS-51120
python3 lthr-jira-analyzer/scripts/run_lt_log_analysis.py --issue-file lthr-jira-analyzer/scripts/issues.txt
```

### 2. 跑单样本或批量离线仿真

```bash
python3 lthr-offline-simulator/scripts/run_lthr_offline_sim.py MOD-9545 --mgr-port COM37 --uart-port COM36
python3 lthr-offline-simulator/scripts/run_lthr_batch.py --issues MOD-7923 MOD-7924 --mgr-port COM37 --uart-port COM36
python3 lthr-offline-simulator/scripts/run_device_multi_sim.py --mgr-port COM37 --uart-port COM36
```

### 3. 生成多样本报告并发布

```bash
python3 lthr-multi-sample-report/scripts/run_pipeline.py --offline-summary /tmp/lthr_multi_sim/device_multi_sim_20260512_101530.json
```

### 4. 验证

```bash
cd lthr-offline-simulator/scripts/tests && python3 -m unittest discover -s . -v
cd ../../lthr-multi-sample-report/scripts/tests && python3 -m unittest discover -s . -v
```

## 依赖关系

- `Python 3.8+`：三个子工作流的共同运行时。
- `zepp`：Jira 信息、附件、评论和 baseline 相关流程需要。
- `lark-cli`：飞书 Base / 文档 / 群通知发布需要。
- `watchlink-v3`：`lthr-offline-simulator` 的设备串口、挂盘、文件系统和 bridge 依赖。

## 注意事项

- 顶层仓库目前**不是**统一 CLI，而是三个子工作流的合集；执行时请进入对应子目录使用其脚本。
- `lthr-offline-simulator` 聚焦业务仿真，不重复实现设备底层能力；设备控制由 `watchlink-v3` 提供。
- 涉及 Jira 或飞书回写时，需要本机已经具备相应 CLI 权限。
- 如果只看仓库名容易误判为单一 LTHR 工具，实际它是“日志分析 + 仿真 + 报告发布”的工作流套件。
