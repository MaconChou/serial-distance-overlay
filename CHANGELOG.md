# Changelog

格式參考 [Keep a Changelog](https://keepachangelog.com/)，版本遵循 [SemVer](https://semver.org/)。

## [0.2.1] - 2026-11-27

### Changed
- 手動「測試」鈕改為按住才套用測試距離，放開即恢復無覆蓋。

## [0.2.0] - 2026-11-27

### Added
- 斷線（非預期）或收到未知訊息時，紅 50% / 藍 50% 交替閃爍警示。
- 警示可勾選啟用/停用，並可設定閃爍間隔（ms）。

### Changed
- 收到 `off` 改為恢復無覆蓋（移除「收到 off 時隱藏覆蓋」選項）。
- 設定檔 `hide_on_off` 改為 `alert_enabled` 與 `alert_interval_ms`。

## [0.1.0] - 2026-11-27

### Added
- COM port 連線與逐行讀取訊息（可選 Baud）。
- 訊息 `off`：不動作（可選「收到 off 時隱藏覆蓋」）。
- 訊息 `distance:數字`：依範圍規則改變指定螢幕顏色與透明度。
- 範圍規則編輯（新增/更新/刪除/上下移），設定自動存於 `com_overlay_config.json`。
- 可指定單一螢幕或全部螢幕；覆蓋層滑鼠穿透、不搶焦點。
- 手動測試距離功能。

### Changed
- 距離不在任何設定範圍內時，自動恢復無覆蓋。
