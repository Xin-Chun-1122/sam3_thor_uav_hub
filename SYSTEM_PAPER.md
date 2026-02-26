# 無人機雙目視覺事件式告警回傳系統
## UAV Dual-Camera Event-Driven Alert Transmission System

> **版本**：v2.0（論文版）  
> **日期**：2026-02-25  
> **平台**：NVIDIA Jetson AGX Thor · Intel RealSense D435 × 2 · ArduPilot MAVLink  
> **關鍵詞**：UAV, Event-Driven, SAM3, Text-Grounding, Zero-shot Detection, WireGuard VPN

---

## 目錄

1. [研究背景與動機](#1-研究背景與動機)
2. [相關工作](#2-相關工作)
3. [系統架構總覽](#3-系統架構總覽)
4. [硬體規格](#4-硬體規格)
5. [軟體堆疊](#5-軟體堆疊)
6. [偵測子系統：Meta SAM3 Text-Grounding](#6-偵測子系統meta-sam3-text-grounding)
7. [事件狀態機（Event State Machine）](#7-事件狀態機event-state-machine)
8. [雙相機採集與影像融合](#8-雙相機採集與影像融合)
9. [GPS 定位機制與 MAVLink 整合](#9-gps-定位機制與-mavlink-整合)
10. [告警傳輸協定](#10-告警傳輸協定)
11. [Hub 地面站伺服器](#11-hub-地面站伺服器)
12. [離線容錯機制](#12-離線容錯機制)
13. [頻寬分析與可行性評估](#13-頻寬分析與可行性評估)
14. [實測結果與驗證](#14-實測結果與驗證)
15. [已知問題與解決過程](#15-已知問題與解決過程)
16. [優化方向與未來工作](#16-優化方向與未來工作)
17. [程式碼結構說明](#17-程式碼結構說明)
18. [快速啟動指南](#18-快速啟動指南)
19. [參考資料](#19-參考資料)

---

## 1. 研究背景與動機

### 1.1 問題陳述

無人機（UAV）搭載視覺感知系統，廣泛應用於山地搜救、火災監測與邊境巡邏等任務。在山區或偏遠地區執行任務時，通訊鏈路受到嚴重限制：

- **4G LTE 山區上行頻寬**：通常僅有 200 Kbps–2 Mbps
- **傳統 720p@15fps H.264 影像串流需求**：1,000–2,000 Kbps（常超出可用頻寬）
- **衛星通訊（Iridium/Thuraya）**：64–128 Kbps，費用高昂，無法承受連續串流

UAV 機載電力預算估算：

| 作業模式 | 估計功耗 |
|----------|----------|
| Jetson AGX Thor 推論 (~50W) + 攝影機 + 飛控 | ~120 W |
| UAV 飛行（電動旋翼 ~2kg 等級） | 400–600 W |
| 任務總功耗 | ~520–720 W |

在典型 6,000 mAh 6S LiPo（~133 Wh）電池下，持續串流的高功耗顯然不合適。

### 1.2 研究目標

1. **低頻寬條件下可靠傳輸**：僅在偵測到有意義事件時傳輸，平均頻寬需求降至 < 300 Kbps
2. **零樣本偵測器**：使用基於自然語言 Prompt 的 Foundation Model，無需針對特定場景重新訓練
3. **假陽性抑制**：透過時序狀態機過濾瞬間誤偵測，確保傳輸前事件已被多幀驗證
4. **完整端到端系統**：從 UAV 雙相機採集、GPU 推論、飛控 GPS，到地面站 Dashboard，覆蓋完整鏈路

### 1.3 研究貢獻

1. **提出 FSM-Gated 事件式傳輸架構**：5 狀態有限狀態機（FSM）作為傳輸閘門，在山區低頻寬環境中實現高可靠性事件傳輸
2. **Meta SAM3 零樣本部署**：在 NVIDIA Jetson AGX Thor（sm_110 架構）上驗證 SAM3 Text-Grounding 推論，解決 BFloat16 精度衝突問題
3. **雙目視覺融合設計**：同步採集兩台 Intel RealSense D435 的 BGR 流並拼接，擴大 FOV 覆蓋範圍
4. **完整系統驗證**：在含 RealSense D435 × 2 + ArduPilot 飛控的實際硬體上完成端到端驗證

---

## 2. 相關工作

### 2.1 UAV 火災偵測

| 研究 | 方法 | 限制 |
|------|------|------|
| Shen et al. (2022) | YOLOv5 + 遠端串流 | 需持續高頻寬（>1Mbps） |
| Li et al. (2023) | Edge inference + JPEG 傳輸 | 固定頻率傳輸，無事件過濾 |
| Barmpoutis et al. (2020) | 多光譜相機融合 | 硬體成本高，無法零樣本 |

**本研究差異**：採用事件式傳輸閘控（FSM），靜默期頻寬需求為 0，峰值需求 < 300 Kbps。

### 2.2 Foundation Model on Edge

SAM（Segment Anything Model，Kirillov et al. 2023）及其後繼 SAM2（Ravi et al. 2024）與 SAM3 展示了強大的零樣本分割能力。SAM3 引入 Text-Grounding 功能，允許使用文字描述定位目標。

**部署挑戰**：
- SAM3 模型大小 3.45 GB，需要 > 6 GB GPU 記憶體（FP16 推論）
- 原生 BFloat16 在 Jetson AGX Thor sm_110 架構上與 OpenCV 有型別相容問題（需明確指定 FP16）
- `set_image()` 特徵提取是計算瓶頸（~200–400ms），需複用以支援多標籤查詢

### 2.3 MAVLink 整合

MAVLink（Micro Air Vehicle Communication Protocol）是 ArduPilot 與 PX4 的標準通訊協定。本研究讀取 `GLOBAL_POSITION_INT` 與 `GPS_RAW_INT` 訊息，以毫秒級精度對齊偵測事件與 GPS 座標。

---

## 3. 系統架構總覽

```
┌─────────────────────────────────────────────────────────────────────────┐
│  無人機端 (Jetson AGX Thor)                                              │
│                                                                         │
│  D435_A ──┐                                                             │
│           ├──> pyrealsense2 ──> BGR Frame buffer                        │
│  D435_B ──┘         │                                                   │
│                      │                                                   │
│  飛控(ArduPilot) ──> MAVLink thread ──> GPS Cache                       │
│  /dev/ttyACM1               │                                           │
│                      │                                                   │
│                      ▼                                                   │
│              _main_loop_body() [2 Hz]                                   │
│                      │                                                   │
│              ┌───────┴───────┐                                          │
│              ▼               ▼                                          │
│          _infer_sam3_real()  fuse_frames()                              │
│          (FP16 autocast)     (1284×480)                                 │
│              │               │                                          │
│              ▼               │                                          │
│          EventStateMachine   │                                          │
│          per-label FSM       │                                          │
│              │               │                                          │
│              └───────┬───────┘                                          │
│                      │ should_send?                                     │
│                      ▼                                                   │
│              send_alert() ──┐                                           │
│              (meta+thumb   │  失敗 ──> save_to_queue()                  │
│               +evidence)   │                                            │
└────────────────────────────┼────────────────────────────────────────────┘
                             │
              WireGuard VPN  │  (10.0.0.20 → 10.0.0.7, ~230ms)
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Hub 地面站 (10.0.0.7:8080)                                             │
│                                                                         │
│  hub_server.py (Flask)                                                  │
│    POST /api/v1/alerts ──> meta.json + thumb.webp + evidence.webp       │
│    GET  / ──────────────> Web Dashboard (自動 10s 刷新)                 │
│                                                                         │
│  hub_events/<event_id>/                                                  │
│    meta.json / thumb.webp / evidence.webp                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 硬體規格

### 4.1 計算平台

| 項目 | 規格 |
|------|------|
| **平台** | NVIDIA Jetson AGX Thor Developer Kit |
| **GPU** | NVIDIA Thor GPU (72-core Ampere+, sm_110) |
| **CUDA 版本** | CUDA 13.0 |
| **PyTorch 版本** | 2.9.0+cu130（容器內） |
| **OS** | Ubuntu 22.04 + Jetson BSP |
| **RAM** | 64 GB LPDDR5（Unified Memory） |
| **儲存** | NVMe SSD ≥ 64 GB |

### 4.2 相機系統

| 項目 | 規格 |
|------|------|
| **型號** | Intel RealSense D435（× 2 台） |
| **序號 A** | `332522075298`（前向廣角） |
| **序號 B** | `332522073133`（側向/後向） |
| **解析度** | 640 × 480 像素 |
| **幀率** | 15 FPS |
| **串流類型** | RGB（BGR order）+ 深度流 |
| **介面** | USB 3.2 Gen 1 |
| **SDK** | Intel pyrealsense2 2.55.1 |

### 4.3 飛行控制器

| 項目 | 規格 |
|------|------|
| **飛控韌體** | ArduPilot（ArduCopter）|
| **通訊介面** | USB Serial → `/dev/ttyACM1` |
| **Baud Rate** | 115200 |
| **MAVLink System ID** | 1 |
| **GPS 訊息** | `GLOBAL_POSITION_INT`（lat/lon/alt × 1e−7）<br>`GPS_RAW_INT`（fix_type，0–6） |
| **GPS 精度** | 單頻 GNSS，3D Fix 時 ±3–5 m |

### 4.4 通訊網路

| 項目 | 規格 |
|------|------|
| **VPN 協定** | WireGuard |
| **Thor IP** | 10.0.0.20 |
| **Hub IP** | 10.0.0.7 |
| **RTT 延遲** | ~230 ms（山區 4G LTE） |
| **加密** | Curve25519 密鑰交換 + ChaCha20-Poly1305 |

---

## 5. 軟體堆疊

```
┌──────────────────────────────────────────────────────┐
│  Hub Server Layer                                    │
│  Flask 3.x · hub_server.py · Dashboard              │
├──────────────────────────────────────────────────────┤
│  Alert Transmission Layer                            │
│  requests 2.31+ · multipart/form-data · WebP         │
│  WireGuard VPN · end-to-end encryption               │
├──────────────────────────────────────────────────────┤
│  Deep Learning Inference Layer                       │
│  Meta SAM3 (facebook/sam3, 3.45GB)                   │
│  PyTorch 2.9.0+cu130 · CUDA 13 · sm_110 · FP16      │
├──────────────────────────────────────────────────────┤
│  Sensor & Communication Layer                        │
│  pyrealsense2 2.55.1 · pymavlink 2.4.49              │
│  OpenCV 4.x · NumPy                                  │
├──────────────────────────────────────────────────────┤
│  Container Runtime                                   │
│  Docker + NVIDIA Container Toolkit                   │
│  uav-fire-detector:latest (24.7 GB)                  │
│  Python 3.12 venv (/opt/venv)                        │
├──────────────────────────────────────────────────────┤
│  OS / BSP                                            │
│  Ubuntu 22.04 · Jetson BSP · ROS2 Jazzy              │
└──────────────────────────────────────────────────────┘
```

---

## 6. 偵測子系統：Meta SAM3 Text-Grounding

### 6.1 SAM3 模型說明

SAM3 是 Meta AI 發布的 Foundation Model，支援以文字描述作為 Prompt 進行零樣本目標偵測與分割。

**關鍵特性**：
- **Text-Grounding**：輸入文字標籤（如 `"fire"`），輸出對應目標的 BBox + Mask
- **模型大小**：3.45 GB（HuggingFace: `facebook/sam3`）
- **推論解析度**：1008 × 1008 px
- **輸出格式**：`boxes (N,4)`、`scores (N,)`、`masks (N,K,H,W)` tensor

### 6.2 初始化流程（`init_sam3()`）

```python
# 1. HuggingFace 登入（facebook/sam3 為 gated model）
from huggingface_hub import login
login(token=os.environ.get("HF_TOKEN", ""))

# 2. bpe vocab 路徑解析（容器掛載路徑）
bpe_path = os.path.join(SAM3_ROOT, "sam3", "assets",
                         "bpe_simple_vocab_16e6.txt.gz")

# 3. 模型建構
from sam3 import build_sam3_image_model
sam3_model = build_sam3_image_model(
    checkpoint="/workspace/sam3",
    bpe_path=bpe_path,
    device="cuda"
)

# 4. Processor 初始化
from sam3 import Sam3Processor
sam3_processor = Sam3Processor(bpe_path=bpe_path)
```

### 6.3 推論流程（`_infer_sam3_real()`）

```python
def _infer_sam3_real(bgr_frame: np.ndarray) -> list[Detection]:
    # Step 1: BGR → RGB → PIL
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    # Step 2: set_image() — 特徵提取，所有標籤共用（只呼叫一次）
    sam3_model.set_image(pil_img)

    detections = []
    for label in LABELS:  # ["fire", "smoke", "person"]
        # Step 3: FP16 autocast（只包住推論區塊，避免 BF16 污染 OpenCV）
        with torch.autocast("cuda", dtype=torch.float16):
            inputs = sam3_processor(images=pil_img, text=label)
            with torch.no_grad():
                outputs = sam3_model(**inputs)

        # Step 4: 後處理
        boxes  = outputs["boxes"].cpu().float().numpy()    # (N, 4) xyxy
        scores = outputs["scores"].cpu().float().numpy()   # (N,)
        masks  = outputs["masks"].cpu().float().numpy()    # (N, K, H, W)

        for i, (box, score) in enumerate(zip(boxes, scores)):
            if score < SCORE_THR:  # 0.45
                continue
            # 壓縮 mask 到 2D
            mask_i = masks[i]
            while mask_i.ndim > 2:
                mask_i = mask_i[0]
            # resize 回原圖尺寸
            mask_i = cv2.resize(mask_i, (W, H),
                                interpolation=cv2.INTER_NEAREST)
            mask_i = (mask_i > 0.5).astype(np.uint8)
            area = int(mask_i.sum())
            if area < MASK_AREA_THR:  # 200 px
                continue
            detections.append(Detection(label=label, score=float(score),
                                        bbox=box.tolist(), mask=mask_i))
    return detections
```

**關鍵設計決策**：

| 決策 | 原因 |
|------|------|
| `set_image()` 只呼叫 1 次 | 特徵提取是推論瓶頸（~200–400ms），所有標籤共用特徵節省 ~66% |
| `torch.float16` 明確指定 | sm_110 原生計算為 BFloat16，但 OpenCV/NumPy 不接受 BFloat16 tensor |
| `while mask_i.ndim > 2` | SAM3 masks 輸出形狀為 `(N, K, H, W)`，需逐層壓縮到 2D |
| `.cpu().float().numpy()` | 確保從 GPU FP16/BF16 tensor 正確轉換為 CPU float32 array |

### 6.4 推論效能

| 指標 | 數值 |
|------|------|
| 推論頻率 | 2 Hz（0.5 秒/次） |
| 每相機推論 | 1 次 `set_image()` + 3 次 `set_text_prompt()` |
| 等效吞吐量 | 4 幀/秒（雙相機合計） |
| 模型初始化時間 | ~52 秒（含 3.45GB 模型載入 + CUDA 編譯） |
| 信心分數門檻 | 0.45 |
| Mask 面積門檻 | 200 px（過濾細小雜訊） |

---

## 7. 事件狀態機（Event State Machine）

### 7.1 設計動機

單幀偵測容易產生假陽性（雲朵、光影、枝葉），直接傳輸會造成大量無效告警。FSM 要求目標在一段時間內多次被偵測到，才進入「確認（Confirmed）」狀態並觸發傳輸。

### 7.2 狀態定義

```
NORMAL ──(M1幀內N1次命中)──> SUSPECTED
SUSPECTED ──(M2幀內N2次命中)──> CONFIRMED ──> TRACKING
TRACKING ──(T_LOST秒無命中)──> LOST ──> NORMAL (重置)
SUSPECTED ──(視窗過期無命中)──> NORMAL
```

| 狀態 | 說明 | 傳輸動作 |
|------|------|----------|
| NORMAL | 正常巡航，無偵測 | 無 |
| SUSPECTED | 短窗口初步偵測 | 無 |
| CONFIRMED | 多窗口確認 | 觸發首次傳輸 |
| TRACKING | 持續追蹤中 | 每 T_SEND 秒傳輸 |
| LOST | 超過 T_LOST 秒未見目標 | 無（重置至 NORMAL） |

### 7.3 參數設定

| 參數 | 值 | 說明 |
|------|-----|------|
| `LOOP_INTERVAL` | 0.5 s | 推論間隔（2 Hz） |
| `SM_M1` | 5 幀 | 第一滑動視窗大小（2.5 秒） |
| `SM_N1` | 2 次 | 第一視窗觸發次數 |
| `SM_M2` | 10 幀 | 第二滑動視窗大小（5.0 秒） |
| `SM_N2` | 4 次 | 第二視窗觸發次數 |
| `SM_T_SEND` | 2.0 s | Tracking 狀態最短傳送間隔 |
| `SM_T_LOST` | 10.0 s | 進入 Lost 的無命中超時 |

### 7.4 首次告警延遲分析

在最理想情況下（每幀都命中）：
- 進入 Suspected：第 1 幀觸發（0.5s）
- 進入 Confirmed：約第 4 幀（2.0s）
- **首次傳輸實測**：約 **2.5–3.5 秒**後確認並觸發

在較稀疏的偵測情況下（每隔一幀命中）：
- **首次傳輸**：約 **5–8 秒**後確認

### 7.5 形式化定義

令 $h_t \in \{0,1\}$ 為時刻 $t$ 的偵測命中狀態，
$W_1(t) = \sum_{i=t-M_1+1}^{t} h_i$，
$W_2(t) = \sum_{i=t-M_2+1}^{t} h_i$。

狀態轉移條件：

$$\text{NORMAL} \xrightarrow{W_1(t) \geq N_1} \text{SUSPECTED} \xrightarrow{W_2(t) \geq N_2} \text{CONFIRMED}$$

傳輸條件（Tracking 狀態）：

$$\text{should\_send} = \text{True} \iff (t - t_{\text{last\_send}}) \geq T_{\text{SEND}}$$

---

## 8. 雙相機採集與影像融合

### 8.1 採集架構

系統使用 pyrealsense2 SDK 直接驅動兩台 D435，透過 USB 3 獨立連接：

```python
# 序號自動偵測
serials = [d.get_info(rs.camera_info.serial_number)
           for d in rs.context().devices]
# → ['332522075298', '332522073133']

# 每相機建立獨立 pipeline
for serial in serials:
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
    pipeline.start(config)
```

超時設定：每幀等待超時設為 3000 ms（初版 200 ms 在 GPU 負載高時會丟幀）。

### 8.2 影像拼接（`fuse_frames()`）

```
CAM-A (640×480)  |  CAM-B (640×480)
                 │
    [4px 灰色分隔線]
                 │
→ 拼接後：1284×480（含 4px 分隔線）
```

拼接流程：
1. 等比縮放兩幀至統一高度 480 px
2. 插入 4 px 灰色垂直分隔線
3. 在各相機區域繪製 SAM3 偵測框、Mask overlay、相機標籤

Mask 繪製：各偵測目標以半透明色塊疊加於原圖（`cv2.addWeighted`），並在 BBox 頂部標示 `label:score`。

### 8.3 Evidence BBox 跳過邏輯

當 `bbox_full = [0, 0, W, H]`（bbox 覆蓋影像 100%）時，`make_evidence()` 跳過重繪 BBox，避免在已有偵測框的畫面上再疊加全圖大框：

```python
img_w, img_h = pil.size
bx1, by1, bx2, by2 = bbox
w, h = bx2 - bx1, by2 - by1
skip_bbox = (w * h >= img_w * img_h * 0.85)  # 覆蓋 ≥ 85% 跳過
```

---

## 9. GPS 定位機制與 MAVLink 整合

### 9.1 背景執行緒設計

MAVLink 連線建立在獨立 daemon 執行緒中，不阻塞主推論迴圈：

```python
def _mav_reader_thread():
    while True:
        try:
            mav = mavutil.mavlink_connection(MAVLINK_DEV, baud=MAVLINK_BAUD)
            mav.wait_heartbeat()
            while True:
                msg = mav.recv_match(
                    type=["GLOBAL_POSITION_INT", "GPS_RAW_INT"],
                    blocking=True, timeout=5
                )
                if msg is None:
                    continue
                # 更新 _gps_cache ...
        except Exception:
            time.sleep(5)  # 斷線後 5 秒重試
```

### 9.2 GPS 訊息解析

`GLOBAL_POSITION_INT` 欄位：

| 欄位 | 單位 | 說明 |
|------|------|------|
| `lat` | 1e-7 度 | 緯度（需除以 1e7） |
| `lon` | 1e-7 度 | 經度（需除以 1e7） |
| `relative_alt` | mm | 相對起飛點高度（需除以 1000） |

`GPS_RAW_INT` 用於取得 `fix_type`（0=無定位，2=2D Fix，3=3D Fix）。

### 9.3 定位有效性判斷

```python
def get_gps():
    if _gps_cache.get("fix_type", 0) >= 2:
        return _gps_cache  # 使用實際 GPS
    else:
        # 備援：NTHU 座標（室內測試/GPS 遮蔽時）
        return {**_home_gps, "source": "home"}
```

| fix_type | 說明 | 本系統行為 |
|----------|------|------------|
| 0 | 無定位 | 使用 Home GPS |
| 1 | Dead Reckoning | 使用 Home GPS |
| 2 | 2D Fix（lat/lon） | 使用實際 GPS ✅ |
| 3 | 3D Fix（lat/lon/alt） | 使用實際 GPS ✅ |
| 4–6 | RTK Fix 等 | 使用實際 GPS ✅ |

### 9.4 Home GPS 設定

| 欄位 | 值 |
|------|-----|
| 緯度 | 24.7968° N |
| 經度 | 120.9961° E |
| 高度 | 75.0 m AMSL |
| 位置描述 | 國立清華大學，新竹市東區光復路二段 101 號 |

---

## 10. 告警傳輸協定

### 10.1 傳輸觸發條件

`EventStateMachine.should_send()` 回傳 True 的條件：
1. 當前狀態為 `CONFIRMED`（首次進入）或 `TRACKING`
2. `TRACKING` 狀態下，距上次傳輸已超過 `SM_T_SEND = 2.0` 秒

### 10.2 HTTP 協定

```
POST http://<HUB_URL>/api/v1/alerts
Header: X-Auth-Token: <token>
Content-Type: multipart/form-data

Fields:
  meta     : JSON string（見 10.3）
  thumb    : WebP 縮圖（320×240, Q60）
  evidence : WebP 偵測圖（原始解析度, Q75）
```

### 10.3 Meta JSON 結構

```json
{
  "event_id":    "fire_1771995086333",
  "timestamp":   "2026-02-25T14:22:33.123Z",
  "label_type":  "fire",
  "level":       "tracking",
  "score":       0.9736,
  "camera":      "dual",
  "roi":         [0, 0, 1284, 480],
  "bbox":        [0, 0, 1284, 480],
  "gps": {
    "lat":      24.7968,
    "lon":      120.9961,
    "alt":      75.0,
    "fix_type": 0,
    "source":   "home"
  },
  "note": "camA=[person:0.99, person:0.98] camB=[fire:0.94, person:0.92] sm=tracking",
  "version": "2.1"
}
```

**Event ID 格式**：`{label}_{unix_ms}`
- `label`：最高信心分數的偵測標籤
- `unix_ms`：事件確認時的 Unix 時間戳（毫秒）

### 10.4 圖像規格

| 檔案 | 格式 | 解析度 | 品質 | 典型大小 |
|------|------|--------|------|----------|
| `thumb.webp` | WebP | 320 × 240 | Q60 | 5–15 KB |
| `evidence.webp` | WebP | 1284 × 480 | Q75 | 15–50 KB |

---

## 11. Hub 地面站伺服器

### 11.1 REST API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/api/v1/alerts` | 接收告警（需 X-Auth-Token） |
| `GET` | `/api/v1/pull_requests` | Reachback 查詢（保留） |
| `GET` | `/` | Web Dashboard |
| `GET` | `/events/<id>/<filename>` | 靜態圖片服務 |

### 11.2 事件儲存結構

```
hub_events/
  fire_1771995086333/
    meta.json
    thumb.webp
    evidence.webp
  person_1771995102847/
    meta.json
    ...
```

### 11.3 Dashboard 功能

**事件標題優先規則**（`_primary_display_label()`）：

```python
LABEL_PRIORITY = {"fire": 3, "smoke": 2, "person": 1}
```

從 `note` 欄位解析出所有偵測標籤，選取最高優先級者作為顯示標題。

**GPS 地名解析**（`_location_label()`）：

| 座標範圍 | 顯示地名 |
|----------|----------|
| lat [24.78, 24.82], lon [120.97, 121.02] | 國立清華大學，新竹市東區光復路二段 |

**Dashboard 視覺設計**：
- Badge 文字：`DETECTION`（不顯示 FSM 內部狀態）
- Badge 顏色：fire=`#e74c3c`（紅）/ person=`#27ae60`（綠）/ smoke=`#7f8c8d`（灰）
- Evidence 圖片：全寬顯示（`width:100%; max-width:960px`），點擊開新分頁
- 自動刷新：`<meta http-equiv=refresh content=10>`

---

## 12. 離線容錯機制

### 12.1 觸發條件

POST 失敗（ConnectionError）或 HTTP status ≠ 200 時，自動呼叫 `save_to_queue()`。

### 12.2 Queue 儲存路徑

```
queue/
  <event_id>/
    meta.json
    thumb.webp
    evidence.webp
```

### 12.3 容量估算

| 參數 | 值 |
|------|-----|
| 每筆大小（典型值） | 21–66 KB |
| 32 GB 儲存可儲存 | ~500,000–1,500,000 筆 |

---

## 13. 頻寬分析與可行性評估

### 13.1 事件式 vs 連續串流

| 模式 | 計算 | 需求 |
|------|------|------|
| 本系統（Tracking 峰值） | 66 KB × (1/2s) × 8 bit/byte | **264 Kbps** |
| 本系統（Tracking 均值） | 40 KB × (1/3s) × 8 | **107 Kbps** |
| 本系統（靜默，無事件） | 0 | **0 Kbps** |
| 720p@15fps H.264 | 標準碼率 | 1,000–2,000 Kbps |
| **頻寬節省比例** | | **95–99%** |

### 13.2 通訊環境可行性

| 環境 | 上行頻寬 | 可行性 | 建議調整 |
|------|----------|--------|----------|
| 山區 4G LTE | 200 Kbps–2 Mbps | ✅ 完全可行 | 無需調整 |
| Starlink 衛星 | > 5 Mbps | ✅ 完全可行 | 可縮短 SM_T_SEND 至 1s |
| Iridium/Thuraya | 64–128 Kbps | ⚠️ 需優化 | Q30 + T_SEND≥8s → ~20Kbps |
| WiFi 市區 | > 10 Mbps | ✅ 完全可行 | 可開啟連續模式 |

### 13.3 POST 傳輸時序分解

```
POST 總時間 ≈ 0.22–0.33 秒
  = WireGuard RTT (~0.23 秒)
  + TCP 握手 (~0.01 秒)
  + Server 處理 (~0.01 秒)
  + 資料傳輸 (57 KB @ ~200Kbps ≈ ~0.02 秒)
```

---

## 14. 實測結果與驗證

### 14.1 測試環境

- **日期**：2026-02-25
- **地點**：室內（國立清華大學，以 Home GPS 模擬 GPS 訊號）
- **硬體**：Jetson AGX Thor + D435 × 2 + ArduPilot 飛控 + WireGuard VPN

### 14.2 系統啟動序列 Log

```
[2026-02-25 14:18:41] [INFO] Loading SAM3 from /workspace/sam3 ...
[2026-02-25 14:19:33] [INFO] SAM3 model loaded (3.45GB) CUDA FP16
[2026-02-25 14:19:33] [INFO] MAVLink: connecting /dev/ttyACM1 baud=115200
[2026-02-25 14:19:34] [INFO] Heartbeat OK sysid=1
[2026-02-25 14:19:35] [INFO] Found 2 cameras: ['332522075298', '332522073133']
[2026-02-25 14:19:36] [INFO] Cameras started, entering inference loop @ 2Hz
```

### 14.3 告警傳輸 Log

```
[EVENT] label=fire level=tracking
        event_id=fire_1771995086333
        GPS=(24.7968, 120.9961, 75.0m) source=home
[SEND]  POST → http://10.0.0.7:8080/api/v1/alerts
[SEND]  status=200 time=0.27s thumb=9.7KB evidence=48.8KB total=58.5KB
```

### 14.4 實測效能數據

| 指標 | 數值 |
|------|------|
| SAM3 模型載入時間 | ~52 秒 |
| 推論迴圈頻率 | 2.0 Hz（穩定） |
| POST 耗時（含 VPN） | 0.22–0.33 秒 |
| thumb.webp 大小 | 9.4–9.7 KB |
| evidence.webp 大小 | 47.4–49.5 KB |
| fire 偵測信心分數 | 0.94–0.97 |
| person 偵測信心分數 | 0.92–0.99 |

### 14.5 Dashboard 驗證截圖內容（2026-02-25 14:22–14:29）

- 標題：`DETECTION FIRE` / `DETECTION PERSON`
- Note：`camA=[person:0.99, person:0.98] camB=[fire:0.94, person:0.92] sm=tracking`
- GPS：lat=24.7968 lon=120.9961 alt=75.0m（📍 Home）
- 地名：國立清華大學，新竹市東區光復路二段

---

## 15. 已知問題與解決過程

| 問題 | 錯誤訊息 | 原因 | 解決方案 |
|------|----------|------|----------|
| bpe 路徑錯誤 | `FileNotFoundError: bpe_simple_vocab...` | 路徑應為 `sam3/sam3/assets/` | 修正路徑加上 `/sam3/` 子目錄 |
| pymavlink 缺失 | `ModuleNotFoundError: pymavlink` | Docker 映像未預裝 | `run_uav_alert.sh` 自動安裝 |
| RealSense 超時 | `RuntimeError: Frame timeout` | GPU 推論時相機 buffer 等待超時 | timeout 改為 3000 ms |
| HF 401 未授權 | `401 Client Error: Unauthorized` | facebook/sam3 為 gated model | `huggingface_hub.login(token=HF_TOKEN)` |
| BFloat16 型別衝突 | `Got unsupported ScalarType BFloat16` | sm_110 預設 BF16，OpenCV 不支援 | 移除全域 autocast，改用 per-call `torch.float16` |
| cv2.resize dsize 錯誤 | `!dsize.empty() in function resize` | masks 形狀為 `(N,K,H,W)` | `while mask_i.ndim > 2: mask_i = mask_i[0]` |
| set_image 呼叫 3 次 | 推論速度慢 ~3× | 原始版本在 label 迴圈內重複呼叫 | 移至迴圈外，所有標籤共用特徵 |
| 全圖 bbox "person 0.99" | Dashboard 整張圖被框起來 | `send_alert()` 傳入 `bbox_full=[0,0,W,H]` | bbox 覆蓋 ≥ 85% 時跳過重繪 |
| 室內 GPS 偏差 | lat≈43.8, lon≈-168.9（海上） | 室內無 GPS Fix（fix_type=0） | fix_type < 2 → 回傳 Home GPS（NTHU） |

---

## 16. 優化方向與未來工作

### 16.1 推論速度優化

| 優化項目 | 方法 | 預期效果 |
|----------|------|----------|
| TensorRT INT8 量化 | `torch2trt` 編譯 | 3–5× 速度提升，sm_110 原生支援 |
| 縮小輸入解析度 | 1008 → 512 px | 推論時間 -60%，精度略降 |
| 非同步雙相機推論 | 雙相機各開獨立 Thread | 延遲降低 ~50% |
| 推論頻率提升 | 0.5s → 0.25s（4 Hz） | 響應更快，但 GPU 負載倍增 |

### 16.2 傳輸頻寬優化

| 優化項目 | 方法 | 預期效果 |
|----------|------|----------|
| 動態 WebP 品質 | 依頻寬探測自動調 Q10–Q75 | 衛星環境降至 ~5 KB/次 |
| 差異幀傳輸 | 只傳與前次 evidence 的 ROI 差異 | 減少 40–70% 大小 |
| SM_T_SEND 動態調整 | 依 RSSI/RTT 動態設置 | 適應不同網路環境 |

### 16.3 偵測精度優化

| 優化項目 | 方法 | 預期效果 |
|----------|------|----------|
| IoU 跨幀追蹤 | SORT / ByteTrack | 更穩定的 Tracking 狀態 |
| D435 深度資訊 | 啟用深度流 | 過濾距離 < 0.5m 的誤偵測 |
| 分類別信心門檻 | fire=0.5, smoke=0.6, person=0.4 | 減少各類別特定假陽性 |

### 16.4 定位精度優化

| 優化項目 | 方法 | 預期效果 |
|----------|------|----------|
| RTK GPS 整合 | 地面站 RTK 基站 | 精度從 ±5m → ±10cm |
| 氣壓計高度融合 | MAVLink `VFR_HUD.alt` | 無 Fix 時提供海拔高度 |
| 地理圍欄告警 | 設定禁飛區多邊形 | 邊界侵入自動告警 |

---

## 17. 程式碼結構說明

### 17.1 檔案列表

```
uav_hub/
├── thor_dualcam_event_sender.py  # 主程式 (~1000 行)
├── thor_send_alert.py            # 傳輸函式庫 (~500 行)
├── hub_server.py                 # Hub Flask 伺服器 (~212 行)
├── run_uav_alert.sh              # Docker 一鍵啟動腳本
├── decord.py                     # SAM3 訓練依賴 stub
├── requirements.txt              # pip 依賴清單
├── SYSTEM_PAPER.md               # 本文件
├── hub_events/                   # Hub 事件儲存目錄
└── queue/                        # 離線容錯備份目錄
```

### 17.2 `thor_dualcam_event_sender.py` 模組說明

| 函式/類別 | 說明 |
|-----------|------|
| `Detection` dataclass | 偵測結果容器（label/score/bbox/mask/camera） |
| `init_sam3()` | SAM3 初始化（HF 登入 + bpe 路徑 + CUDA） |
| `_infer_sam3_real()` | FP16 推論主體（set_image × 1 + set_text_prompt × 3） |
| `EventStateMachine` | per-label 5狀態 FSM + 節流傳送控制 |
| `_mav_reader_thread()` | MAVLink GPS 背景執行緒 + 自動重連 |
| `get_gps()` | GPS 讀取 + fix_type 判斷 + Home GPS 備援 |
| `fuse_frames()` | 雙相機拼接 + Detection overlay 繪製 |
| `_main_loop_body()` | 每 0.5s 核心邏輯（推論→FSM→拼接→GPS→POST） |
| `run_realsense_mode()` | pyrealsense2 雙相機主迴圈 + 序號偵測 |
| `main()` | CLI argparse + 初始化 + 執行 |

### 17.3 `thor_send_alert.py` 模組說明

| 函式 | 說明 |
|------|------|
| `build_meta()` | 組裝 meta JSON（event_id/timestamp/GPS/score/note） |
| `make_thumb()` | 製作 320×240 WebP Q60 縮圖 |
| `make_evidence()` | 製作帶 SAM3 mask + bbox overlay 的 evidence WebP |
| `send_alert()` | multipart POST + 失敗回退 save_to_queue() |
| `save_to_queue()` | 寫入 `queue/<event_id>/` 離線備份 |

---

## 18. 快速啟動指南

### 18.1 前置需求

```bash
# 系統需求
- NVIDIA Jetson AGX Thor (or CUDA 13 compatible GPU)
- Docker + NVIDIA Container Toolkit
- Intel RealSense D435 × 2 (USB3)
- ArduPilot 飛控 /dev/ttyACM1
- WireGuard VPN 已設定

# SAM3 模型（需提前下載）
git lfs clone https://huggingface.co/facebook/sam3 /home/alan/xin/sam3
```

### 18.2 Thor 端啟動

```bash
bash /home/alan/xin/uav_hub/run_uav_alert.sh
```

### 18.3 Hub 端啟動

```bash
pip install flask requests Pillow
python3 hub_server.py
# → http://0.0.0.0:8080
```

### 18.4 常用 CLI 參數

```
--no-ros2                     使用 pyrealsense2 模式（Docker 推薦）
--sam3-root /workspace/sam3   SAM3 模型路徑
--hub-url http://10.0.0.7:8080/api/v1/alerts
--mavlink /dev/ttyACM1        飛控串口
--baud 115200                 MAVLink Baud Rate
--home-lat 24.7968            Home GPS 緯度
--home-lon 120.9961           Home GPS 經度
--home-alt 75                 Home GPS 高度 (m)
--score-thr 0.45              SAM3 信心分數門檻
--loop-interval 0.5           推論間隔 (秒)
```

---

## 19. 參考資料

1. **SAM**: Kirillov et al., "Segment Anything," *ICCV 2023*. arXiv:2304.02643
2. **SAM2**: Ravi et al., "SAM 2: Segment Anything in Images and Videos," *2024*. arXiv:2408.00714
3. **MAVLink Protocol**: MAVLink Development Team, *MAVLink Micro Air Vehicle Communication Protocol*, v2.0, 2024. https://mavlink.io
4. **Intel RealSense D435**: Intel Corporation, *Intel RealSense Depth Camera D435 Product Brief*, 2023.
5. **WireGuard**: Donenfeld, J.A., "WireGuard: Next Generation Kernel Network Tunnel," *NDSS 2017*.
6. **NVIDIA Jetson AGX Thor**: NVIDIA Corporation, *Jetson AGX Thor Technical Reference Manual*, 2024.
7. **PyTorch Mixed Precision**: NVIDIA, *Automatic Mixed Precision Training*, PyTorch Documentation, 2024.

---

*最後更新：2026-02-25 · 版本 v2.0（論文版）*
