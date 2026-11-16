"""DISPATCH DESK — a dispatching assistant that only ever suggests.

  * Reads the office's board workbook through its OWN copy-aside — never the
    live file in place, and it never writes to it.
  * Imports board.py for its row rules only.
  * Writes only to its output folder (settings.OUT_DIR / "DESK") and its own
    small state folder (settings.STATE_DIR).
  * It drafts; a human pastes, sends and assigns.

Everything specific to one carrier is in dispatch_desk/settings.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dispatch_desk import settings  # noqa: E402

try:                                    # pages carry the carrier's name from the settings
    import page_design as _pd
    _pd.BRAND = settings.COMPANY
except ImportError:                     # the design module is only needed to build pages
    pass
