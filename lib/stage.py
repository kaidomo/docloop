#!/usr/bin/env python3
"""peer-review stage: scaffold a review folder.
Copies targets (files/folders) into the review folder + a brief template (only if absent) + next-step guidance.
Usage: python3 stage.py <name> <target-path...> [--dest DIR]
- An existing REVIEW_BRIEF.md is never overwritten (rounds accumulate).
- name must be a single folder name (no path separators / .. / absolute paths).
- Deletes and copies are confined to the review folder (symlinks can't escape).
- Copies are atomic (temp→replace), internal symlinks are excluded, original↔copy mapping is in STAGE_MANIFEST.md."""
import sys, os, shutil, argparse

DEFAULT_DEST = os.path.expanduser(os.environ.get("DOCLOOP_REVIEW_DIR", "~/.docloop/reviews"))
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "REVIEW_BRIEF.template.md")
BRIEF_NAME = "REVIEW_BRIEF.md"
MANIFEST_NAME = "STAGE_MANIFEST.md"


def _ignore(src, names):
    """copytree filter: exclude internal symlinks (blocks both reading and copying external content) + exclude noise files."""
    skip = set()
    for n in names:
        if os.path.islink(os.path.join(src, n)):
            skip.add(n)
        elif n in ("__pycache__", ".git", "_preview") or n.endswith(".pyc"):
            skip.add(n)
    return skip


def _clean(dst):
    """Remove dst regardless of type (symlinks: remove the link only — external content is preserved)."""
    if os.path.islink(dst):
        os.unlink(dst)
    elif os.path.isdir(dst):
        shutil.rmtree(dst)
    elif os.path.exists(dst):
        os.remove(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--dest", default=DEFAULT_DEST)
    a = ap.parse_args()

    # Sanitize name: must be a single folder-name token (blocks path traversal and absolute paths)
    name = a.name
    if name in ("", ".", "..") or "/" in name or "\\" in name or os.path.isabs(name):
        sys.exit(f"[abort] name '{name}' invalid — no path separators, '..', or absolute paths (a single review-folder name).")

    dest = os.path.realpath(os.path.expanduser(a.dest))
    review_dir = os.path.join(dest, name)
    # r2-B1: abort if review_dir is a symlink (prevents deleting or escaping to an external directory)
    if os.path.islink(review_dir):
        sys.exit(f"[abort] '{review_dir}' is a symlink — the review folder must be a real directory.")
    if os.path.exists(review_dir) and not os.path.isdir(review_dir):   # r6-R1: explicit failure if it's a plain file
        sys.exit(f"[abort] '{review_dir}' is not a directory (it's a file) — clear or change the review-folder path.")
    created = not os.path.exists(review_dir)
    os.makedirs(review_dir, exist_ok=True)
    review_real = os.path.realpath(review_dir)
    if os.path.commonpath([dest, review_real]) != dest:   # confirm review_dir is actually under dest
        if created:
            try: os.rmdir(review_dir)
            except OSError: pass
        sys.exit(f"[abort] review_dir resolved outside dest: {review_real}")

    copied, seen, manifest_pairs = [], set(), []
    for t in a.targets:
        t = os.path.abspath(os.path.expanduser(t))
        if not os.path.exists(t):
            print(f"  ! target not found (skipped): {t}"); continue
        # r4-R1: top-level symlink targets are risky to dereference → reject (prevents pulling in external content)
        if os.path.islink(t):
            print(f"  ! target is a symlink — rejected (prevents pulling in external content): {t}"); continue
        # r4-R2/r5-B1: reject if target and review_dir overlap (ancestor/self/descendant) — prevents self-copy or deletion of the original (both directions)
        t_real = os.path.realpath(t)
        cp = os.path.commonpath([t_real, review_real])
        if cp == t_real or cp == review_real:
            print(f"  ! target overlaps the review folder (ancestor/self/descendant) — rejected: {t}"); continue
        base = os.path.basename(t.rstrip("/"))
        # r4-B1: empty basename (e.g. root path) would make dst==review_dir, risking deletion of the review folder → reject
        if not base or os.path.realpath(os.path.join(review_real, base)) == review_real:
            print(f"  ! target basename is empty or is the review folder itself — rejected: {t}"); continue
        if base in (BRIEF_NAME, MANIFEST_NAME):      # protect the accumulated brief and manifest
            print(f"  ! '{base}' is a reserved file — copy skipped"); continue
        if base in seen:                             # basename collision
            print(f"  ! basename clash '{base}' — keeping the first target, skipped: {t}"); continue
        dst = os.path.join(review_real, base)
        if os.path.commonpath([review_real, os.path.realpath(dst)]) != review_real:
            print(f"  ! dst is outside the review folder — skipped: {dst}"); continue
        # Copy to temp first → replace the existing dst only on success. For directories, the existing dst
        # is removed before rename, so that brief window is not fully atomic (an interrupted run may leave
        # an empty staged copy — the original is always safe; re-stage to recover).
        tmp_dst = dst + ".tmp-stage"
        try:
            _clean(tmp_dst)                          # remove any leftover temp
            if os.path.isdir(t):
                shutil.copytree(t, tmp_dst, symlinks=False,   # internal symlinks excluded via _ignore (blocks external content)
                                ignore=_ignore)
            else:
                shutil.copy(t, tmp_dst)
            _clean(dst)                              # clean existing dst only after success (safe dir↔file swap)
            os.replace(tmp_dst, dst)                 # near-atomic replace (same filesystem)
        except (OSError, shutil.Error) as e:         # permission error, long path, broken symlink, mid-copy failure, cleanup failure → skip instead of traceback
            try: _clean(tmp_dst)                     # remove leftover temp (double-failure is ignored)
            except OSError: pass
            print(f"  ! copy failed (skipped): {t} — {e}"); continue
        seen.add(base); copied.append(base); manifest_pairs.append((base, t_real))

    if not copied:                                   # 0 targets copied → fail + clean up any newly created empty folder (r2-R3)
        if created:
            try: os.rmdir(review_dir)
            except OSError: pass
        elif os.path.exists(os.path.join(review_dir, MANIFEST_NAME)):  # preserve existing folder (prevent damaging a previous round)
            print("  ! existing copies/STAGE_MANIFEST.md are from the previous stage and were NOT updated by this run (aborting below).")
        sys.exit("[abort] 0 targets copied — could not build the review folder (check target paths).")

    brief = os.path.join(review_dir, BRIEF_NAME)
    if os.path.exists(brief):
        brief_status = "kept existing (rounds accumulate)"
    else:
        shutil.copy(TEMPLATE, brief)
        brief_status = "created from template — the authoring model/user fills it"

    # Manifest: staged basename ← original absolute path (prevents misapplying changes — apply to the original)
    with open(os.path.join(review_dir, MANIFEST_NAME), "w") as mf:
        mf.write("# Stage manifest — `staged copy` ← `original absolute path`\n")
        mf.write("# Apply changes to the original path. Confirm the approval table's 'original apply path' against this mapping. (regenerated each stage)\n\n")
        for b, src in manifest_pairs:
            mf.write(f"- `{b}` ← `{src}`\n")

    print(f"review folder: {review_dir}")
    print(f"copied: {copied}")
    print(f"REVIEW_BRIEF.md: {brief_status}")
    print("\nNext (see prompts/review.md for the full loop):")
    print("  1) Fill REVIEW_BRIEF.md (what it is, decisions already made, what to look at).")
    print(f"  2) cd '{review_dir}' && codex exec --skip-git-repo-check --sandbox read-only - > REVIEW_r1.md")
    print("     ('-' is stdin — feed the review prompt from prompts/review.md step 2. No empty input.)")
    print("  3) Triage findings -> ⛔ human approval -> apply+test -> record 'Applied (vN)' -> repeat if needed.")


if __name__ == "__main__":
    main()
