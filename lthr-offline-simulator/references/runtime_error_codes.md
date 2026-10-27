# Runtime Error Codes

这些错误码由共享 helper 直接根据错误文本归类，并通过 `[HINT][E_*]` 形式输出给脚本、stderr 或 JSON 摘要。目标是让排障动作落在代码里，而不是在多个 skill 文档里重复写长篇 checklist。

## 当前错误码

- `E_WSL_BRIDGE`: WSL 到 Windows 的 interop 失败或落入 UNC cwd 问题。
- `E_SERIAL_PORT_DENIED`: COM 口被外部 Windows 串口工具占用。
- `E_SERIAL_PORT_CLOSED`: MGR 通道关闭、掉线或部署阶段串口失效。
- `E_DATA_ROOT_DETECT`: Windows data-root 未出现、出现多个候选，或 WSL 仍落回 legacy 挂载路径。
- `E_UART_PORT_PROBE`: UART 候选口探测失败，当前端口对不可信。
- `E_UTEST_START_MISSING`: 在观察窗口内没有看到 `GoMore Offline CSV Simulation Start` 或样本进度，通常意味着命令未启动或 UART 未接通。
- `E_UTEST_TIMEOUT`: utest 启动后未在预期窗口完成。
- `E_DEVICE_READY_TIMEOUT`: deploy 后设备没有恢复到 launcher-ready 或 shell-ready。
- `E_IMG_DISK_NOT_FOUND`: IMG 日志盘未被识别到。
- `E_IMG_DISK_TIMEOUT`: IMG 日志盘在等待窗口内没有出现。
- `E_LOG_FILE_NOT_FOUND`: 本地传入的日志文件路径不存在。

## 使用规则

- 共享 Python helper 负责定义和维护错误码，不要在多个脚本里各自拼不同命名。
- skill 文档只保留“优先看 runtime hint / error code”的原则，不再逐条复制每个错误码的完整恢复流程。
- 如果新增错误码，优先先改 helper 和本文件，再看是否需要在某个脚本的 JSON 摘要里补充字段。
