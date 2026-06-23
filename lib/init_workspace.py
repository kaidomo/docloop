#!/usr/bin/env python3
"""Bootstrap a work folder — a standard layout that separates inputs from outputs.

Why: if you mix the files you were given with the files docloop generates, you
can't tell them apart later. This lays down role-based folders and isolates the
material you hand in under inputs/.

Usage: python3 init_workspace.py <work-folder> [given_files...] [--no-move]
  - creates the 4-folder layout: inputs/ · outputs/ · work/ · reports/
  - moves given files into inputs/
  - creates README.md (folder conventions) if absent

Idempotent: existing folders/files are left alone; a name clash is skipped, not overwritten.
"""
import argparse
import os
import shutil

DIRS = ["inputs", "outputs", "work", "reports"]

README = """# Work folder — {project}

Authoring workspace. Inputs you provide and outputs docloop generates are kept
separate, by role, so they never get confused.

| Location        | What | Who writes it |
|-----------------|------|---------------|
| `inputs/`       | Originals you provide — policy/spec docs, prototypes | **Read-only.** docloop never edits/creates here |
| `outputs/`      | Publish artifacts (derived, regenerated) — the final pages | docloop generates |
| `work/`         | Intermediates — previews, fragments, scratch | docloop generates (not published) |
| `reports/`      | Internal audit docs — gap/open-question reports | docloop generates (shared, not published) |
| `manifest.yaml` | State backbone (sections, status, sources) | docloop + human |
| `*.md` (body)   | Unified body (SSOT) master — edit ONLY here | docloop authors/edits |

**Rules**
- Anything new you receive → put it in `inputs/` (never mixed with outputs).
- Generated files go only to `outputs/`, `work/`, `reports/`.
- `work/` is disposable and regenerable. `outputs/` is regenerable too (the SSOT + manifest are truth).
"""


def is_clean_name(name):
    return name.isascii() and " " not in name


def main():
    ap = argparse.ArgumentParser(description="Bootstrap the standard work-folder layout")
    ap.add_argument("project", help="work-folder path (created if absent)")
    ap.add_argument("files", nargs="*", help="given files to isolate under inputs/")
    ap.add_argument("--no-move", action="store_true", help="create folders only, don't move files")
    a = ap.parse_args()

    root = os.path.abspath(a.project)
    for d in DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)

    readme = os.path.join(root, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(README.format(project=os.path.basename(root) or root))

    inputs_root = os.path.join(root, "inputs")
    moved, skipped, warned = [], [], []
    if not a.no_move:
        for src in a.files:
            asrc = os.path.abspath(src)
            if not os.path.exists(asrc):
                skipped.append((src, "not found"))
                continue
            if os.path.isdir(asrc):
                # moving a whole directory risks removing more than intended -> refuse
                skipped.append((src, "directory — moving wholesale is risky; pass files individually or place under inputs/ by hand"))
                continue
            if asrc == inputs_root or asrc.startswith(inputs_root + os.sep):
                skipped.append((src, "already under inputs/"))
                continue
            name = os.path.basename(asrc.rstrip(os.sep))
            dest = os.path.join(inputs_root, name)
            if os.path.exists(dest):
                skipped.append((src, "name already exists in inputs/ — kept"))
                continue
            shutil.move(asrc, dest)
            moved.append(f"inputs/{name}")
            if not is_clean_name(name):
                warned.append(name)

    print(f"work folder: {root}")
    print("  inputs/  outputs/  work/  reports/  +README.md")
    if moved:
        print(f"isolated into inputs/ ({len(moved)}):")
        for m in moved:
            print(f"  -> {m}")
    for s, why in skipped:
        print(f"  (skipped) {s} — {why}")
    if warned:
        print("note: filenames with spaces/non-ASCII — prefer ascii + underscores:")
        for w in warned:
            print(f"  - {w}")
    print("\nNext: put manifest.yaml at the work-folder root and point its paths —")
    print("  output_dir: outputs (★ must be directly under the root for split's safety check) · audit reports: reports/")
    print("  inputs/ is read-only — generated files go only to work/outputs/reports.")


if __name__ == "__main__":
    main()
