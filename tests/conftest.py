import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# CI has no live Cognee instance, so retrieval returns flagged stub evidence.
# In production the runtime controller fails closed on that flag rather than
# answering from fabricated content; the suite opts in so it can exercise the
# full pipeline. Set before importing app.core.config, which reads env at
# import time. tests/test_runtime_controller.py covers the production default.
os.environ.setdefault("ROUBAIX_ALLOW_STUB_EVIDENCE", "true")
