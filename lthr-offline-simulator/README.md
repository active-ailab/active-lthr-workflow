# lthr-offline-simulator

LTHR 离线仿真工作流，运行在真机设备上。

这个 skill 聚焦 LTHR / GoMore 业务语义，本身不重复实现底层设备能力；设备串口、挂盘、文件系统操作、ready/awake、bridge 诊断等由 `watchlink-v3` 提供。

## 适用场景

- 单样本离线 CSV 真机仿真
- Jira 附件样本回放
- `data_sample/` 多样本批跑
- 只跑 UART `utest`
- IMG 日志提取
- 结果写出为 Markdown / JSON / Jira 评论
- 乳酸阈引导测试目标心率区间 / 百分比计算

## 边界说明

### 本 skill 负责

- `data_sample/` 业务路径
- GoMore CSV 选择、部署和校验
- 仿真流程编排
- Jira 附件选择与回传
- 结果总结、趋势分析、Markdown 产物
- IMG 日志提取后的业务级汇总

### watchlink-v3 负责

- 串口命令路由
- VBUS / ready / awake
- data / log 挂盘与卸盘
- 设备文件系统基础操作
- WSL / Windows bridge 与 runtime diagnose

## 目录结构

```text
lthr-offline-simulator/
├── SKILL.md
├── README.md
├── assets/
├── references/
└── scripts/
    ├── run_lthr_offline_sim.py
    ├── run_lthr_batch.py
    ├── run_device_multi_sim.py
    ├── run_utest_only.py
    ├── img_log_extractor.py
    ├── shared/
    └── tests/
```

## 主要入口

- `scripts/run_lthr_offline_sim.py`
- `scripts/run_lthr_batch.py`
- `scripts/run_device_multi_sim.py`
- `scripts/run_utest_only.py`
- `scripts/img_log_extractor.py`

## 使用示例

### 1. 单样本仿真

```bash
python3 scripts/run_lthr_offline_sim.py MOD-9545 --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_offline_sim.py MOD-9545 --csv-path /tmp/sample.csv --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_offline_sim.py MOD-9545 --csv-path /tmp/sample.csv --skip-jira --deploy-only --mgr-port COM37
```

### 2. 批量 Jira 仿真

```bash
python3 scripts/run_lthr_batch.py --issues MOD-7923 MOD-7924 --mgr-port COM37 --uart-port COM36
python3 scripts/run_lthr_batch.py --issues-file ./batch.txt --continue-on-error --mgr-port COM37 --uart-port COM36
```

### 3. 多样本仿真

```bash
python3 scripts/run_device_multi_sim.py --mgr-port COM37 --uart-port COM36
python3 scripts/run_device_multi_sim.py --data-dir /path/to/data_sample --with-img-extract --mgr-port COM37 --uart-port COM36
python3 scripts/run_device_multi_sim.py --img-only
```

### 4. 只跑 UART utest

```bash
python3 scripts/run_utest_only.py --uart-port COM36
```

### 5. 仅提取 IMG 日志

```bash
python3 scripts/img_log_extractor.py --mgr-port COM37
python3 scripts/img_log_extractor.py --skip-mount --mount-root /mnt/f/IMAGE
```

## shared/ 重构结果

当前大部分可复用编排逻辑已下沉到 `scripts/shared/`：

- `device_flow.py`
- `windows_mount_bridge.py`
- `offline_sim_runner.py`
- `multi_sim_runner.py`
- `multi_sim_support.py`
- `batch_runner.py`
- `jira_utils.py`
- `jira_reporting.py`
- `data_sample_ops.py`
- `workflow_reporter.py`
- `img_extract_support.py`

入口脚本现在更接近“编排器”，而不是大而全的单文件脚本。

## 依赖

### 必需依赖

- `Python 3.8+`
  - 用途：运行仿真入口、shared 模块和测试。
- `watchlink-v3`
  - 用途：提供端口路由、ready/awake、挂盘、设备文件系统操作、WSL bridge 和诊断能力。
  - 说明：这是本 skill 的设备能力前置，不建议绕过。

### 按场景需要

- `zepp jira`
  - 场景：从 Jira 下载 CSV / 附件，或把结果回传 Jira 时需要。
  - 如果只跑本地 `--csv-path`、`--skip-jira`、`--img-only` 等本地流程，可以不依赖 Jira 通信。

### 平台补充

- WSL + `watchlink-v3` sudo helper
  - 场景：需要对设备 data/log 盘做挂载读写时。
  - 说明：建议先安装 `watchlink-v3` 的 `install_watchlink_v3_sudo.sh`，否则需要依赖手工 `sudo -v`。

## 验证

```bash
cd scripts/tests
python3 -m unittest discover -s . -v
```

## 说明

- `assets/` 放模板
- `references/` 放输出结构、错误码、引导测试心率区间等专项说明
- `README.md` 面向 GitHub / 团队成员
- `SKILL.md` 面向 agent 路由与执行提示

## 专项参考

- 引导测试目标心率区间计算：`references/guide_test_hr_ranges.md`
