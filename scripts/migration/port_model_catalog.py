"""Splice upstream's model-catalog helpers into the fork's ModelsHandler.

Run once; the result is reviewed and committed. Kept in scripts/migration
alongside emit_fork_web.py so the provenance of the block is on record.
"""
import re
import subprocess
import sys

FORK = "channel/web/fork/handlers/models.py"
UPSTREAM = "channel/web/api/models.py"

src = subprocess.run(["git", "show", f"origin/master:{UPSTREAM}"],
                     capture_output=True, text=True, check=True).stdout


def slice_class_body(name, *, start_marker=None):
    """Upstream source of one class member (attr or method), 4-space indent."""
    lines = src.splitlines()
    if start_marker:
        idx = next(i for i, l in enumerate(lines) if start_marker in l)
    else:
        idx = next(i for i, l in enumerate(lines) if re.match(rf"\s*(@classmethod|@staticmethod)\s*$", l)
                   and name in "\n".join(lines[i:i + 6]))
    # Walk back to the decorator if the match landed on the def.
    while idx > 0 and not lines[idx].startswith("    @") and not lines[idx].startswith("    " + name):
        idx -= 1
    start = idx
    # For a method the member's own ``def`` line matches the "next member"
    # pattern, so step over the decorator(s) and the def before scanning; an
    # assignment (a table) has no def line and must scan from the next line.
    cursor = start + 1
    if re.match(r"\s*(@|def )", lines[start]):
        while cursor < len(lines) and not re.match(r"\s*def \w+", lines[cursor]):
            cursor += 1
        cursor += 1
    end = cursor
    for i in range(cursor, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if re.match(r"    (@classmethod|@staticmethod|def |#|_?[A-Za-z]\w*\s*=)", line):
            end = i
            break
    else:
        end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


table = slice_class_body("_PRESET_MODEL_META", start_marker="_PRESET_MODEL_META = {")
apply_catalog = slice_class_body("_apply_catalog")
preset_seed = slice_class_body("_preset_seed")
merged_catalog = slice_class_body("_merged_catalog")

for label, block in (("table", table), ("apply", apply_catalog),
                     ("seed", preset_seed), ("merged", merged_catalog)):
    if not block.startswith("    "):
        print(f"[{label}] bad slice:\n{block[:200]}", file=sys.stderr)
        sys.exit(2)

# --- adapt to the fork's idioms -------------------------------------------
# The fork's canonical provider table lives on ConfigHandler (the merge split
# it out of the monolith); upstream's module-level PROVIDER_MODELS does not
# exist here. Import it lazily inside the body, as the rest of the module does.
apply_catalog = apply_catalog.replace(
    "        merged = dict(presets)\n",
    "        from channel.web.web_channel import ConfigHandler\n"
    "        provider_models = ConfigHandler.PROVIDER_MODELS\n"
    "        merged = dict(presets)\n", 1)
apply_catalog = apply_catalog.replace("PROVIDER_MODELS.keys()", "provider_models.keys()")

preset_seed = preset_seed.replace(
    "        merged: \"OrderedDict[str, dict]\" = OrderedDict()\n",
    "        from channel.web.web_channel import ConfigHandler\n"
    "        merged: \"OrderedDict[str, dict]\" = OrderedDict()\n", 1)
preset_seed = preset_seed.replace(
    "for m in PROVIDER_MODELS.get(pid, {}).get(\"models\") or []:",
    "for m in ConfigHandler.PROVIDER_MODELS.get(pid, {}).get(\"models\") or []:")

for block_name, block in (("_apply_catalog", apply_catalog), ("_preset_seed", preset_seed),
                          ("_merged_catalog", merged_catalog)):
    if "PROVIDER_MODELS" in block and "ConfigHandler" not in block:
        print(f"[{block_name}] unadapted PROVIDER_MODELS reference:\n{block}", file=sys.stderr)
        sys.exit(2)

merged_block = "\n\n".join([table, apply_catalog, preset_seed, merged_catalog])
# Nudge the table above the methods for readability (upstream has it below).
merged_block = table + "\n\n" + "\n\n".join([apply_catalog, preset_seed, merged_catalog])

target = open(FORK, encoding="utf-8").read()

# Idempotent: drop a previously spliced block (the header comment through the
# last helper) so a re-run after a fix replaces it instead of duplicating.
target = re.sub(
    r"    # --- model catalog overlay[\s\S]*?\n(?=    @classmethod\n    def _provider_overview)",
    "", target)

anchor = "    @classmethod\n    def _provider_overview(cls) -> List[dict]:"
if anchor not in target:
    print("anchor not found", file=sys.stderr)
    sys.exit(2)
if "_PRESET_MODEL_META" in target:
    print("already spliced", file=sys.stderr)
    sys.exit(2)

header = (
    "    # --- model catalog overlay ---------------------------------------\n"
    "    # Upstream lets the console edit a provider's model list; the overlay\n"
    "    # itself is the shared runtime module ``models.model_catalog`` (merged\n"
    "    # whole). These helpers are the web surface for it: presets are the\n"
    "    # base, and only what the user actually changed is layered on top, so a\n"
    "    # preset the user never touched keeps following the code-side metadata.\n"
)
target = target.replace(anchor, header + merged_block + "\n\n" + anchor, 1)
open(FORK, "w", encoding="utf-8").write(target)
print("spliced", len(merged_block.splitlines()), "lines")
