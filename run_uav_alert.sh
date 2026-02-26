#!/bin/bash
# run_uav_alert.sh
# ================================================================
# UAV 告警系統啟動腳本
# 在 uav-fire-detector Docker 容器內執行：
#   - 雙 D435 相機（ROS2 topic 或 realsense fallback）
#   - SAM3 GPU 推論（fire/smoke/person）
#   - MAVLink GPS 讀取（/dev/ttyACM1）
#   - 事件式回傳 → Hub 筆電（10.0.0.7:8080）
#
# 使用方式：
#   chmod +x run_uav_alert.sh
#
#   # 假相機測試（不需 D435/飛控，先確認 SAM3+POST 通）
#   ./run_uav_alert.sh --fake-cam
#
#   # 實際執行
#   ./run_uav_alert.sh
#
#   # 指定 Hub token
#   ./run_uav_alert.sh --token YOUR_REAL_TOKEN
# ================================================================

set -e

# ── 路徑設定 ──
UAV_HUB=/home/alan/xin/uav_hub
SAM3_ROOT=/home/alan/xin/sam3
MAVLINK_DEV=/dev/ttyACM1       # 飛控串口
IMAGE=uav-fire-detector:latest

# ── 把 CLI 參數全部透傳給 Python ──
EXTRA_ARGS="$@"

# 容器內沒有 ROS2，改用 pyrealsense2 直讀 D435
EXTRA_ARGS="$EXTRA_ARGS --no-ros2"

echo "======================================================"
echo "  UAV Alert System — Docker Launch"
echo "  Image  : $IMAGE"
echo "  Hub    : http://10.0.0.7:8080/api/v1/alerts"
echo "  SAM3   : $SAM3_ROOT"
echo "  MAVLink: $MAVLINK_DEV"
echo "  Args   : $EXTRA_ARGS"
echo "======================================================"

# 確認 GPU 可用
docker run --rm --runtime=nvidia $IMAGE \
  python3 -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'" \
  2>/dev/null && echo "[OK] GPU 可用" || { echo "[ERR] GPU 不可用，中止"; exit 1; }

# 啟動主程式
SERIAL_DEVICES=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | awk '{print "--device=" $0 ":" $0}' | tr '\n' ' ')

docker run -it --rm \
  --runtime=nvidia \
  --network=host \
  --privileged \
  $SERIAL_DEVICES \
  -v $UAV_HUB:/workspace/uav_hub \
  -v $SAM3_ROOT:/workspace/sam3 \
  -v $UAV_HUB/decord.py:/workspace/sam3/decord.py \
  -v $UAV_HUB/queue:/workspace/uav_hub/queue \
  -v /dev:/dev \
  -e PYTHONPATH=/workspace/sam3:/workspace/uav_hub \
  -e CUDA_VISIBLE_DEVICES=0 \
  -e HF_TOKEN="${HF_TOKEN:-YOUR_HF_TOKEN_HERE}" \
  -w /workspace/uav_hub \
  $IMAGE \
  bash -c "pip install pymavlink pyserial -q && python3 thor_dualcam_event_sender.py \
    --sam3-root /workspace/sam3 \
    --hub-url http://10.0.0.7:8080/api/v1/alerts \
    --token '${TOKEN:-CHANGE_ME_TO_A_LONG_RANDOM_TOKEN}' \
    --mavlink $MAVLINK_DEV \
    --baud 115200 \
    $EXTRA_ARGS"
