"""Editable scenes, guarded property patches and reproducible session persistence.
No GUI backend is selected at import time. No pickle/eval/network interface.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import platform
import importlib.metadata
from datetime import datetime, timezone

import numpy as np
from matplotlib.figure import Figure
from matplotlib.text import Text
from matplotlib.legend import Legend
from matplotlib.axes import Axes
from matplotlib.collections import Collection
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.image import AxesImage
from matplotlib import colors, font_manager

VERSION = "1.0.0"


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"Invalid JSON number: {v}")))


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value, name, lower=None, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if lower is not None and value < lower or upper is not None and value > upper:
        raise ValueError(f"{name} is outside [{lower}, {upper}]")
    return float(value)


def vector(value, count, name):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f"{name} needs {count} numbers")
    return [finite(x, name) for x in value]


def font_check(family, text):
    if not isinstance(family, str) or not family.strip():
        raise ValueError("fontfamily must be a non-empty installed family name")
    path = font_manager.findfont(font_manager.FontProperties(family=[family]), fallback_to_default=False)
    cmap = font_manager.get_font(path).get_charmap()
    absent = sorted({ord(c) for c in text if not c.isspace()} - set(cmap))
    if absent:
        raise ValueError("Font lacks glyphs: " + ", ".join(f"U+{x:04X}" for x in absent[:20]))
    return path


def choose_font():
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"):
        try:
            font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
            return name
        except ValueError:
            pass
    return "DejaVu Sans"


def kind_of(artist):
    if isinstance(artist, Legend): return "legend"
    if isinstance(artist, Text): return "text"
    if isinstance(artist, Axes): return "axes"
    if isinstance(artist, AxesImage): return "image"
    if isinstance(artist, Collection): return "collection"
    if isinstance(artist, Line2D): return "line"
    if isinstance(artist, Patch): return "patch"
    raise TypeError(f"Unsupported editable object: {type(artist).__name__}")


COMMON = {"visible", "zorder"}
PROPERTIES = {
    "text": COMMON | {"text", "fontsize", "fontfamily", "color", "fontweight", "position", "ha", "va", "rotation"},
    "legend": COMMON | {"fontsize", "fontfamily", "position", "title", "framealpha", "edgecolor", "facecolor"},
    "axes": COMMON | {"position", "framecolor", "framelinewidth"},
    "image": COMMON | {"cmap", "clim", "alpha"},
    "collection": COMMON | {"cmap", "clim", "alpha", "edgecolor", "linewidth", "facecolor"},
    "line": COMMON | {"color", "linewidth", "alpha", "linestyle"},
    "patch": COMMON | {"edgecolor", "facecolor", "linewidth", "alpha"},
}


class EditableScene:
    """One persistent Figure; style edits never invoke the data/factory builder."""
    def __init__(self, figure, objects=None, provenance=None, colorbars=None):
        if not isinstance(figure, Figure):
            raise TypeError("figure must be a matplotlib.figure.Figure")
        self.figure = figure
        self.export_size_inches = figure.get_size_inches().tolist()
        if not hasattr(figure.canvas, 'get_renderer'):
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            FigureCanvasAgg(figure)
        figure.canvas.draw()
        self.objects = {}
        self.provenance = copy.deepcopy(provenance or {})
        self.provenance.setdefault('software', {'python': platform.python_version(), **{m: importlib.metadata.version(m) for m in ('matplotlib', 'numpy', 'geopandas', 'rasterio', 'pyproj')}})
        self.colorbars = colorbars or {}
        self.locked_properties = {}
        self.warnings = []
        self.revision = 0
        self.undo_stack = []
        self.redo_stack = []
        self.log = []
        self.baseline = None
        for key, obj in (objects or {}).items():
            self.register(key, obj)
        if not self.objects:
            self.discover()
        self.baseline = self.snapshot()

    @classmethod
    def from_figure(cls, figure, objects=None, provenance=None, colorbars=None):
        return cls(figure, objects, provenance, colorbars)

    def register(self, key, artist):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,119}", key):
            raise ValueError("Object IDs must be stable ASCII names, e.g. map.main or title")
        if key in self.objects:
            raise ValueError(f"Duplicate object ID: {key}")
        if artist.figure is not self.figure:
            raise ValueError("Registered object belongs to another Figure")
        kind_of(artist)
        if isinstance(artist, Text) and artist.axes is not None:
            ax = artist.axes
            for axis in (ax.xaxis, ax.yaxis):
                if artist is axis.label:
                    xy = ax.transAxes.inverted().transform(artist.get_transform().transform(artist.get_position()))
                    axis.set_label_coords(float(xy[0]), float(xy[1]))
        artist.set_gid(key)
        self.objects[key] = artist
        try: artist.set_picker(True)
        except Exception: pass
        return artist

    def discover(self):
        """Conservative discovery; project factories should register semantic IDs."""
        for i, ax in enumerate(self.figure.axes):
            self.register(f"axes.{i}", ax)
            if ax.title.get_text(): self.register(f"title.{i}", ax.title)
            if ax.xaxis.label.get_text(): self.register(f"xlabel.{i}", ax.xaxis.label)
            if ax.yaxis.label.get_text(): self.register(f"ylabel.{i}", ax.yaxis.label)
            if ax.get_legend(): self.register(f"legend.{i}", ax.get_legend())
            for j, text in enumerate(ax.texts): self.register(f"annotation.{i}.{j}", text)
        for i, text in enumerate(self.figure.texts): self.register(f"figuretext.{i}", text)
        for i, legend in enumerate(self.figure.legends): self.register(f"figurelegend.{i}", legend)

    def properties(self, key):
        obj = self.objects[key]
        kind = kind_of(obj)
        data = {"visible": bool(obj.get_visible()), "zorder": float(obj.get_zorder())}
        if kind == "text":
            data.update(text=obj.get_text(), fontsize=float(obj.get_fontsize()), fontfamily=obj.get_fontfamily()[0],
                        fontweight=obj.get_fontweight(), color=colors.to_hex(obj.get_color(), keep_alpha=True),
                        position=[float(x) for x in obj.get_position()], ha=obj.get_ha(), va=obj.get_va(), rotation=float(obj.get_rotation()))
        elif kind == "legend":
            texts = obj.get_texts()
            frame = obj.get_frame()
            pos = self.figure.transFigure.inverted().transform(obj.get_window_extent(self.figure.canvas.get_renderer()).get_points()[0])
            data.update(fontsize=float(texts[0].get_fontsize()) if texts else 10.,
                        fontfamily=texts[0].get_fontfamily()[0] if texts else choose_font(),
                        title=obj.get_title().get_text(), position=pos.tolist(),
                        framealpha=frame.get_alpha() if frame.get_alpha() is not None else 1.,
                        edgecolor=colors.to_hex(frame.get_edgecolor(), keep_alpha=True),
                        facecolor=colors.to_hex(frame.get_facecolor(), keep_alpha=True))
        elif kind == "axes":
            spine = next(iter(obj.spines.values()))
            data.update(position=list(obj.get_position().bounds), framecolor=colors.to_hex(spine.get_edgecolor(), keep_alpha=True),
                        framelinewidth=float(spine.get_linewidth()))
        else:
            if "alpha" in PROPERTIES[kind]: data["alpha"] = obj.get_alpha() if obj.get_alpha() is not None else 1.
            if kind in ("image", "collection"):
                data["cmap"] = obj.get_cmap().name
                low, high = obj.get_clim()
                if low is not None and high is not None: data["clim"] = [float(low), float(high)]
            if kind == "line":
                data.update(color=colors.to_hex(obj.get_color(), keep_alpha=True), linewidth=float(obj.get_linewidth()), linestyle=obj.get_linestyle())
            if kind == "patch":
                data.update(facecolor=colors.to_hex(obj.get_facecolor(), keep_alpha=True), edgecolor=colors.to_hex(obj.get_edgecolor(), keep_alpha=True), linewidth=float(obj.get_linewidth()))
            # Collections may contain per-feature colours/widths. Preserve their
            # baseline arrays, do not collapse them to one colour in snapshots.
            if kind == "collection":
                data.update(facecolor=obj.get_facecolors().tolist(), edgecolor=obj.get_edgecolors().tolist(), linewidth=obj.get_linewidths().tolist())
        return data

    def snapshot(self):
        return {"objects": {key: self.properties(key) for key in self.objects}, "figure": {"size_inches": list(self.export_size_inches)}}

    def allowed_properties(self, key):
        allowed = PROPERTIES[kind_of(self.objects[key])] - set(self.locked_properties.get(key, []))
        if getattr(self.objects[key], 'arrow_patch', None) is not None:
            allowed -= {'position', 'rotation'}
        return allowed

    def list_objects(self):
        return [{"id": key, "kind": kind_of(obj), "allowed": sorted(self.allowed_properties(key)), "properties": self.properties(key),
                 "coordinates": "figure_fraction" if isinstance(obj, (Legend, Axes)) else "artist_transform"}
                for key, obj in self.objects.items()]

    def _validate(self, key, patch, internal=False):
        if key not in self.objects: raise ValueError(f"Unknown object: {key}")
        if not isinstance(patch, dict) or not patch: raise ValueError("patch must be a non-empty object")
        obj = self.objects[key]
        kind = kind_of(obj)
        unknown = set(patch) - (PROPERTIES[kind] if internal else self.allowed_properties(key))
        if unknown: raise ValueError(f"Unsupported properties for {kind}: {sorted(unknown)}")
        result = copy.deepcopy(patch)
        for name, value in result.items():
            if name == "visible" and not isinstance(value, bool): raise ValueError("visible must be boolean")
            if name in {"fontsize", "linewidth", "framelinewidth", "rotation", "zorder", "alpha", "framealpha"}:
                if internal and kind == "collection" and name == "linewidth": continue
                limits = (1, 200) if name == "fontsize" else (0, 1) if name in {"alpha", "framealpha"} else (0, 20) if "linewidth" in name else (-10000, 10000)
                result[name] = finite(value, name, *limits)
            if name in {"color", "edgecolor", "facecolor", "framecolor"}:
                if not (internal and kind == "collection" and isinstance(value, list)):
                    if not colors.is_color_like(value): raise ValueError(f"Invalid colour: {value}")
            if name in {"text", "title"} and (not isinstance(value, str) or len(value) > 10000): raise ValueError("text/title must be a bounded string")
            if name == "position":
                p = vector(value, 4 if kind == "axes" else 2, name)
                if kind == "axes" and (p[2] <= 0 or p[3] <= 0 or min(p) < 0 or p[0] + p[2] > 1.001 or p[1] + p[3] > 1.001):
                    raise ValueError("Axes frame must fit within figure fractions [x,y,width,height]")
                if kind == "legend" and any(x < 0 or x > 1 for x in p): raise ValueError("Legend anchor must fit within the figure")
                result[name] = p
            if name == "clim":
                p = vector(value, 2, name)
                if p[0] >= p[1]: raise ValueError("clim needs min < max")
                result[name] = p
            if name == "cmap":
                import matplotlib
                if not isinstance(value, str): raise ValueError("Unknown colour map")
                if value not in matplotlib.colormaps and not (internal and value == obj.get_cmap().name):
                    raise ValueError("Unknown colour map")
            if name == "ha" and value not in {"left", "center", "right"}: raise ValueError("Invalid ha")
            if name == "va" and value not in {"top", "center", "bottom", "baseline", "center_baseline"}: raise ValueError("Invalid va")
            if name == "fontweight" and value not in {"normal", "bold", "light", "medium", "semibold", "heavy", "black", 100, 200, 300, 400, 500, 600, 700, 800, 900}: raise ValueError("Invalid fontweight")
            if name == "linestyle" and value not in {"-", "--", "-.", ":", "", "None", "none"}: raise ValueError("Invalid linestyle")
        if kind in {"text", "legend"} and ("fontfamily" in result or "text" in result or "title" in result):
            text = result.get("text", obj.get_text()) if kind == "text" else result.get("title", obj.get_title().get_text()) + "".join(t.get_text() for t in obj.get_texts())
            family = result.get("fontfamily", self.properties(key)["fontfamily"])
            font_check(family, text)
        return result

    def _apply(self, key, patch):
        obj = self.objects[key]
        kind = kind_of(obj)
        for name, value in patch.items():
            if name in COMMON: getattr(obj, f"set_{name}")(value)
            elif kind == "text":
                getattr(obj, "set_" + name)(value)
                if name == "position" and obj.axes is not None and obj is obj.axes.title:
                    obj.axes._autotitlepos = False  # stop Matplotlib repositioning a dragged axes title
            elif kind == "legend":
                if name == "position":
                    obj.set_loc("lower left")
                    obj.set_bbox_to_anchor(value, transform=self.figure.transFigure)
                    obj.borderaxespad = 0
                elif name in {"fontfamily", "fontsize"}:
                    for text in list(obj.get_texts()) + [obj.get_title()]: getattr(text, "set_" + name)(value)
                elif name == "title": obj.set_title(value)
                else: getattr(obj.get_frame(), "set_" + ("alpha" if name == "framealpha" else name))(value)
            elif kind == "axes":
                if name == "position":
                    self.figure.set_layout_engine(None)
                    obj.set_position(value)
                else:
                    for spine in obj.spines.values(): getattr(spine, "set_" + ("edgecolor" if name == "framecolor" else "linewidth"))(value)
            elif name == "clim": obj.set_clim(*value)
            elif name == "cmap" and value == obj.get_cmap().name: pass
            else: getattr(obj, "set_" + name)(value)
        if key in self.colorbars:
            self.colorbars[key].update_normal(obj)

    def restore(self, state):
        if set(state.get("objects", {})) != set(self.objects): raise ValueError("Object registry differs from saved scene; do not guess IDs")
        self.export_size_inches = vector(state["figure"]["size_inches"], 2, "size_inches")
        if min(self.export_size_inches) <= 0: raise ValueError("Export size must be positive")
        for key, patch in state["objects"].items():
            validated = self._validate(key, patch, internal=True)
            self._apply(key, validated)
        self.figure.canvas.draw()

    def edit(self, patches, expected_revision=None):
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError(f"Revision conflict: expected {expected_revision}, current {self.revision}")
        if not isinstance(patches, list) or not patches or len(patches) > 100:
            raise ValueError("patches must contain 1..100 object patches")
        validated = [(item["target"], self._validate(item["target"], item["properties"])) for item in patches]
        before = self.snapshot()
        try:
            for key, patch in validated: self._apply(key, patch)
            self.figure.canvas.draw()
        except Exception:
            self.restore(before)
            raise
        after = self.snapshot()
        self.undo_stack.append(before)
        self.redo_stack.clear()
        self.revision += 1
        self.log.append({"revision": self.revision, "utc": datetime.now(timezone.utc).isoformat(), "patches": patches})
        return after

    def undo(self):
        if not self.undo_stack: raise ValueError("Nothing to undo")
        current = self.snapshot()
        prior = self.undo_stack[-1]
        self.restore(prior)
        self.undo_stack.pop()
        self.redo_stack.append(current)
        self.revision += 1

    def redo(self):
        if not self.redo_stack: raise ValueError("Nothing to redo")
        current = self.snapshot()
        following = self.redo_stack[-1]
        self.restore(following)
        self.redo_stack.pop()
        self.undo_stack.append(current)
        self.revision += 1

    def audit(self):
        self.figure.canvas.draw()
        renderer = self.figure.canvas.get_renderer() if hasattr(self.figure.canvas, "get_renderer") else None
        issues = list(self.warnings)
        if renderer:
            frame = self.figure.bbox
            boxes = []
            for key, obj in self.objects.items():
                if not obj.get_visible() or kind_of(obj) not in {"text", "legend"}: continue
                try:
                    box = obj.get_window_extent(renderer)
                    if box.width == 0 or box.height == 0: continue
                    if box.x0 < frame.x0 - 1 or box.y0 < frame.y0 - 1 or box.x1 > frame.x1 + 1 or box.y1 > frame.y1 + 1:
                        issues.append(f"Clipped furniture: {key}")
                    for other, prior in boxes:
                        if box.overlaps(prior): issues.append(f"Furniture overlap: {other} / {key}")
                    boxes.append((key, box))
                except (AttributeError, ValueError):
                    issues.append(f"Extent unavailable: {key}")
        return {"revision": self.revision, "issues": list(dict.fromkeys(issues)),
                "scope": "registered furniture clipping/overlap plus GIS preflight; inspect map evidence and final-size output manually"}

    def export(self, path, dpi=300, overwrite=False):
        path = Path(path).resolve()
        if path.suffix.lower() not in {".png", ".pdf", ".svg"}: raise ValueError("Export supports PNG/PDF/SVG")
        finite(dpi, "dpi", 36, 1200)
        sidecar = path.with_suffix(path.suffix + ".manifest.json")
        if not overwrite and (path.exists() or sidecar.exists()): raise FileExistsError("Export/manifest already exists; choose a new name")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".render-", suffix=path.suffix, dir=path.parent)
        os.close(fd)
        import matplotlib
        current_size = self.figure.get_size_inches().copy()
        try:
            self.figure.set_size_inches(self.export_size_inches)
            with matplotlib.rc_context({"svg.fonttype": "none", "pdf.fonttype": 42, "savefig.bbox": None}):
                self.figure.savefig(tmp, dpi=dpi, bbox_inches=None, facecolor=self.figure.get_facecolor())
            os.replace(tmp, path)
        finally:
            self.figure.set_size_inches(current_size)
            self.figure.canvas.draw()
            if Path(tmp).exists(): Path(tmp).unlink()
        write_json(sidecar, {"version": VERSION, "revision": self.revision, "dpi": dpi,
                            "size_inches": list(self.export_size_inches), "provenance": self.provenance,
                            "state": self.snapshot(), "audit": self.audit(), "sha256": file_hash(path)})
        return {"path": str(path), "manifest": str(sidecar), "revision": self.revision}

    def save_session(self, folder, spec):
        folder = Path(folder)
        write_json(folder / "session.json", {"version": VERSION, "spec": spec, "provenance": self.provenance,
                   "revision": self.revision, "state": self.snapshot(), "baseline": self.baseline,
                   "undo": self.undo_stack[-100:], "redo": self.redo_stack[-100:], "log": self.log})
