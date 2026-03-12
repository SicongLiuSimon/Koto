import sys
import time

sys.path.insert(0, ".")
import app.core.agent.tool_registry as tr_mod
from app.core.agent.tool_registry import ToolRegistry

reg = ToolRegistry()

def hang_long():
    time.sleep(30)

reg.register_tool("hang2", hang_long, "hangs", {"type": "OBJECT", "properties": {}})

orig = tr_mod._TOOL_TIMEOUT
tr_mod._TOOL_TIMEOUT = 2   # Override to 2s for fast test

t0 = time.time()
try:
    reg.execute("hang2", {})
    print("ERROR: expected RuntimeError not raised")
    sys.exit(1)
except RuntimeError as e:
    elapsed = time.time() - t0
    print(f"Timeout fired at {elapsed:.2f}s msg='{e}'")
    if 1.8 <= elapsed <= 4.0 and "timed out" in str(e).lower():
        print("PASS: timeout enforcement verified")
        sys.exit(0)
    else:
        print(f"FAIL: elapsed={elapsed:.2f}s or wrong message")
        sys.exit(1)
finally:
    tr_mod._TOOL_TIMEOUT = orig
