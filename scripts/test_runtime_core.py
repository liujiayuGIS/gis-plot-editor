"""Headless export/transaction/locking regression tests; no external GIS data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image

from scene import EditableScene, read_json, write_json, file_hash
from session_lock import SessionLock
from transport import Mailbox, dispatch


def example_scene():
    fig = Figure(figsize=(3, 2.5), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([.18, .2, .65, .6])
    points = ax.scatter([0, 1, 2], [0, 1, .5], c=[10, 50, 100], vmin=10, vmax=100)
    title = fig.text(.5, .95, "Example", ha="center", va="top", fontfamily="DejaVu Sans")
    scene = EditableScene(fig, {"title": title, "map.main": ax, "layer.sites.0": points})
    scene.metric_values = {"layer.sites.0": np.array([10., 50., 100.])}
    return scene


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.scene = example_scene()

    def tearDown(self): self.temp.cleanup()

    def test_raster_exports_dimensions_dpi_and_format(self):
        for index, (suffix, fmt) in enumerate([(".png", "PNG"), (".jpg", "JPEG"), (".jpeg", "JPEG"), (".tif", "TIFF"), (".tiff", "TIFF"), (".TIFF", "TIFF")]):
            with self.subTest(suffix=suffix):
                path = self.path / (f"map{index}" + suffix)
                result = self.scene.export(path, dpi=200)
                with Image.open(path) as im:
                    self.assertEqual(im.format, fmt)
                    self.assertEqual(im.size, (600, 500))
                    self.assertAlmostEqual(im.info["dpi"][0], 200, delta=.1)
                    if fmt == "JPEG":
                        self.assertEqual(im.mode, "RGB")
                        self.assertTrue(all(x > 245 for x in im.getpixel((0, 0))))
                    if fmt == "TIFF": self.assertEqual(im.tag_v2[259], 5)  # lossless LZW
                manifest = read_json(result["manifest"])
                self.assertEqual(manifest["sha256"], file_hash(path))
                self.assertEqual(manifest["size_inches"], [3., 2.5])
                with self.assertRaises(FileExistsError): self.scene.export(path)

    def test_vector_exports(self):
        self.scene.export(self.path / "map.pdf")
        self.scene.export(self.path / "map.svg")
        self.assertTrue((self.path / "map.pdf").read_bytes().startswith(b"%PDF"))
        self.assertIn("<svg", (self.path / "map.svg").read_text())

    def test_transparency_and_options(self):
        for suffix in (".png", ".tiff"):
            path = self.path / ("transparent" + suffix)
            self.scene.export(path, transparent=True)
            with Image.open(path) as im: self.assertEqual(im.convert("RGBA").getpixel((0, 0))[3], 0)
        for kwargs in ({"transparent": True}, {"jpeg_quality": 0}, {"jpeg_quality": True}, {"tiff_compression": "bad"}):
            with self.assertRaises(ValueError): self.scene.export(self.path / "bad.jpg", **kwargs)
        with self.assertRaises(ValueError): self.scene.export(self.path / "bad.gif")

    def test_export_audits_final_size_and_restores_preview(self):
        self.scene.figure.set_size_inches([6, 5])
        with patch.object(self.scene, "audit", wraps=self.scene.audit) as audit:
            self.scene.export(self.path / "map.png")
            self.assertEqual(audit.call_count, 1)
        np.testing.assert_allclose(self.scene.figure.get_size_inches(), [6, 5])

    def test_color_scale_reports_and_undo(self):
        self.scene.edit([{"target": "layer.sites.0", "properties": {"clim": [20, 80]}}])
        report = self.scene.audit()
        self.assertEqual((report["color_scales"][0]["below"], report["color_scales"][0]["above"]), (1, 1))
        self.assertTrue(any("Colour scale clips" in x for x in report["issues"]))
        self.scene.undo()
        self.assertEqual(self.scene.audit()["color_scales"][0]["below"], 0)

    def test_style_template_preserves_semantics_and_undo(self):
        self.scene.edit([{"target": "title", "properties": {"fontsize": 16, "text": "Source title"}},
                         {"target": "layer.sites.0", "properties": {"clim": [20, 80]}}])
        template = self.scene.style_template(True)
        other = example_scene()
        other.apply_style_template(template, 0)
        self.assertEqual(other.properties("title")["text"], "Example")
        self.assertEqual(other.properties("title")["fontsize"], 16)
        self.assertEqual(other.properties("layer.sites.0")["clim"], [10, 100])
        other.undo()
        self.assertNotEqual(other.properties("title")["fontsize"], 16)
        other.apply_style_template(template, other.revision, True)
        self.assertEqual(other.properties("layer.sites.0")["clim"], [20, 80])
        template["objects"]["title"]["properties"]["text"] = "Overwrite"
        before = other.snapshot()
        with self.assertRaises(ValueError): other.apply_style_template(template, other.revision)
        self.assertEqual(before, other.snapshot())

    def test_mutations_require_revision(self):
        for op in ("patch", "undo", "redo", "style_apply"):
            with self.assertRaises(ValueError): dispatch(self.scene, {"op": op}, self.path, {})

    def test_committed_command_recovers_missing_response(self):
        box = Mailbox(self.path, self.scene, {})
        rid = "a" * 32
        write_json(self.path / "commands" / (rid + ".json"),
                   {"op": "patch", "expected_revision": 0, "patches": [{"target": "title", "properties": {"text": "Changed"}}]})
        original = write_json
        def interrupted_write(path, value):
            if Path(path).parent.name == "responses": raise KeyboardInterrupt("simulated exit after commit")
            original(path, value)
        with patch("transport.write_json", side_effect=interrupted_write):
            with self.assertRaises(KeyboardInterrupt): box.process()
        saved = read_json(self.path / "session.json")
        rebuilt = example_scene(); rebuilt.restore(saved["state"])
        rebuilt.revision = saved["revision"]; rebuilt.processed_requests = saved["processed_requests"]
        Mailbox(self.path, rebuilt, {}).process()
        self.assertEqual(rebuilt.revision, 1)
        self.assertTrue(read_json(self.path / "responses" / (rid + ".json"))["ok"])

    def test_session_write_failure_rolls_back_mutation(self):
        box = Mailbox(self.path, self.scene, {})
        write_json(self.path / "commands" / ("b" * 32 + ".json"),
                   {"op": "patch", "expected_revision": 0, "patches": [{"target": "title", "properties": {"text": "Unsaved"}}]})
        with patch.object(self.scene, "save_session", side_effect=OSError("disk full")): box.process()
        self.assertEqual(self.scene.revision, 0)
        self.assertEqual(self.scene.objects["title"].get_text(), "Example")
        self.assertEqual(self.scene.processed_requests, {})

    def test_lock_blocks_other_process_despite_stale_heartbeat(self):
        write_json(self.path / "runtime.json", {"state": "running", "heartbeat": 0})
        code = "import sys; from session_lock import SessionLock; lock=SessionLock(sys.argv[1]); lock.release()"
        with SessionLock(self.path):
            result = subprocess.run([sys.executable, "-c", code, str(self.path)], cwd=Path(__file__).parent, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], cwd=Path(__file__).parent, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.path / "editor.lock").exists())

    def test_export_command_options(self):
        result = dispatch(self.scene, {"op": "export", "path": "map.tiff", "dpi": 150, "tiff_compression": "raw"}, self.path, {})
        with Image.open(result["path"]) as im:
            self.assertEqual(im.format, "TIFF")
            self.assertEqual(im.tag_v2[259], 1)

    def test_lock_released_by_process_exit(self):
        code = "import os,sys; from session_lock import SessionLock; lock=SessionLock(sys.argv[1]); os._exit(0)"
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], cwd=Path(__file__).parent, capture_output=True)
        self.assertEqual(result.returncode, 0)
        with SessionLock(self.path): pass

    def test_factory_restore_keeps_command_receipts(self):
        from gis import open_session
        factory = self.path / "factory.py"
        factory.write_text("from matplotlib.figure import Figure\nfrom scene import EditableScene\ndef build_scene(params):\n f=Figure(figsize=(3,2.5)); t=f.text(.5,.9,'Example'); return EditableScene(f,{'title':t})\n")
        spec = {"mode": "factory", "factory": str(factory), "params": {}}
        scene, spec = open_session(self.path, spec, trusted=True)
        box = Mailbox(self.path, scene, spec)
        rid = "c" * 32
        write_json(self.path / "commands" / (rid + ".json"),
                   {"op": "patch", "expected_revision": 0, "patches": [{"target": "title", "properties": {"text": "Restored"}}]})
        box.process()
        (self.path / "responses" / (rid + ".json")).unlink()
        rebuilt, spec = open_session(self.path, trusted=True)
        Mailbox(self.path, rebuilt, spec).process()
        self.assertEqual(rebuilt.revision, 1)
        self.assertEqual(rebuilt.objects["title"].get_text(), "Restored")


if __name__ == "__main__": unittest.main(verbosity=2)
