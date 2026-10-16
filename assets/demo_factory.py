"""Synthetic local Figure adapter example. No real geographic measurements."""
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.lines import Line2D
from pyproj import CRS
from scene import EditableScene, choose_font


def build_scene(params):
    font = choose_font()
    fig = Figure(figsize=(7.2, 5), dpi=110, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([.12, .16, .65, .65])
    ax.set_aspect("equal")
    one = ax.scatter([0, 1, 2], [0, 1, .4], color="#367ca4", s=70, label="Group A")
    two = ax.scatter([.4, 1.6], [1.4, 1.6], color="#a87535", s=70, label="Group B")
    ax.set_xlim(-.4, 2.5); ax.set_ylim(-.4, 2.3)
    ax.set_xlabel("Synthetic X (m)", fontfamily=font); ax.set_ylabel("Synthetic Y (m)", fontfamily=font)
    title = fig.text(.5, .94, "合成数据演示 — 可编辑场景", ha="center", va="top", fontsize=14, fontfamily=font)
    note = fig.text(.12, .05, "SYNTHETIC DEMO / Not a study result", fontsize=9, color="#6a727b")
    legend = fig.legend(handles=[Line2D([], [], marker="o", ls="", color="#367ca4", label="Group A"),
                                 Line2D([], [], marker="o", ls="", color="#a87535", label="Group B")],
                        loc="lower left", bbox_to_anchor=(.76, .68), borderaxespad=0, prop={"family": font, "size": 10})
    fig.canvas.draw()
    return EditableScene(fig, {"map.main": ax, "title": title, "legend": legend, "note": note,
                               "map.xlabel": ax.xaxis.label, "map.ylabel": ax.yaxis.label,
                               "layer.group_a": one, "layer.group_b": two},
                         {"synthetic": True, "display_crs_wkt": CRS.from_epsg(32650).to_wkt(),
                          "coordinate_note": "Synthetic local coordinates only; no measured locations"})
