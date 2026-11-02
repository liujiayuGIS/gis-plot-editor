"""Render the README's explicitly synthetic editing example; no GIS measurements."""
from pathlib import Path
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from scene import EditableScene


def main():
    fig = Figure(figsize=(7.2, 4.8), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([.12, .18, .6, .64])
    ax.scatter([0, 1, 2], [0, 1, .4], color="#367ca4", s=70, label="Group A")
    ax.scatter([.4, 1.6], [1.4, 1.6], color="#a87535", s=70, label="Group B")
    ax.set(xlim=(-.4, 2.5), ylim=(-.4, 2.3), xlabel="Synthetic X", ylabel="Synthetic Y", aspect="equal")
    title = fig.text(.5, .94, "Editable GIS figure", ha="center", va="top", fontfamily="DejaVu Sans")
    legend = fig.legend(loc="lower left", bbox_to_anchor=(.76, .65), borderaxespad=0)
    fig.text(.12, .05, "SYNTHETIC DEMO / No measured locations", fontsize=9, color="#626c76")
    scene = EditableScene(fig, {"title": title, "legend": legend, "map.main": ax})
    scene.edit([{"target": "title", "properties": {"fontsize": 17, "color": "#263e52"}},
                {"target": "legend", "properties": {"position": [.76, .57], "framealpha": 0.8}}], expected_revision=0)
    # Documentation image only; normal user exports use scene.export + manifest.
    import matplotlib
    with matplotlib.rc_context({"svg.fonttype": "none", "savefig.bbox": None}):
        fig.savefig(Path(__file__).resolve().parent.parent / "assets/demo-edited.svg", metadata={"Date": None})


if __name__ == "__main__": main()
