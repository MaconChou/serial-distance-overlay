# com-overlay

讀取 COM port 訊息，依距離範圍改變指定螢幕的**全畫面色彩覆蓋**（顏色 + 透明度）。
覆蓋層為滑鼠穿透、不搶焦點的視窗，不影響原本操作，只改變畫面顏色。

> 平台：Windows（Python 3.8+）。覆蓋層的滑鼠穿透使用 Win32 API。

## 安裝

```bash
git clone <YOUR_REPO_URL>
cd com-overlay
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python com_overlay.py
```

## 訊息格式

每則訊息以換行結尾（`\n` 或 `\r\n`）：

| 訊息 | 行為 |
|------|------|
| `off` | 不動作（可勾選「收到 off 時隱藏覆蓋」） |
| `distance:25.5` | 依範圍規則套用顏色/透明度 |
| 距離不在任何範圍內 | 恢復無覆蓋 |

## 範圍規則

判斷式為 `最小 ≤ 距離 < 最大`，由上往下取第一個符合的規則。
預設：

| 範圍 | 顏色 | 透明度 |
|------|------|--------|
| 0 – 30 | 紅 `#ff0000` | 60% |
| 30 – 60 | 黃 `#ffd000` | 40% |
| 60 – 100 | 綠 `#00c000` | 30% |

設定會儲存在 `com_overlay_config.json`（已加入 `.gitignore`）。

## 打包成 exe（選用）

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile com_overlay.py
```

## 測試用（無硬體）

用虛擬序列埠（如 com0com）配對後，用任一序列終端機送出 `distance:20`；
或直接用 GUI 的「手動測試距離」。

## 授權

MIT，見 [LICENSE](LICENSE)。
