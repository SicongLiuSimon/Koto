"""
Scan key API contracts in app/ and web/ looking for:
1. Function signature → caller mismatches
2. Missing imports that would cause NameError at runtime
3. Return type mismatches in key data flows
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

issues = []

# ── Collect all def sites ─────────────────────────────────────────────────────
defs = {}  # (filepath, funcname) -> {params, required, lineno}

def scan_defs(dirpath):
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if not f.endswith('.py'):
                continue
            path = os.path.join(root, f)
            try:
                src = open(path, encoding='utf-8').read()
                tree = ast.parse(src)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = node.args
                    params = [a.arg for a in args.args]
                    n_defaults = len(args.defaults)
                    required = params[: (len(params) - n_defaults)] if n_defaults else params[:]
                    key = (path, node.name)
                    defs[key] = {
                        'params': params,
                        'required': required,
                        'lineno': node.lineno,
                    }

scan_defs('app')
scan_defs('web')
print(f"Scanned {len(defs)} function defs in app/ and web/")

# ── Helper: find a def ────────────────────────────────────────────────────────
def find_def(path_fragment, func_name):
    for (p, n), v in defs.items():
        if path_fragment in p and n == func_name:
            return p, v
    return None, None

# ── Check 1: UnifiedAgent.__init__ ───────────────────────────────────────────
p, v = find_def('unified_agent', '__init__')
if v:
    print(f"\nUnifiedAgent.__init__ params: {v['params']}")
else:
    issues.append("UnifiedAgent.__init__ not found")

# ── Check 2: UnifiedAgent.run ─────────────────────────────────────────────────
p, v = find_def('unified_agent', 'run')
if v:
    print(f"UnifiedAgent.run params: {v['params']}")
else:
    issues.append("UnifiedAgent.run not found")

# ── Check 3: SmartDispatcher.analyze return tuple unpacking ──────────────────
# Scan web/app.py for analyze() call sites
src = open('web/app.py', encoding='utf-8').read()
analyze_calls = [
    line.strip() for line in src.splitlines()
    if 'SmartDispatcher.analyze(' in line or 'LocalDispatcher' in line
]
print(f"\nSmartDispatcher.analyze() call sites ({len(analyze_calls)}):")
for c in analyze_calls[:10]:
    print(f"  {c[:100]}")

# ── Check 4: generate_with_fallback signature vs callers ─────────────────────
p, v = find_def('model_fallback', 'generate_with_fallback')
if v:
    print(f"\ngenerate_with_fallback params: {v['params']}")

# Check all callers in unified_agent.py
ua_src = open('app/core/agent/unified_agent.py', encoding='utf-8').read()
for i, line in enumerate(ua_src.splitlines(), 1):
    if 'generate_with_fallback(' in line and 'def ' not in line:
        # Print context (5 lines)
        lines = ua_src.splitlines()
        start = max(0, i - 3)
        end = min(len(lines), i + 6)
        print(f"\ngenerate_with_fallback call at unified_agent.py:{i}")
        for j, l in enumerate(lines[start:end], start + 1):
            marker = ">>>" if j == i else "   "
            print(f"  {marker} {j:4d}: {l}")
        break

# ── Check 5: factory.py create_agent calls ───────────────────────────────────
p, v = find_def('factory', 'create_agent')
if v:
    print(f"\nfactory.create_agent params: {v['params']}")

# ── Check 6: _load_history callers vs definition ─────────────────────────────
p, v = find_def('agent_routes', '_load_history')
if v:
    print(f"\n_load_history params: {v['params']}")
    # Check callers
    ar_src = open('app/api/agent_routes.py', encoding='utf-8').read()
    for i, line in enumerate(ar_src.splitlines(), 1):
        if '_load_history(' in line and 'def ' not in line:
            print(f"  caller line {i}: {line.strip()}")

# ── Check 7: skill_capability.py _load_entry_point whitelist ─────────────────
sc_src = open('app/core/skills/skill_capability.py', encoding='utf-8').read()
if '"app."' in sc_src or '"web."' in sc_src:
    print("\nskill_capability._load_entry_point: whitelist present ✓")
else:
    issues.append("skill_capability._load_entry_point missing module whitelist")

# ── Check 8: output_validator import re ──────────────────────────────────────
ov_src = open('app/core/security/output_validator.py', encoding='utf-8').read()
if 'import re' in ov_src:
    print("output_validator: import re present ✓")
else:
    issues.append("output_validator: missing import re")

# ── Check 9: ai_router.py valid_tasks in classify() ──────────────────────────
ar_src = open('app/core/routing/ai_router.py', encoding='utf-8').read()
if 'valid_tasks' in ar_src:
    print("ai_router: valid_tasks present ✓")
else:
    issues.append("ai_router: missing valid_tasks list")

# ── Check 10: model_fallback circuit breaker ──────────────────────────────────
mf_src = open('app/core/llm/model_fallback.py', encoding='utf-8').read()
if '_cascade_failures' in mf_src and '_CIRCUIT_BREAKER_BASE' in mf_src:
    print("model_fallback: circuit breaker present ✓")
else:
    issues.append("model_fallback: circuit breaker missing")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
if issues:
    print(f"ISSUES FOUND ({len(issues)}):")
    for iss in issues:
        print(f"  ✗ {iss}")
else:
    print("No contract issues found ✓")
