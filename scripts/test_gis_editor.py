"""Reproducible regression suite; all GIS observations are synthetic."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import warnings

import numpy as np
import geopandas as gpd
import rasterio
from affine import Affine
from shapely.geometry import Point, Polygon
from PIL import Image

from scene import EditableScene, read_json, write_json, file_hash
from gis import build_scene, normalise_spec, open_session
from transport import Mailbox, dispatch

ROOT = Path(__file__).resolve().parent.parent


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.demo = normalise_spec(read_json(ROOT / "assets/demo.json"), ROOT / "assets")
        self.scene = build_scene(self.demo, trusted=True)

    def tearDown(self): self.temp.cleanup()

    def vector(self, extension=".geojson", categorical=False):
        path = self.path / ("observations" + extension)
        data = gpd.GeoDataFrame({"value": [10., 20., 30.], "class": ["A", "B", "A"]},
             geometry=[Point(114, 22.5), Point(114.1, 22.6), Point(114.2, 22.55)], crs=4326)
        data.to_file(path, driver={".geojson": "GeoJSON", ".gpkg": "GPKG", ".shp": "ESRI Shapefile"}[extension])
        layer = {"id": "sites", "path": str(path), "field": "class" if categorical else "value",
                 "encoding": "categorical" if categorical else "continuous", "units": "score"}
        if categorical: layer.update(categories=["A", "B"], colors={"A": "#367ca4", "B": "#a87535"})
        return {"mode": "gis", "display_crs": "EPSG:32650", "title": "合成站点测试", "layers": [layer]}

    def test_same_figure_and_locked_values(self):
        original = self.scene.figure
        offsets = self.scene.objects["layer.group_a"].get_offsets().copy()
        self.scene.edit([{"target": "title", "properties": {"text": "修改后的标题", "fontsize": 18}},
                         {"target": "legend", "properties": {"position": [.73, .20]}},
                         {"target": "map.main", "properties": {"framecolor": "#777777"}}], expected_revision=0)
        self.assertIs(self.scene.figure, original)
        np.testing.assert_array_equal(self.scene.objects["layer.group_a"].get_offsets(), offsets)
        self.assertEqual(self.scene.objects["title"].get_text(), "修改后的标题")
        self.assertEqual(self.scene.revision, 1)

    def test_undo_redo(self):
        old = self.scene.objects["title"].get_text()
        self.scene.edit([{"target": "title", "properties": {"text": "after"}}])
        self.scene.undo(); self.assertEqual(self.scene.objects["title"].get_text(), old)
        self.scene.redo(); self.assertEqual(self.scene.objects["title"].get_text(), "after")

    def test_unknown_and_conflict(self):
        before = self.scene.snapshot()
        for patch in [{"xdata": [1, 2]}, {"fontsize": -1}, {"position": [float("nan"), 0]}]:
            with self.assertRaises(ValueError): self.scene.edit([{"target": "title", "properties": patch}])
        with self.assertRaises(ValueError): self.scene.edit([{"target": "title", "properties": {"text": "x"}}], 99)
        self.assertEqual(self.scene.snapshot(), before)

    def test_batch_validation(self):
        text = self.scene.objects["title"].get_text()
        with self.assertRaises(ValueError):
            self.scene.edit([{"target": "title", "properties": {"text": "not applied"}}, {"target": "bad", "properties": {"visible": True}}])
        self.assertEqual(self.scene.objects["title"].get_text(), text)

    def test_font_missing(self):
        with self.assertRaises(ValueError): self.scene.edit([{"target": "title", "properties": {"fontfamily": "DefinitelyMissingFamily123"}}])

    def test_restore_styles(self):
        folder = self.path / "session"
        scene, spec = open_session(folder, self.demo, True)
        scene.edit([{"target": "title", "properties": {"text": "persisted", "position": [.5, .88]}},
                    {"target": "legend", "properties": {"position": [.7, .2]}}])
        scene.save_session(folder, spec)
        rebuilt, _ = open_session(folder, trusted=True)
        self.assertEqual(rebuilt.objects["title"].get_text(), "persisted")
        self.assertEqual(rebuilt.revision, 1)
        np.testing.assert_allclose(rebuilt.properties("legend")["position"], [.7, .2])
        rebuilt.undo(); self.assertNotEqual(rebuilt.objects["title"].get_text(), "persisted")

    def test_changed_source_blocks_restore(self):
        factory = self.path / "factory.py"
        factory.write_bytes((ROOT / "assets/demo_factory.py").read_bytes())
        spec = dict(self.demo, factory=str(factory))
        open_session(self.path / "saved", spec, True)
        factory.write_text(factory.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        with self.assertRaises(ValueError): open_session(self.path / "saved", trusted=True)

    def test_export_dimensions_and_overwrite(self):
        for extension in (".png", ".pdf", ".svg"):
            path = self.path / ("figure" + extension)
            result = self.scene.export(path, dpi=100)
            self.assertEqual(read_json(result["manifest"])["sha256"], file_hash(path))
            with self.assertRaises(FileExistsError): self.scene.export(path)
        with Image.open(self.path / "figure.png") as im: self.assertEqual(im.size, (720, 500))
        self.assertIn("<svg", (self.path / "figure.svg").read_text(encoding="utf-8"))
        self.assertTrue((self.path / "figure.pdf").read_bytes().startswith(b"%PDF"))

    def test_resize_does_not_change_export_size(self):
        self.scene.figure.set_size_inches(10, 8)
        self.scene.export(self.path / 'resized.png', dpi=100)
        with Image.open(self.path / 'resized.png') as im:
            self.assertEqual(im.size, (720, 500))
        np.testing.assert_allclose(self.scene.figure.get_size_inches(), [10, 8])
        self.assertEqual(self.scene.snapshot()['figure']['size_inches'], [7.2, 5.0])

    def test_crs_required(self):
        spec = self.vector()
        spec.pop("display_crs")
        with self.assertRaises(ValueError): build_scene(spec)

    def test_missing_source_crs_required(self):
        path = self.path / "no_crs.shp"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gpd.GeoDataFrame({"v": [1]}, geometry=[Point(114, 22.5)]).to_file(path)
        self.assertTrue(any("'crs' was not provided" in str(w.message) for w in caught))
        spec = {"display_crs": "EPSG:32650", "layers": [{"path": str(path)}]}
        with self.assertRaises(ValueError): build_scene(spec)
        spec["layers"][0].update(source_crs="EPSG:4326", crs_confirmed=True)
        self.assertTrue(build_scene(spec).provenance["results_locked"])

    def test_crs_mismatch_required(self):
        spec = self.vector()
        spec["layers"][0]["source_crs"] = "EPSG:3857"
        with self.assertRaises(ValueError): build_scene(spec)

    def test_vector_formats(self):
        for extension in (".geojson", ".gpkg", ".shp"):
            scene = build_scene(self.vector(extension))
            self.assertEqual(scene.provenance["layers"][0]["count"], 3)
            self.assertIn("colorbar.sites", scene.objects)
            values = scene.objects["layer.sites.0"].get_array().copy()
            scene.edit([{"target": "title", "properties": {"text": "New title"}}])
            np.testing.assert_array_equal(values, scene.objects["layer.sites.0"].get_array())

    def test_categorical_legend(self):
        scene = build_scene(self.vector(categorical=True))
        self.assertIn("legend", scene.objects)
        self.assertEqual([t.get_text() for t in scene.objects["legend"].get_texts()], ["A", "B"])
        with self.assertRaises(ValueError):
            scene.edit([{"target": "layer.sites.0", "properties": {"facecolor": "red"}}])
        scene.edit([{"target": "title", "properties": {"text": "category edit"}}])
        scene.undo()
        self.assertEqual([t.get_text() for t in scene.objects["legend"].get_texts()], ["A", "B"])
        folder = self.path / "category-session"
        scene.save_session(folder, self.vector(categorical=True))
        rebuilt, _ = open_session(folder)
        self.assertEqual(rebuilt.objects["layer.sites.0"].get_cmap().name, scene.objects["layer.sites.0"].get_cmap().name)

    def test_colorbar_synchronisation(self):
        scene = build_scene(self.vector())
        scene.edit([{"target": "layer.sites.0", "properties": {"clim": [0, 40], "cmap": "plasma"}}])
        self.assertEqual(scene.colorbars["layer.sites.0"].mappable.get_clim(), (0, 40))
        scene.undo(); self.assertEqual(scene.colorbars["layer.sites.0"].mappable.get_clim(), (10, 30))

    def test_raster_mask_and_resampling(self):
        path = self.path / "values.tif"
        data = np.arange(100, dtype="float32").reshape(10, 10); data[0, 0] = -9999
        with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32", crs="EPSG:32650",
                           transform=Affine(100, 0, 200000, 0, -100, 2500000), nodata=-9999) as file: file.write(data, 1)
        spec = {"display_crs": "EPSG:32650", "layers": [{"path": str(path), "id": "raster", "resampling": "nearest"}]}
        scene = build_scene(spec)
        array = scene.objects["layer.raster.0"].get_array()
        self.assertTrue(np.ma.getmaskarray(array)[0, 0])
        self.assertEqual(float(array.max()), 99.)
        spec["layers"][0]["resampling"] = "bad"
        with self.assertRaises(ValueError): build_scene(spec)

    def test_invalid_geometry(self):
        path = self.path / "bad.geojson"
        gpd.GeoDataFrame({"x": [1]}, geometry=[Polygon([(114, 22), (115, 23), (115, 22), (114, 23), (114, 22)])], crs=4326).to_file(path)
        with self.assertRaises(ValueError): build_scene({"display_crs": "EPSG:32650", "layers": [{"path": str(path)}]})

    def test_scale_and_north(self):
        spec = self.vector(); spec.update(scale_bar_m=2000, north_arrow=True)
        scene = build_scene(spec)
        self.assertTrue(any(r.get("furniture") == "true_north" for r in scene.provenance["layers"]))
        spec["display_crs"] = "EPSG:4326"
        with self.assertRaises(ValueError): build_scene(spec)

    def test_factory_trust_boundary(self):
        with self.assertRaises(ValueError): build_scene(self.demo)

    def test_mailbox_idempotency(self):
        box = Mailbox(self.path, self.scene, self.demo)
        rid = "a" * 32
        write_json(self.path / "commands" / (rid + ".json"), {"op": "patch", "patches": [{"target": "title", "properties": {"text": "mailbox"}}]})
        box.process(); self.assertTrue(read_json(self.path / "responses" / (rid + ".json"))["ok"])
        box.process(); self.assertEqual(self.scene.revision, 1)
        with self.assertRaises(ValueError): dispatch(self.scene, {"op": "export", "path": str(self.path.parent / "escape.png")}, self.path, self.demo)

    def test_strict_json(self):
        path = self.path / "bad.json"
        for value in ('{"a":1,"a":2}', '{"a":NaN}', "{'a':1}"):
            path.write_text(value, encoding="utf-8")
            with self.assertRaises(ValueError): read_json(path)

    def test_audit_detects_clipping(self):
        self.scene.edit([{"target": "title", "properties": {"position": [2, 2]}}])
        self.assertTrue(any("Clipped" in x for x in self.scene.audit()["issues"]))


if __name__ == "__main__": unittest.main(verbosity=2)
