#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
COM Port 距離訊息 → 螢幕色彩覆蓋工具 (Windows)

安裝:  pip install pyserial
執行:  python com_overlay.py

訊息規則 (每行一則, 以換行結尾):
  off              -> 恢復無覆蓋
  distance:數字    -> 依範圍規則改變指定螢幕的覆蓋顏色/透明度 (範圍外 = 無覆蓋)
  斷線 / 未知訊息  -> 紅色 50% 與藍色 50% 交替閃爍 (可勾選是否啟用)

覆蓋視窗為「滑鼠穿透」+「不搶焦點」，不影響原本操作，只改變畫面顏色。
"""
import ctypes
import datetime
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("請先安裝 pyserial:  pip install pyserial")
    sys.exit(1)

IS_WIN = sys.platform == "win32"
if IS_WIN:  # 讓 Tk 座標與實體像素一致 (多螢幕/縮放)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "com_overlay_config.json")
ALERT_COLORS = ("#ff0000", "#0000ff")   # 紅 / 藍 交替
ALERT_OPACITY = 50                      # 50%
DIST_RE = re.compile(r"^distance\s*:\s*(-?\d+(?:\.\d+)?)$", re.IGNORECASE)

DEFAULT_RULES = [
    {"min": 0, "max": 30, "color": "#ff0000", "opacity": 60},
    {"min": 30, "max": 60, "color": "#ffd000", "opacity": 40},
    {"min": 60, "max": 100, "color": "#00c000", "opacity": 30},
]


# ---------------------------------------------------------------- 螢幕列舉
def get_monitors(root):
    """回傳 [(x, y, w, h), ...]"""
    mons = []
    if IS_WIN:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.POINTER(RECT), ctypes.c_void_p)

        def cb(hmon, hdc, lprc, lparam):
            r = lprc.contents
            mons.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    if not mons:
        mons = [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]
    return mons


# ---------------------------------------------------------------- 覆蓋視窗
class Overlay:
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020   # 滑鼠穿透
    WS_EX_TOOLWINDOW = 0x00000080    # 不出現在工作列
    WS_EX_NOACTIVATE = 0x08000000    # 不搶焦點

    def __init__(self, master, rect):
        x, y, w, h = rect
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{w}x{h}{x:+d}{y:+d}")
        self.win.configure(bg="black")
        self.visible = False

    def _click_through(self):
        if not IS_WIN:
            return
        try:
            self.win.update_idletasks()
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            style = user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            style |= (self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT |
                      self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE)
            user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, style)
        except Exception:
            pass

    def show(self, color, opacity_pct):
        self.win.configure(bg=color)
        self.win.attributes("-alpha", max(0.0, min(1.0, opacity_pct / 100.0)))
        if not self.visible:
            self.win.deiconify()
            self.visible = True
        self._click_through()
        self.win.attributes("-topmost", True)

    def hide(self):
        if self.visible:
            self.win.withdraw()
            self.visible = False

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- 主程式
class App:
    def __init__(self, root):
        self.root = root
        root.title("COM 距離 → 螢幕色彩覆蓋")
        root.geometry("760x640")

        self.ser = None
        self.thread = None
        self.stop_evt = threading.Event()
        self.q = queue.Queue()
        self.overlays = {}          # monitor index -> Overlay
        self.monitors = get_monitors(root)
        self.rules = [dict(r) for r in DEFAULT_RULES]
        self.pick_color = "#ff0000"
        self.alert_job = None
        self.alert_phase = 0
        self.test_job = None

        self._build_ui()
        self._load_config()
        self._refresh_ports()
        self._refresh_rules()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._poll_queue)

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # --- 連線
        f1 = ttk.LabelFrame(self.root, text="序列埠連線")
        f1.pack(fill="x", padx=8, pady=6)
        ttk.Label(f1, text="COM:").grid(row=0, column=0, **pad)
        self.cb_port = ttk.Combobox(f1, width=26, state="readonly")
        self.cb_port.grid(row=0, column=1, **pad)
        ttk.Button(f1, text="重新整理", command=self._refresh_ports).grid(row=0, column=2, **pad)
        ttk.Label(f1, text="Baud:").grid(row=0, column=3, **pad)
        self.cb_baud = ttk.Combobox(f1, width=9, values=[9600, 19200, 38400, 57600, 115200, 230400])
        self.cb_baud.set("115200")
        self.cb_baud.grid(row=0, column=4, **pad)
        self.btn_conn = ttk.Button(f1, text="連線", command=self._toggle_connect)
        self.btn_conn.grid(row=0, column=5, **pad)
        self.lbl_status = ttk.Label(f1, text="● 未連線", foreground="gray")
        self.lbl_status.grid(row=0, column=6, **pad)

        # --- 螢幕
        f2 = ttk.LabelFrame(self.root, text="覆蓋目標螢幕")
        f2.pack(fill="x", padx=8, pady=6)
        names = [f"螢幕 {i + 1}  ({w}x{h} @ {x},{y})" for i, (x, y, w, h) in enumerate(self.monitors)]
        names.append("全部螢幕")
        self.cb_mon = ttk.Combobox(f2, values=names, state="readonly", width=40)
        self.cb_mon.current(0)
        self.cb_mon.grid(row=0, column=0, **pad)
        self.cb_mon.bind("<<ComboboxSelected>>", self._on_monitor_change)
        ttk.Button(f2, text="清除覆蓋", command=self._clear_overlays).grid(row=0, column=1, **pad)

        # --- 異常警示
        f2b = ttk.LabelFrame(self.root, text="異常警示 (斷線 / 收到未知訊息)")
        f2b.pack(fill="x", padx=8, pady=6)
        self.var_alert = tk.BooleanVar(value=True)
        ttk.Checkbutton(f2b, text="啟用 紅50% / 藍50% 交替閃爍",
                        variable=self.var_alert,
                        command=self._on_alert_toggle).grid(row=0, column=0, **pad)
        ttk.Label(f2b, text="間隔(ms):").grid(row=0, column=1, **pad)
        self.sp_interval = ttk.Spinbox(f2b, from_=100, to=5000, increment=100, width=7)
        self.sp_interval.set("500")
        self.sp_interval.grid(row=0, column=2, **pad)

        # --- 規則
        f3 = ttk.LabelFrame(self.root, text="距離範圍 → 顏色 / 透明度  (min ≤ 距離 < max，由上而下第一個符合者)")
        f3.pack(fill="both", expand=True, padx=8, pady=6)
        cols = ("min", "max", "color", "opacity")
        self.tree = ttk.Treeview(f3, columns=cols, show="headings", height=6, selectmode="browse")
        for c, t, w in (("min", "最小值", 90), ("max", "最大值", 90),
                        ("color", "顏色", 110), ("opacity", "透明度 %", 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="x", padx=6, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_rule)

        ef = ttk.Frame(f3)
        ef.pack(fill="x", padx=6, pady=4)
        ttk.Label(ef, text="最小:").grid(row=0, column=0)
        self.e_min = ttk.Entry(ef, width=8)
        self.e_min.grid(row=0, column=1, padx=4)
        ttk.Label(ef, text="最大:").grid(row=0, column=2)
        self.e_max = ttk.Entry(ef, width=8)
        self.e_max.grid(row=0, column=3, padx=4)
        self.btn_color = tk.Button(ef, text="選擇顏色", width=10, bg=self.pick_color,
                                   fg="white", command=self._choose_color)
        self.btn_color.grid(row=0, column=4, padx=6)
        ttk.Label(ef, text="透明度:").grid(row=0, column=5)
        self.sc_op = tk.Scale(ef, from_=0, to=100, orient="horizontal", length=140)
        self.sc_op.set(50)
        self.sc_op.grid(row=0, column=6, padx=4)

        bf = ttk.Frame(f3)
        bf.pack(fill="x", padx=6, pady=4)
        ttk.Button(bf, text="新增", command=self._add_rule).pack(side="left", padx=3)
        ttk.Button(bf, text="更新選取", command=self._update_rule).pack(side="left", padx=3)
        ttk.Button(bf, text="刪除選取", command=self._delete_rule).pack(side="left", padx=3)
        ttk.Button(bf, text="上移", command=lambda: self._move_rule(-1)).pack(side="left", padx=3)
        ttk.Button(bf, text="下移", command=lambda: self._move_rule(1)).pack(side="left", padx=3)

        tf = ttk.Frame(f3)
        tf.pack(fill="x", padx=6, pady=4)
        ttk.Label(tf, text="手動測試距離 (按住測試鈕):").pack(side="left")
        self.e_test = ttk.Entry(tf, width=8)
        self.e_test.insert(0, "20")
        self.e_test.pack(side="left", padx=4)
        self.btn_test = ttk.Button(tf, text="測試 (按住)")
        self.btn_test.pack(side="left")
        self.btn_test.bind("<ButtonPress-1>", self._test_press)
        self.btn_test.bind("<ButtonRelease-1>", self._test_release)

        # --- Log
        f4 = ttk.LabelFrame(self.root, text="接收訊息")
        f4.pack(fill="both", expand=True, padx=8, pady=6)
        self.txt = tk.Text(f4, height=8, state="disabled", wrap="none")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)

    # ------------------------------------------------------------ 序列埠
    def _refresh_ports(self):
        ports = [f"{p.device} - {p.description}" for p in list_ports.comports()]
        self.cb_port["values"] = ports
        if ports and not self.cb_port.get():
            self.cb_port.current(0)

    def _toggle_connect(self):
        if self.ser:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        sel = self.cb_port.get().split(" - ")[0].strip()
        if not sel:
            messagebox.showwarning("提示", "請先選擇 COM port")
            return
        try:
            self.ser = serial.Serial(sel, int(self.cb_baud.get()), timeout=0.2)
        except Exception as e:
            self.ser = None
            messagebox.showerror("連線失敗", str(e))
            return
        self._clear_overlays()
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        self.btn_conn.config(text="中斷")
        self.lbl_status.config(text=f"● 已連線 {sel}", foreground="green")
        self._log(f"[系統] 已連線 {sel}")

    def _disconnect(self, alert=False):
        """alert=True: 非預期斷線 -> 啟動警示; 手動中斷 -> 清除覆蓋"""
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=1)
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.btn_conn.config(text="連線")
        self.lbl_status.config(text="● 未連線", foreground="gray")
        self._log("[系統] 已中斷")
        if alert:
            self._start_alert()
        else:
            self._clear_overlays()

    def _reader(self):
        buf = b""
        while not self.stop_evt.is_set():
            try:
                data = self.ser.read(256)
            except Exception as e:
                self.q.put(("error", str(e)))
                return
            if not data:
                continue
            buf += data
            while True:
                m = re.search(rb"[\r\n]+", buf)
                if not m:
                    break
                line, buf = buf[:m.start()], buf[m.end():]
                if line.strip():
                    self.q.put(("line", line.decode("utf-8", "ignore").strip()))
            if len(buf) > 4096:
                buf = b""

    def _poll_queue(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    self._handle_line(val)
                elif kind == "error":
                    self._log(f"[錯誤] {val}")
                    self._disconnect(alert=True)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    # ------------------------------------------------------------ 訊息處理
    def _handle_line(self, line):
        self._log(f"RX: {line}")
        if line.lower() == "off":
            self._clear_overlays()          # 恢復無覆蓋 (同時停止警示)
            return
        m = DIST_RE.match(line)
        if m:
            self._stop_alert()
            self._apply_distance(float(m.group(1)))
            return
        # 未知訊息 -> 警示
        self._log("  未知訊息，啟動警示")
        self._start_alert()

    def _match_rule(self, d):
        for r in self.rules:
            if r["min"] <= d < r["max"]:
                return r
        return None

    def _apply_distance(self, d):
        r = self._match_rule(d)
        if not r:
            # 不在任何設定範圍內 -> 恢復無覆蓋
            self._log(f"  距離 {d} 不在任何範圍內，恢復無覆蓋")
            for ov in self.overlays.values():
                ov.hide()
            return
        for idx in self._target_indices():
            ov = self.overlays.get(idx)
            if ov is None:
                ov = self.overlays[idx] = Overlay(self.root, self.monitors[idx])
            ov.show(r["color"], r["opacity"])

    def _target_indices(self):
        i = self.cb_mon.current()
        if i < 0:
            i = 0
        if i >= len(self.monitors):  # 全部螢幕
            return list(range(len(self.monitors)))
        return [i]

    def _clear_overlays(self):
        self._stop_alert()
        for ov in self.overlays.values():
            ov.destroy()
        self.overlays.clear()

    # ------------------------------------------------------------ 警示 (紅/藍交替)
    def _start_alert(self):
        if not self.var_alert.get() or self.alert_job is not None:
            return
        self.alert_phase = 0
        self._alert_tick()

    def _stop_alert(self):
        if self.alert_job is not None:
            self.root.after_cancel(self.alert_job)
            self.alert_job = None

    def _alert_tick(self):
        color = ALERT_COLORS[self.alert_phase]
        self.alert_phase ^= 1
        for idx in self._target_indices():
            ov = self.overlays.get(idx)
            if ov is None:
                ov = self.overlays[idx] = Overlay(self.root, self.monitors[idx])
            ov.show(color, ALERT_OPACITY)
        try:
            ms = max(100, int(self.sp_interval.get()))
        except ValueError:
            ms = 500
        self.alert_job = self.root.after(ms, self._alert_tick)

    def _on_alert_toggle(self):
        if not self.var_alert.get() and self.alert_job is not None:
            self._clear_overlays()

    def _on_monitor_change(self, _e=None):
        was_alert = self.alert_job is not None
        self._clear_overlays()
        if was_alert:
            self._start_alert()

    # ------------------------------------------------------------ 手動測試 (按住)
    def _test_press(self, _e=None):
        try:
            self._test_value = float(self.e_test.get())
        except ValueError:
            messagebox.showwarning("提示", "請輸入數字")
            return
        self._stop_alert()
        self._test_tick()

    def _test_tick(self):
        self._apply_distance(self._test_value)
        self.test_job = self.root.after(100, self._test_tick)  # 按住期間持續套用

    def _test_release(self, _e=None):
        if self.test_job is None:
            return
        self.root.after_cancel(self.test_job)
        self.test_job = None
        self._clear_overlays()   # 放開 -> 恢復無覆蓋

    # ------------------------------------------------------------ 規則編輯
    def _refresh_rules(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rules:
            self.tree.insert("", "end", values=(r["min"], r["max"], r["color"], r["opacity"]))

    def _choose_color(self):
        c = colorchooser.askcolor(color=self.pick_color, title="選擇顏色")[1]
        if c:
            self.pick_color = c
            self.btn_color.config(bg=c)

    def _read_form(self):
        try:
            mn, mx = float(self.e_min.get()), float(self.e_max.get())
        except ValueError:
            messagebox.showwarning("提示", "最小/最大值請輸入數字")
            return None
        if mn >= mx:
            messagebox.showwarning("提示", "最小值必須小於最大值")
            return None
        mn = int(mn) if mn == int(mn) else mn
        mx = int(mx) if mx == int(mx) else mx
        return {"min": mn, "max": mx, "color": self.pick_color, "opacity": int(self.sc_op.get())}

    def _sel_index(self):
        s = self.tree.selection()
        return self.tree.index(s[0]) if s else None

    def _add_rule(self):
        r = self._read_form()
        if r:
            self.rules.append(r)
            self._refresh_rules()

    def _update_rule(self):
        i = self._sel_index()
        r = self._read_form()
        if i is not None and r:
            self.rules[i] = r
            self._refresh_rules()

    def _delete_rule(self):
        i = self._sel_index()
        if i is not None:
            del self.rules[i]
            self._refresh_rules()

    def _move_rule(self, d):
        i = self._sel_index()
        if i is None:
            return
        j = i + d
        if 0 <= j < len(self.rules):
            self.rules[i], self.rules[j] = self.rules[j], self.rules[i]
            self._refresh_rules()
            self.tree.selection_set(self.tree.get_children()[j])

    def _on_select_rule(self, _e):
        i = self._sel_index()
        if i is None:
            return
        r = self.rules[i]
        self.e_min.delete(0, "end"); self.e_min.insert(0, r["min"])
        self.e_max.delete(0, "end"); self.e_max.insert(0, r["max"])
        self.pick_color = r["color"]
        self.btn_color.config(bg=r["color"])
        self.sc_op.set(r["opacity"])

    # ------------------------------------------------------------ 其他
    def _log(self, s):
        self.txt.config(state="normal")
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.txt.insert("end", f"[{ts}] {s}\n")
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > 300:
            self.txt.delete("1.0", f"{n - 300}.0")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                c = json.load(f)
            self.rules = c.get("rules", self.rules)
            self.cb_baud.set(str(c.get("baud", "115200")))
            self.var_alert.set(c.get("alert_enabled", True))
            self.sp_interval.set(str(c.get("alert_interval_ms", 500)))
            mi = c.get("monitor", 0)
            if 0 <= mi < len(self.cb_mon["values"]):
                self.cb_mon.current(mi)
        except Exception:
            pass

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"rules": self.rules, "baud": self.cb_baud.get(),
                           "alert_enabled": self.var_alert.get(),
                           "alert_interval_ms": self.sp_interval.get(),
                           "monitor": self.cb_mon.current()},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_config()
        self._disconnect()
        self._clear_overlays()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
