# 乳酸阈引导测试目标心率区间计算

## 适用场景

- 用户给出 `maxHr` 和 `restHr`，需要快速计算 WARMUP / STAB1 / STAB2 / STAB3 / STABALL / SPRINT / RELAX 阶段的目标心率。
- 排查“引导测试百分比是否改对了”“当前阶段目标心率是多少”“为什么日志里的阈值是这个数”。
- 对照 Lyon app 侧 `packages/apps/sports/src/manager/sport_lthrTest_manager.c` 的实现逻辑。

## 输入来源

- `maxHr`
  - GoMore 打开时：`sportGomoreService_getGuideTstMaxHr()`
  - 非 GoMore 路径：`sportSetting_getSettingManager()->hrZone->getMaxHr()`
- `restHr`
  - `HealthHrSportGetRestHrValue()`

## 计算公式

先算心率储备：

```text
reserve = maxHr - restHr
```

再按阶段百分比计算目标值：

```text
target = restHr + ratio * reserve
```

实现注意点：

- `lthr` 数组元素类型是 `uint8_t`，带小数的结果会直接截断，不做四舍五入。
- `SPRINT` 只设置下限，`lthr[1] = 0xff` 表示上限开放。
- `RELAX` 只设置 `lthr[1]`，不写低值。
- 如果在排查 STABALL / SPRINT / RELAX 没生效的问题，除了看 `switch` 分支，还要同时检查调用入口对 `queryId` 上界的前置校验。

## 当前阶段百分比

| 阶段 | 低值比例 | 高值比例 | 说明 |
|------|----------|----------|------|
| `SPORT_LTHR_TEST_WARMUP` | 50% | 60% | 热身阶段 |
| `SPORT_LTHR_TEST_STAB1` | 60% | 70% | 稳定段 1 |
| `SPORT_LTHR_TEST_STAB2` | 70% | 80% | 稳定段 2 |
| `SPORT_LTHR_TEST_STAB3` | 80% | 90% | 稳定段 3 |
| `SPORT_LTHR_TEST_STABALL` | 60% | 90% | 稳定段整体区间 |
| `SPORT_LTHR_TEST_SPRINT` | 90% | `0xff` | 冲刺阶段，上限开放 |
| `SPORT_LTHR_TEST_RELAX` | 不写 | 60% | 放松阶段，仅上限 |

## 速算模板

```text
WARMUP  = [restHr + 0.50 * reserve, restHr + 0.60 * reserve]
STAB1   = [restHr + 0.60 * reserve, restHr + 0.70 * reserve]
STAB2   = [restHr + 0.70 * reserve, restHr + 0.80 * reserve]
STAB3   = [restHr + 0.80 * reserve, restHr + 0.90 * reserve]
STABALL = [restHr + 0.60 * reserve, restHr + 0.90 * reserve]
SPRINT  = [restHr + 0.90 * reserve, 0xff]
RELAX   = [未设置, restHr + 0.60 * reserve]
```

## 示例：`maxHr = 188`，`restHr = 60`

```text
reserve = 188 - 60 = 128
```

| 阶段 | 理论结果 | 实际写入 `uint8_t` 后 |
|------|----------|-----------------------|
| WARMUP | `124` / `136.8` | `124` / `136` |
| STAB1 | `136.8` / `149.6` | `136` / `149` |
| STAB2 | `149.6` / `162.4` | `149` / `162` |
| STAB3 | `162.4` / `175.2` | `162` / `175` |
| STABALL | `136.8` / `175.2` | `136` / `175` |
| SPRINT | `175.2` / `0xff` | `175` / `255` |
| RELAX | `-` / `136.8` | `-` / `136` |

## 快速回答模板

当用户只给 `maxHr` 和 `restHr` 时，可以直接按下面格式答：

```text
先算 reserve = maxHr - restHr。

WARMUP: low / high
STAB1: low / high
STAB2: low / high
STAB3: low / high
STABALL: low / high
SPRINT: low / 255
RELAX: high only

注意：代码实际写入 uint8_t，小数会直接截断。
```