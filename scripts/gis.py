"""GIS files and explicitly trusted local Figure factories -> EditableScene."""
from __future__ import annotations
import copy
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import Normalize, BoundaryNorm, ListedColormap
from matplotlib import colormaps
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from pyproj import CRS, Transformer

from scene import EditableScene, choose_font, file_hash, finite, font_check


def resolve_path(path, base):
    if not isinstance(path, str) or not path: raise ValueError("A local path is required")
    if "://" in path: raise ValueError("Remote resources are not accepted; use an approved local file")
    return (Path(base) / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()


def normalise_spec(spec, base):
    spec = copy.deepcopy(spec)
    if not isinstance(spec, dict): raise ValueError("spec must be an object")
    if spec.get("mode", "gis") == "factory":
        spec["factory"] = str(resolve_path(spec["factory"], base))
        spec["data_files"] = [str(resolve_path(p, base)) for p in spec.get("data_files", [])]
    else:
        spec["mode"] = "gis"
        if not isinstance(spec.get("layers"), list) or not spec["layers"]: raise ValueError("At least one GIS layer is required")
        for layer in spec["layers"]: layer["path"] = str(resolve_path(layer["path"], base))
    return spec


def inputs(spec):
    paths = [spec["factory"]] + spec.get("data_files", []) if spec.get("mode") == "factory" else [layer["path"] for layer in spec["layers"]]
    expanded = []
    for value in paths:
        path = Path(value)
        if not path.is_file(): raise FileNotFoundError(str(path))
        expanded.append(path)
        if path.suffix.lower() == ".shp":
            required = [path.with_suffix(s) for s in (".shx", ".dbf")]
            if not all(p.exists() for p in required): raise ValueError("Shapefile requires .shp/.shx/.dbf siblings")
            expanded.extend(required)
            expanded.extend(p for p in (path.with_suffix(".prj"), path.with_suffix(".cpg")) if p.exists())
    return {str(p): file_hash(p) for p in dict.fromkeys(expanded)}


def checked_crs(actual, layer):
    declared = layer.get("source_crs")
    if actual is None:
        if not declared or layer.get("crs_confirmed") is not True:
            raise ValueError(f"CRS missing for {layer['path']}; confirm source_crs and crs_confirmed=true, do not guess EPSG:4326")
        return CRS.from_user_input(declared)
    actual = CRS.from_user_input(actual)
    if declared and not actual.equals(CRS.from_user_input(declared)):
        raise ValueError("source_crs conflicts with file metadata; repair metadata explicitly before plotting")
    return actual


def check_geographic_bounds(bounds, crs):
    if crs.is_geographic:
        xmin, ymin, xmax, ymax = bounds
        if xmin < -180.0001 or xmax > 180.0001 or ymin < -90.0001 or ymax > 90.0001:
            raise ValueError("Geographic x/y out of longitude/latitude range; verify axis order and source CRS")
        if xmax - xmin > 180:
            raise ValueError("Antimeridian-crossing data requires explicit splitting; this local editor does not silently wrap it")


def build_gis(spec):
    import geopandas as gpd
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import array_bounds

    hashes = inputs(spec)
    if not spec.get("display_crs"): raise ValueError("display_crs must be explicitly selected for the map purpose")
    target = CRS.from_user_input(spec["display_crs"])
    width, height = spec.get("size_mm", [180, 130])
    finite(width, "width_mm", 50, 1000); finite(height, "height_mm", 50, 1000)
    dpi = int(spec.get("preview_dpi", 110))
    finite(dpi, "preview_dpi", 36, 300)
    font = spec.get("font", choose_font())
    font_check(font, spec.get("title", ""))
    fig = Figure(figsize=(width / 25.4, height / 25.4), dpi=dpi, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([.10, .14, .70, .72])
    ax.set_aspect("equal", adjustable="box")
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Longitude (°)" if target.is_geographic else f"X ({target.axis_info[0].unit_name})", fontfamily=font)
    ax.set_ylabel("Latitude (°)" if target.is_geographic else f"Y ({target.axis_info[1].unit_name})", fontfamily=font)
    title = fig.text(.5, .95, spec.get("title", ""), ha="center", va="top", fontsize=13, fontfamily=font)
    objects = {"map.main": ax, "title": title, "map.xlabel": ax.xaxis.label, "map.ylabel": ax.yaxis.label}
    colorbars, records, notices, handles = {}, [], [], []
    layer_ids = set()
    for index, layer in enumerate(spec["layers"]):
        lid = layer.get("id", f"layer{index}")
        if lid in layer_ids: raise ValueError("Layer IDs must be unique")
        layer_ids.add(lid)
        path = Path(layer["path"])
        suffix = path.suffix.lower()
        zorder = finite(layer.get("zorder", index + 1), "zorder", -100, 1000)
        opacity = finite(layer.get("alpha", 1.), "alpha", 0, 1)
        cmap_name = layer.get("cmap", "viridis")
        if cmap_name not in colormaps: raise ValueError("Unknown colormap")
        record = {"id": lid, "path": str(path), "sha256": hashes[str(path)], "source": layer.get("source", "user local file")}
        if suffix in {".geojson", ".json", ".shp", ".gpkg"}:
            data = gpd.read_file(path, **({"layer": layer["layer"]} if layer.get("layer") else {}))
            source = checked_crs(data.crs, layer)
            if data.crs is None: data = data.set_crs(source)
            if data.empty: raise ValueError(f"Empty vector layer: {lid}")
            missing = data.geometry.isna() | data.geometry.is_empty
            invalid = ~data.geometry.is_valid & ~missing
            if missing.any() or invalid.any(): raise ValueError(f"{lid}: {int(missing.sum())} missing/empty and {int(invalid.sum())} invalid geometries; resolve explicitly")
            if not np.isfinite(data.total_bounds).all(): raise ValueError("Non-finite geometry bounds")
            check_geographic_bounds(data.total_bounds, source)
            projected = data.to_crs(target)
            if not np.isfinite(projected.total_bounds).all(): raise ValueError("Non-finite transformed geometry; target CRS may be unsuitable")
            field = layer.get("field")
            start = len(ax.collections)
            kwargs = {"ax": ax, "alpha": opacity, "zorder": zorder, "edgecolor": layer.get("edgecolor", "#596673"),
                      "linewidth": finite(layer.get("linewidth", .5), "linewidth", 0, 10), "markersize": finite(layer.get("point_size", 20), "point_size", 1, 5000),
                      "aspect": None}
            mappable = None
            if not field:
                projected.plot(color=layer.get("color", "#83a8b6"), **kwargs)
                handles.append(Patch(facecolor=layer.get("color", "#83a8b6"), label=layer.get("label", lid)))
            else:
                if field not in data: raise ValueError(f"Missing field {field}")
                encoding = layer.get("encoding")
                if encoding not in {"continuous", "categorical"}: raise ValueError("Field requires explicit encoding: continuous or categorical")
                values = data[field]
                record["missing_values"] = int(values.isna().sum())
                if encoding == "categorical":
                    categories = layer.get("categories")
                    mapping = layer.get("colors")
                    if not categories or not mapping or not set(str(x) for x in values.dropna()).issubset(set(categories)):
                        raise ValueError("Categorical encoding requires explicit categories (string order) and complete colors mapping")
                    if set(mapping) != set(categories): raise ValueError("Colors must cover exactly the categories")
                    cat_values = values.map(lambda x: categories.index(str(x)) if not __import__('pandas').isna(x) else np.nan)
                    color_map = ListedColormap([mapping[c] for c in categories])
                    norm = BoundaryNorm(np.arange(len(categories) + 1) - .5, len(categories))
                    projected.assign(__plotvalue=cat_values).plot(column="__plotvalue", cmap=color_map, norm=norm,
                      missing_kwds={"color": layer.get("missing_color", "#d6d6d6"), "label": "No data"}, **kwargs)
                    handles.extend(Patch(facecolor=mapping[c], label=c) for c in categories)
                    if values.isna().any(): handles.append(Patch(facecolor=layer.get("missing_color", "#d6d6d6"), label="No data"))
                    record.update(field=field, encoding=encoding, categories=categories, colors=mapping)
                else:
                    try: numeric = values.to_numpy(dtype=float)
                    except (ValueError, TypeError): raise ValueError("Continuous field must be numeric")
                    valid = numeric[np.isfinite(numeric)]
                    if not len(valid): raise ValueError("No finite metric values")
                    if np.isinf(numeric).any(): raise ValueError("Infinite metric values must be resolved explicitly")
                    low, high = layer.get("clim", [float(valid.min()), float(valid.max())])
                    if low == high: low, high = low - .5, high + .5; notices.append(f"{lid}: constant field; display scale padded, metrics unchanged")
                    if not math.isfinite(low + high) or low >= high: raise ValueError("Invalid clim")
                    projected.plot(column=field, cmap=cmap_name, norm=Normalize(low, high),
                        missing_kwds={"color": layer.get("missing_color", "#d6d6d6"), "label": "No data"}, **kwargs)
                    record.update(field=field, encoding=encoding, range=[float(valid.min()), float(valid.max())], clim=[low, high], units=layer.get("units", "unspecified"))
                    if not layer.get("units"): notices.append(f"{lid}: metric units unspecified; confirm before publication")
                    mappable = next((c for c in ax.collections[start:] if c.get_array() is not None), None)
            added = ax.collections[start:]
            for j, obj in enumerate(added): objects[f"layer.{lid}.{j}"] = obj
            record.update(source_crs=source.to_wkt(), count=len(data), bounds=list(projected.total_bounds))
            if mappable is not None:
                key = next(key for key, obj in objects.items() if obj is mappable)
                cax = fig.add_axes([.85, .20 + .15 * len(colorbars), .025, .48])
                bar = fig.colorbar(mappable, cax=cax)
                bar.set_label(f"{field} ({layer.get('units', 'units unspecified')})", fontfamily=font)
                colorbars[key] = bar
                objects[f"colorbar.{lid}"] = cax
                objects[f"colorbarlabel.{lid}"] = cax.yaxis.label
        elif suffix in {".tif", ".tiff"}:
            with rasterio.open(path) as source_file:
                source = checked_crs(source_file.crs, layer)
                check_geographic_bounds(source_file.bounds, source)
                band = layer.get("band", 1)
                if not isinstance(band, int) or isinstance(band, bool) or band < 1 or band > source_file.count: raise ValueError("Raster band out of range")
                method = layer.get("resampling")
                if method not in {"nearest", "bilinear"}: raise ValueError("Raster requires explicit resampling=nearest or bilinear")
                if layer.get("encoding") == "categorical":
                    raise ValueError("Categorical raster requires an explicit discrete legend factory; do not render it as continuous")
                max_edge = int(layer.get("preview_max_edge", 1600))
                finite(max_edge, "preview_max_edge", 100, 4000)
                with WarpedVRT(source_file, src_crs=source, crs=target, resampling=getattr(Resampling, method)) as vrt:
                    scale = min(1., max_edge / max(vrt.width, vrt.height))
                    h, w = max(1, round(vrt.height * scale)), max(1, round(vrt.width * scale))
                    values = vrt.read(band, out_shape=(h, w), masked=True, resampling=getattr(Resampling, method))
                    values = np.ma.masked_invalid(values)
                    if values.count() == 0: raise ValueError("Raster contains no finite unmasked values")
                    from affine import Affine
                    resize = Affine.scale(vrt.width / w, vrt.height / h)
                    transform = vrt.transform @ resize if hasattr(vrt.transform, '__matmul__') else vrt.transform * resize
                    left, bottom, right, top = array_bounds(h, w, transform)
                    low, high = layer.get("clim", [float(values.min()), float(values.max())])
                    if low == high: low, high = low - .5, high + .5
                    if not math.isfinite(low + high) or low >= high: raise ValueError("Invalid raster clim")
                    im = ax.imshow(values, extent=[left, right, bottom, top], cmap=cmap_name, vmin=low, vmax=high,
                                   interpolation="nearest", alpha=opacity, zorder=zorder)
                    key = f"layer.{lid}.0"
                    objects[key] = im
                    cax = fig.add_axes([.85, .20 + .15 * len(colorbars), .025, .48])
                    bar = fig.colorbar(im, cax=cax)
                    bar.set_label(layer.get("label", f"{lid} ({layer.get('units', 'units unspecified')})"), fontfamily=font)
                    colorbars[key] = bar
                    objects[f"colorbar.{lid}"] = cax
                    objects[f"colorbarlabel.{lid}"] = cax.yaxis.label
                    record.update(source_crs=source.to_wkt(), band=band, source_shape=[source_file.height, source_file.width],
                                  display_shape=[h, w], resampling=method, range=[float(values.min()), float(values.max())],
                                  bounds=[left, bottom, right, top], nodata=source_file.nodata if source_file.nodata is None or math.isfinite(source_file.nodata) else "nonfinite")
                    if scale < 1: notices.append(f"{lid}: preview downsampled to {w}x{h}; high DPI export does not restore source raster detail")
        else: raise ValueError(f"Unsupported GIS format: {suffix}")
        records.append(record)
    if len(colorbars) > 1:
        raise ValueError("Multiple continuous layers need an explicit multi-panel factory; do not overlap colorbars")
    if spec.get("extent"):
        extent = spec["extent"]
        if not isinstance(extent, list) or len(extent) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in extent): raise ValueError("extent=[xmin,ymin,xmax,ymax] in display CRS")
        if extent[0] >= extent[2] or extent[1] >= extent[3]: raise ValueError("Invalid extent")
        ax.set_xlim(extent[0], extent[2]); ax.set_ylim(extent[1], extent[3])
        for rec in records:
            b = rec["bounds"]
            if b[0] < extent[0] or b[1] < extent[1] or b[2] > extent[2] or b[3] > extent[3]: notices.append(f"{rec['id']}: viewport clips layer bounds; confirm intentional study-area crop")
    if handles:
        legend = fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(.78, .66), borderaxespad=0, fontsize=8,
                            prop={"family": font, "size": 8})
        objects["legend"] = legend
    if target.is_geographic: notices.append("Geographic display has position-dependent scale; no constant metre scale bar added")
    if spec.get("north_arrow"):
        fig.canvas.draw()
        origin = [.92, .80]
        x, y = ax.transData.inverted().transform(ax.transAxes.transform(origin))
        inverse = Transformer.from_crs(target, target.geodetic_crs, always_xy=True)
        forward = Transformer.from_crs(target.geodetic_crs, target, always_xy=True)
        lon, lat = inverse.transform(x, y)
        nx, ny = forward.transform(lon, min(89.999, lat + .01))
        delta = ax.transData.transform([nx, ny]) - ax.transData.transform([x, y])
        length = np.linalg.norm(delta)
        if not np.isfinite(length) or length < 1e-12: raise ValueError("True north direction cannot be resolved")
        delta = delta / length * (.065 * ax.bbox.height)
        endpoint = ax.transAxes.inverted().transform(ax.transAxes.transform(origin) + delta)
        arrow = ax.annotate("N", xy=endpoint, xytext=origin, xycoords="axes fraction", textcoords="axes fraction",
                         arrowprops={"arrowstyle": "->", "color": "#283d48"}, ha="center", fontfamily=font)
        # Deliberately not editable: moving only its text would invalidate orientation.
        records.append({"furniture": "true_north", "evaluated_at": [lon, lat], "placement": origin})
    if spec.get("scale_bar_m") is not None:
        if not target.is_projected or not target.axis_info: raise ValueError("Scale bar requires a projected CRS and declared local length")
        metres = finite(spec["scale_bar_m"], "scale_bar_m", .001, 1e8)
        conversion = target.axis_info[0].unit_conversion_factor
        ymin, ymax = ax.get_ylim(); xmin, xmax = ax.get_xlim()
        x, y = xmin + .08 * (xmax - xmin), ymin + .10 * (ymax - ymin)
        length = metres / conversion
        if length > .4 * (xmax - xmin): raise ValueError("Scale bar is too long for viewport")
        inv = Transformer.from_crs(target, target.geodetic_crs, always_xy=True)
        lon1, lat1 = inv.transform(x, y); lon2, lat2 = inv.transform(x + length, y)
        actual = target.get_geod().inv(lon1, lat1, lon2, lat2)[2]
        if not math.isfinite(actual) or abs(actual / metres - 1) > .02: raise ValueError("Local projection scale distortion exceeds 2%; choose a suitable local CRS/scale method")
        ax.plot([x, x + length], [y, y], color="#283d48", lw=2, zorder=100)
        ax.text(x + length / 2, y, f"{metres / 1000:g} km" if metres >= 1000 else f"{metres:g} m", ha="center", va="bottom", fontfamily=font, fontsize=8)
        records.append({"furniture": "scale_bar", "metres": metres, "local_geodesic_metres": actual})
    fig.canvas.draw()
    scene = EditableScene(fig, objects, {"data_hashes": hashes, "display_crs_wkt": target.to_wkt(),
                                          "layers": records, "results_locked": True,
                                          "boundary_review": "User must verify source/version and publication requirements; not an approval certificate"}, colorbars)
    scene.warnings = notices
    for key, obj in scene.objects.items():
        if key.startswith('layer.'):
            # Named categories and single-colour layers have proxy legend keys.
            # Freeze their colour properties instead of permitting legend drift.
            record = next((r for r in records if key.startswith('layer.' + r.get('id', '') + '.')), None)
            if record and record.get('encoding') != 'continuous' and not isinstance(obj, __import__('matplotlib').image.AxesImage):
                scene.locked_properties[key] = {'facecolor', 'edgecolor', 'cmap', 'clim'}
    return scene


def build_factory(spec, trusted=False):
    if not trusted: raise ValueError("Local Python factory executes code; inspect it and explicitly pass --trusted-factory")
    hashes = inputs(spec)
    path = Path(spec["factory"])
    if path.suffix.lower() != ".py": raise ValueError("Factory must be a local .py file")
    module_spec = importlib.util.spec_from_file_location("gis_user_factory", path)
    module = importlib.util.module_from_spec(module_spec)
    sys.path.insert(0, str(path.parent))
    try:
        module_spec.loader.exec_module(module)
        function = getattr(module, spec.get("function", "build_scene"))
        scene = function(copy.deepcopy(spec.get("params", {})))
    finally: sys.path.pop(0)
    if isinstance(scene, Figure):
        scene = EditableScene.from_figure(scene)
    if not isinstance(scene, EditableScene): raise TypeError("Factory must return EditableScene or Figure")
    if not hasattr(scene.figure.canvas, "get_renderer"): FigureCanvasAgg(scene.figure)
    scene.figure.canvas.draw()
    scene.provenance.update(data_hashes=hashes, trusted_factory=str(path), results_locked=True)
    scene.baseline = scene.snapshot()
    if not scene.provenance.get("display_crs_wkt"):
        scene.warnings.append("Factory CRS is unverified; declare display_crs_wkt and source layer CRS in provenance")
    return scene


def build_scene(spec, trusted=False):
    return build_factory(spec, trusted) if spec.get("mode") == "factory" else build_gis(spec)


def open_session(folder, spec=None, trusted=False):
    from scene import read_json
    folder = Path(folder)
    if (folder / "session.json").exists():
        if spec is not None: raise ValueError("Existing session: restore it without supplying a new spec")
        saved = read_json(folder / "session.json")
        spec = saved["spec"]
        current = inputs(spec)
        if current != saved["provenance"]["data_hashes"]:
            raise ValueError("Data/factory changed since save; create a new session instead of silently restoring against different inputs")
        scene = build_scene(spec, trusted)
        scene.restore(saved["state"])
        scene.provenance = saved["provenance"]
        scene.revision = saved["revision"]
        scene.baseline = saved["baseline"]
        scene.undo_stack = saved["undo"]
        scene.redo_stack = saved["redo"]
        scene.log = saved["log"]
    else:
        if spec is None: raise ValueError("New session requires a spec")
        scene = build_scene(spec, trusted)
    scene.save_session(folder, spec)
    return scene, spec
