"""Tk desktop editor. All Artist mutations and draws happen on its main thread."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.text import Text
from matplotlib.legend import Legend
from matplotlib.axes import Axes
import numpy as np

from scene import read_json, write_json
from gis import build_scene, normalise_spec, open_session
from transport import Mailbox, send


class PreviewToolbar(NavigationToolbar2Tk):
    # Data/extent changes are semantic edits, not untracked navigation edits.
    toolitems = tuple(item for item in NavigationToolbar2Tk.toolitems if item[3] not in {'pan', 'zoom', 'save_figure', 'configure_subplots'})


class Editor:
    def __init__(self, root, scene, folder, spec):
        self.root, self.scene, self.folder, self.spec = root, scene, Path(folder), spec
        self.root.title("GIS 出图编辑器 — " + self.folder.name)
        self.root.geometry("1160x780")
        self.root.minsize(920, 650)
        self.drag = None
        self.selected = None
        main = ttk.Frame(root); main.pack(fill="both", expand=True)
        left = ttk.Frame(main); left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(main, padding=12, width=310); right.pack(side="right", fill="y")
        ttk.Label(right, text="可编辑对象", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w")
        self.objects = tk.Listbox(right, height=13, exportselection=False, width=34)
        self.objects.pack(fill="x", pady=8)
        for key in scene.objects: self.objects.insert("end", key)
        self.objects.bind("<<ListboxSelect>>", self.select_from_list)
        ttk.Label(right, text="属性").pack(anchor="w")
        self.prop = ttk.Combobox(right, state="readonly")
        self.prop.pack(fill="x", pady=4); self.prop.bind("<<ComboboxSelected>>", self.populate_value)
        ttk.Label(right, text="值（文本直接输入；位置为数字数组）").pack(anchor="w")
        self.value = ttk.Entry(right, width=35); self.value.pack(fill="x", pady=4)
        ttk.Button(right, text="应用修改", command=self.apply).pack(fill="x", pady=5)
        actions = ttk.Frame(right); actions.pack(fill="x", pady=6)
        ttk.Button(actions, text="撤销", command=lambda: self.history("undo")).pack(side="left", expand=True, fill="x")
        ttk.Button(actions, text="重做", command=lambda: self.history("redo")).pack(side="left", expand=True, fill="x")
        ttk.Button(right, text="检查布局与 GIS 提示", command=self.audit).pack(fill="x", pady=5)
        ttk.Button(right, text="保存会话", command=self.save).pack(fill="x", pady=5)
        ttk.Button(right, text="导出 PNG / PDF / SVG", command=self.export).pack(fill="x", pady=5)
        ttk.Label(right, text="拖拽：选中标题、注释、图例或图框\n图框位置：整幅图的比例坐标\n数据值/几何坐标：只读\n改投影、范围：修改配置并建新会话", wraplength=290, justify="left").pack(anchor="w", pady=12)
        self.status = tk.StringVar(value="已加载；样式编辑不会重跑分析")
        ttk.Label(root, textvariable=self.status, wraplength=1080, padding=8).pack(fill="x")
        self.canvas = FigureCanvasTkAgg(scene.figure, master=left)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.toolbar = PreviewToolbar(self.canvas, left, pack_toolbar=False)
        self.toolbar.update(); self.toolbar.pack(fill="x")
        self.canvas.mpl_connect("button_press_event", self.press)
        self.canvas.mpl_connect("motion_notify_event", self.motion)
        self.canvas.mpl_connect("button_release_event", self.release)
        self.mailbox = Mailbox(folder, scene, spec)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.poll)

    def select(self, key):
        self.selected = key
        keys = list(self.scene.objects)
        index = keys.index(key)
        self.objects.selection_clear(0, "end"); self.objects.selection_set(index); self.objects.see(index)
        properties = self.scene.properties(key)
        allowed = self.scene.allowed_properties(key)
        self.prop["values"] = sorted(allowed)
        self.prop.set("position" if "position" in allowed else sorted(allowed)[0])
        self.populate_value()

    def select_from_list(self, event=None):
        chosen = self.objects.curselection()
        if chosen: self.select(self.objects.get(chosen[0]))

    def populate_value(self, event=None):
        if not self.selected: return
        value = self.scene.properties(self.selected).get(self.prop.get())
        self.value.delete(0, "end")
        self.value.insert(0, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))

    def apply(self):
        if not self.selected: return
        text = self.value.get(); prop = self.prop.get()
        if prop in {'text', 'title', 'fontfamily', 'color', 'edgecolor', 'facecolor', 'framecolor', 'cmap', 'ha', 'va', 'linestyle'}:
            value = text
        else:
            try: value = json.loads(text)
            except json.JSONDecodeError: value = text
        try:
            self.scene.edit([{"target": self.selected, "properties": {prop: value}}])
            self.save(silent=True)
            self.canvas.draw_idle()
            self.status.set(f"已修改 {self.selected}.{prop}；版本 {self.scene.revision}")
        except Exception as exc: messagebox.showerror("修改未应用", str(exc), parent=self.root)

    def press(self, event):
        if event.button != 1 or self.toolbar.mode or event.x is None: return
        candidates = []
        for key, obj in self.scene.objects.items():
            if not obj.get_visible(): continue
            try:
                hit = obj.contains(event)[0]
                if hit:
                    priority = 0 if isinstance(obj, (Text, Legend)) else 1 if isinstance(obj, Axes) else 2
                    candidates.append((priority, key, obj))
            except Exception: pass
        if not candidates: return
        _, key, obj = sorted(candidates, key=lambda row: row[0])[0]
        self.select(key)
        if not isinstance(obj, (Text, Legend, Axes)): return
        # Geo annotations with arrow have coupled coordinates, so no drag support.
        if isinstance(obj, Text) and getattr(obj, "arrow_patch", None) is not None:
            self.status.set("带箭头注释不支持单独拖拽；需专用复合对象适配器")
            return
        start = self.scene.properties(key)["position"]
        if isinstance(obj, Legend):
            start = self.scene.figure.transFigure.inverted().transform(obj.get_window_extent(self.canvas.get_renderer()).get_points()[0]).tolist()
        self.drag = {"key": key, "obj": obj, "pixel": np.array([event.x, event.y]), "start": start,
                     "before": self.scene.snapshot(), "last": start, "changed": False}

    def motion(self, event):
        if self.drag is None or event.x is None or event.y is None: return
        d = self.drag; obj = d["obj"]
        transform = self.scene.figure.transFigure if isinstance(obj, (Legend, Axes)) else obj.get_transform()
        inv = transform.inverted()
        delta = inv.transform([event.x, event.y]) - inv.transform(d["pixel"])
        pos = list(d["start"])
        pos[0] += float(delta[0]); pos[1] += float(delta[1])
        if isinstance(obj, Axes):
            pos[0] = min(1 - pos[2], max(0, pos[0])); pos[1] = min(1 - pos[3], max(0, pos[1]))
        if isinstance(obj, Legend): pos = [min(.99, max(0, x)) for x in pos]
        try:
            self.scene._apply(d["key"], {"position": pos})
            d["last"] = pos; d["changed"] = True
            self.canvas.draw_idle()
        except Exception as exc:
            self.status.set(str(exc))

    def release(self, event):
        if self.drag is None: return
        d, self.drag = self.drag, None
        if not d["changed"]: return
        # Commit through the same validation/history path used by Agent patches.
        self.scene.restore(d["before"])
        try:
            self.scene.edit([{"target": d["key"], "properties": {"position": d["last"]}}])
            self.save(silent=True); self.populate_value()
            self.status.set(f"已移动 {d['key']}；版本 {self.scene.revision}")
        except Exception as exc: messagebox.showerror("移动未应用", str(exc), parent=self.root)

    def history(self, operation):
        try:
            getattr(self.scene, operation)(); self.save(silent=True); self.canvas.draw_idle(); self.populate_value()
            self.status.set(f"{'已撤销' if operation == 'undo' else '已重做'}；版本 {self.scene.revision}")
        except Exception as exc: self.status.set(str(exc))

    def audit(self):
        audit = self.scene.audit()
        messagebox.showinfo("检查范围：布局预检，不代表科学/发布合规认证",
                            "\n".join(audit["issues"]) or "注册的标题/图例未发现裁切或互相重叠；仍需人工检查是否遮挡地图证据。", parent=self.root)

    def save(self, silent=False):
        import matplotlib
        figsize = self.scene.figure.get_size_inches().copy()
        try:
            self.scene.figure.set_size_inches(self.scene.export_size_inches)
            with matplotlib.rc_context({'savefig.bbox': None}):
                self.scene.figure.savefig(self.folder / 'preview.png', dpi=110, bbox_inches=None)
        finally:
            self.scene.figure.set_size_inches(figsize)
            self.scene.figure.canvas.draw()
        self.scene.save_session(self.folder, self.spec)
        if not silent: self.status.set(f"会话已保存：{self.folder / 'session.json'}")

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.root, initialdir=self.folder, defaultextension=".png",
                  filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")], title="导出成图（保留实际图幅尺寸）")
        if not path: return
        try:
            overwrite = False
            if Path(path).exists() or Path(path + ".manifest.json").exists():
                overwrite = messagebox.askyesno("确认替换", "将替换所选成图及对应来源记录，是否继续？", parent=self.root)
                if not overwrite: return
            result = self.scene.export(path, overwrite=overwrite)
            self.save(silent=True); self.status.set("已导出：" + result["path"])
        except Exception as exc: messagebox.showerror("导出失败", str(exc), parent=self.root)

    def poll(self):
        try:
            if self.drag is None:
                previous_revision = self.scene.revision
                if self.mailbox.process(): self.close(); return
                if self.scene.revision != previous_revision:
                    self.save(silent=True)
                    self.canvas.draw_idle()
                    if self.selected: self.populate_value()
        except Exception as exc: self.status.set("命令处理异常：" + str(exc))
        self.root.after(200, self.poll)

    def close(self):
        try: self.save(silent=True); self.mailbox.finish()
        finally: self.root.destroy()


def acquire_lock(folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "editor.lock"
    if lock.exists():
        status = folder / "runtime.json"
        if status.exists():
            s = read_json(status)
            if s.get("state") == "running" and time.time() - s.get("heartbeat", 0) < 10:
                raise RuntimeError("Session is already open; use send instead of opening a second editor")
        # stale lock only; never kill processes or overwrite user state
        lock.unlink()
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as stream: stream.write(str(os.getpid()))
    return lock


def gui(folder, spec_path=None, trusted=False):
    lock = acquire_lock(folder)
    try:
        spec = None
        if not (Path(folder) / "session.json").exists():
            if spec_path is None: raise ValueError("A new session needs --spec")
            spec = normalise_spec(read_json(spec_path), Path(spec_path).parent)
        scene, spec = open_session(folder, spec, trusted)
        root = tk.Tk()
        Editor(root, scene, folder, spec)
        root.mainloop()
    finally:
        if lock.exists(): lock.unlink()


def welcome():
    root = tk.Tk(); root.title("GIS 出图编辑器"); root.geometry("650x350")
    box = ttk.Frame(root, padding=24); box.pack(fill="both", expand=True)
    ttk.Label(box, text="GIS 出图编辑器", font=("Microsoft YaHei", 18, "bold")).pack(anchor="w", pady=10)
    ttk.Label(box, text="同一个画布持续编辑；指标与几何数据不随样式修改而重算。\n打开场景 JSON，或选择含 session.json 的目录恢复上次修改。", wraplength=600, justify="left").pack(anchor="w", pady=8)
    choice = {}
    def pick_spec():
        p = filedialog.askopenfilename(parent=root, title="选择场景配置", filetypes=[("JSON", "*.json")])
        if not p: return
        folder = filedialog.askdirectory(parent=root, title="选择新会话目录（不能已有 session.json）")
        if not folder: return
        trusted = False
        if read_json(p).get("mode") == "factory":
            trusted = messagebox.askyesno("确认执行本地 Python", "该配置会执行本地绘图函数。请仅打开自己编写或已审查的代码，是否继续？", parent=root)
            if not trusted: return
        choice.update(folder=folder, spec=p, trusted=trusted); root.destroy()
    def restore():
        folder = filedialog.askdirectory(parent=root, title="选择已有会话目录")
        if not folder: return
        saved = read_json(Path(folder) / "session.json")
        trusted = False
        if saved["spec"].get("mode") == "factory":
            trusted = messagebox.askyesno("确认执行已保存的绘图函数", "恢复时会重新执行本地绘图函数，再应用保存的样式。是否继续？", parent=root)
            if not trusted: return
        choice.update(folder=folder, spec=None, trusted=trusted); root.destroy()
    def demo():
        folder = filedialog.askdirectory(parent=root, title="选择演示会话保存目录")
        if not folder: return
        assets = Path(sys._MEIPASS) / 'assets' if getattr(sys, 'frozen', False) else Path(__file__).parent.parent / 'assets'
        choice.update(folder=folder, spec=str(assets / 'demo.json'), trusted=True); root.destroy()
    for text, action in [("打开 GIS / Figure 场景配置", pick_spec), ("恢复已保存会话", restore), ("运行合成数据演示（不是真实研究结果）", demo)]:
        ttk.Button(box, text=text, command=action).pack(fill="x", pady=6)
    root.mainloop()
    if choice: gui(choice["folder"], choice["spec"], choice["trusted"])


def main():
    parser = argparse.ArgumentParser(description="GIS editable plotting skill runtime")
    commands = parser.add_subparsers(dest="action", required=True)
    g = commands.add_parser("gui"); g.add_argument("--session"); g.add_argument("--spec"); g.add_argument("--trusted-factory", action="store_true")
    r = commands.add_parser("render"); r.add_argument("--session", required=True); r.add_argument("--spec"); r.add_argument("--trusted-factory", action="store_true"); r.add_argument("--output", required=True); r.add_argument("--dpi", type=int, default=300)
    s = commands.add_parser("send"); s.add_argument("--session", required=True); s.add_argument("--command", required=True); s.add_argument("--timeout", type=float, default=15)
    d = commands.add_parser("doctor")
    args = parser.parse_args()
    try:
        if args.action == "doctor":
            import matplotlib, geopandas, rasterio, pyproj
            print(json.dumps({"ok": True, "python": sys.version, "matplotlib": matplotlib.__version__, "geopandas": geopandas.__version__, "rasterio": rasterio.__version__, "pyproj": pyproj.__version__, "tk": tk.TkVersion}, ensure_ascii=False))
        elif args.action == "send":
            result = send(args.session, read_json(args.command), args.timeout)
            print(json.dumps(result, ensure_ascii=False)); return 0 if result["ok"] else 1
        elif args.action == "gui":
            if args.session: gui(args.session, args.spec, args.trusted_factory)
            else: welcome()
        elif args.action == "render":
            if (Path(args.session) / "editor.lock").exists(): raise RuntimeError("Session is locked by an editor; export through send")
            spec = normalise_spec(read_json(args.spec), Path(args.spec).parent) if args.spec else None
            scene, spec = open_session(args.session, spec, args.trusted_factory)
            FigureCanvasAgg(scene.figure)
            result = scene.export(args.output, args.dpi)
            print(json.dumps({"ok": True, "result": result, "audit": scene.audit()}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
