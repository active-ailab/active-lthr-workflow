"""
_pipeline_config.py - Pipeline 默认凭证配置

从此前的对话记录中提取的实际值已填入下方。当 CLI 未指定对应参数时，
run_pipeline.py 会自动使用这些默认值，避免每次重复输入。

如需更新，直接修改此文件即可。
"""

# ── 飞书 Base ──
BASE_TOKEN = "TZGZbPQ7EaI5EasOyEDc1QZtnFi"          # 乳酸阈多样本仿真测试结果 Base
TABLE_ID = "tblmhuNaKc9OLYAz"                         # 数据表 ID
BASE_TEMPLATE = "TZGZbPQ7EaI5EasOyEDc1QZtnFi"         # Base 模板（与上同，用于复制）
BASE_URL = "https://zepp.feishu.cn/base/TZGZbPQ7EaI5EasOyEDc1QZtnFi"  # Base 链接（报告中引用）

# ── 飞书发布与通知 ──
GOMORE_FOLDER = "V6ICfsqKnl1ojrdjli0caYlDnpb"       # GomoreSDK_Report 文件夹 token，用于步骤9归档
CHAT_ID = "oc_1ea73f4f7b8263de4346787f9640ada4"     # 飞书群 ID，用于步骤10发送群通知

# ── data_sample 云盘来源 ──
DATA_SAMPLE_FOLDER = "HYS7fstGll8lGedAVKFc6RNxnAb"   # 飞书云盘 data_sample 文件夹 token，多样本仿真时优先从此拉取

# ── 默认输出目录 ──
# 传 --offline-summary 时会自动跟随 summary 目录，无需修改。
OUTPUT_DIR = None    # None 表示使用 DATA_DIR（见 _pipeline_common.py）
