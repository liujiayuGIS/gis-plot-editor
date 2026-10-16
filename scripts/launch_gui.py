"""Native GUI launch entry with persistent diagnostics (no silent import failures)."""
from pathlib import Path
import os
import sys
import traceback

log_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GisPlotEditor" / "launch.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a", encoding="utf-8") as log:
    log.write(f"\nLaunch interpreter: {sys.executable}\nArguments: {sys.argv!r}\n")
    log.flush()
    sys.stdout = log
    sys.stderr = log
    try:
        from gis_editor import main
        if len(sys.argv) == 1:
            sys.argv.append('gui')
        code = main()
        log.write(f"Exit status: {code}\n")
    except BaseException:
        traceback.print_exc(file=log)
        raise
