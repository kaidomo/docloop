#!/usr/bin/env python3
"""peer-review stage: 리뷰 폴더 스캐폴드.
대상(파일/폴더)을 리뷰 폴더로 복사 + 브리프 템플릿(없을 때만) + 다음 단계 안내.
사용: python3 stage.py <name> <대상경로...> [--dest DIR]
- 기존 REVIEW_BRIEF.md는 덮어쓰지 않는다(라운드 누적 보존).
- name은 폴더명 한 토막만(경로 구분자/.. /절대경로 금지).
- 삭제·복사는 리뷰 폴더 내부로 한정(symlink로 외부 탈출 차단).
- 복사는 원자적(temp→replace), 내부 symlink는 제외, 원본↔사본 매핑은 STAGE_MANIFEST.md."""
import sys, os, shutil, argparse

DEFAULT_DEST = os.path.expanduser(os.environ.get("DOCLOOP_REVIEW_DIR", "~/.docloop/reviews"))
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "REVIEW_BRIEF.template.md")
BRIEF_NAME = "REVIEW_BRIEF.md"
MANIFEST_NAME = "STAGE_MANIFEST.md"


def _ignore(src, names):
    """copytree 필터: 내부 symlink 제외(외부 내용 읽기/복사 둘 다 차단) + 잡파일 제외."""
    skip = set()
    for n in names:
        if os.path.islink(os.path.join(src, n)):
            skip.add(n)
        elif n in ("__pycache__", ".git", "_preview") or n.endswith(".pyc"):
            skip.add(n)
    return skip


def _clean(dst):
    """dst를 타입 무관 정리(symlink는 링크만 제거 — 외부 내용 보존)."""
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

    # name 정제: 폴더명 한 토막만(경로 traversal·절대경로 차단)
    name = a.name
    if name in ("", ".", "..") or "/" in name or "\\" in name or os.path.isabs(name):
        sys.exit(f"[중단] name '{name}' 무효 — 경로 구분자·'..'·절대경로 금지(리뷰 폴더명 한 토막).")

    dest = os.path.realpath(os.path.expanduser(a.dest))
    review_dir = os.path.join(dest, name)
    # r2-B1: review_dir이 symlink면 중단(외부 디렉터리 삭제/탈출 방지)
    if os.path.islink(review_dir):
        sys.exit(f"[중단] '{review_dir}'가 symlink — 리뷰 폴더는 실제 디렉터리여야 함.")
    if os.path.exists(review_dir) and not os.path.isdir(review_dir):   # r6-R1: 파일이면 명시적 실패
        sys.exit(f"[중단] '{review_dir}'가 디렉터리가 아님(파일) — 리뷰 폴더 경로를 비우거나 바꾸세요.")
    created = not os.path.exists(review_dir)
    os.makedirs(review_dir, exist_ok=True)
    review_real = os.path.realpath(review_dir)
    if os.path.commonpath([dest, review_real]) != dest:   # review_dir이 dest 하위인지 확정
        if created:
            try: os.rmdir(review_dir)
            except OSError: pass
        sys.exit(f"[중단] review_dir이 dest 밖으로 해석됨: {review_real}")

    copied, seen, manifest_pairs = [], set(), []
    for t in a.targets:
        t = os.path.abspath(os.path.expanduser(t))
        if not os.path.exists(t):
            print(f"  ! 대상 없음(건너뜀): {t}"); continue
        # r4-R1: top-level symlink target은 역참조 위험 → 거부(외부 내용 유입 방지)
        if os.path.islink(t):
            print(f"  ! 대상이 symlink — 거부(외부 내용 유입 방지): {t}"); continue
        # r4-R2/r5-B1: target과 review_dir이 서로의 내부면(조상·자기·자손) 거부 — 자기복사/원본삭제 방지(양방향)
        t_real = os.path.realpath(t)
        cp = os.path.commonpath([t_real, review_real])
        if cp == t_real or cp == review_real:
            print(f"  ! 대상이 리뷰 폴더와 포함관계(조상/자신/자손) — 거부: {t}"); continue
        base = os.path.basename(t.rstrip("/"))
        # r4-B1: 빈 basename(루트 등)은 dst==review_dir이 되어 리뷰 폴더 삭제 위험 → 거부
        if not base or os.path.realpath(os.path.join(review_real, base)) == review_real:
            print(f"  ! 대상 basename이 비어있거나 리뷰 폴더 자신 — 거부: {t}"); continue
        if base in (BRIEF_NAME, MANIFEST_NAME):      # 누적 브리프·매니페스트 보호
            print(f"  ! '{base}'은 예약 파일 — 복사 건너뜀"); continue
        if base in seen:                             # 같은 basename 충돌
            print(f"  ! basename 충돌 '{base}' — 먼저 온 대상 유지, 건너뜀: {t}"); continue
        dst = os.path.join(review_real, base)
        if os.path.commonpath([review_real, os.path.realpath(dst)]) != review_real:
            print(f"  ! dst가 리뷰 폴더 밖 — 건너뜀: {dst}"); continue
        # temp에 먼저 복사 → 성공 시에만 기존 dst 교체. 디렉터리는 기존 dst를 지운 뒤 rename하므로
        # 그 짧은 구간까지 완전 원자적이진 않다(중단 시 staged 사본이 빌 수 있음 — 원본은 항상 안전, 재스테이지로 복구).
        tmp_dst = dst + ".tmp-stage"
        try:
            _clean(tmp_dst)                          # 잔여 temp 정리
            if os.path.isdir(t):
                shutil.copytree(t, tmp_dst, symlinks=False,   # 내부 symlink는 _ignore로 제외(외부 내용 차단)
                                ignore=_ignore)
            else:
                shutil.copy(t, tmp_dst)
            _clean(dst)                              # 성공 후에만 기존 dst 정리(dir↔file 스왑 안전)
            os.replace(tmp_dst, dst)                 # 거의 원자적 교체(같은 FS)
        except (OSError, shutil.Error) as e:         # 권한·긴경로·깨진 symlink·중간실패·정리실패 → traceback 대신 건너뜀
            try: _clean(tmp_dst)                     # temp 잔존 제거(이중 실패는 무시)
            except OSError: pass
            print(f"  ! 복사 실패(건너뜀): {t} — {e}"); continue
        seen.add(base); copied.append(base); manifest_pairs.append((base, t_real))

    if not copied:                                   # 0건 → 실패 + 새로 만든 빈 폴더 정리(r2-R3)
        if created:
            try: os.rmdir(review_dir)
            except OSError: pass
        elif os.path.exists(os.path.join(review_dir, MANIFEST_NAME)):  # 기존 폴더는 보존(이전 라운드 훼손 방지)
            print("  ! 기존 사본/STAGE_MANIFEST.md는 직전 stage 결과이며 이번 실행으로 갱신되지 않음(아래 비정상 종료).")
        sys.exit("[중단] 복사된 대상 0건 — 리뷰 폴더를 구성하지 못함(대상 경로 확인).")

    brief = os.path.join(review_dir, BRIEF_NAME)
    if os.path.exists(brief):
        brief_status = "기존 유지(라운드 누적)"
    else:
        shutil.copy(TEMPLATE, brief)
        brief_status = "생성(템플릿) — Claude가 채울 것"

    # 매니페스트: staged basename ← 원본 절대경로 (원본 오적용 방지 — 반영은 원본에)
    with open(os.path.join(review_dir, MANIFEST_NAME), "w") as mf:
        mf.write("# Stage manifest — `staged 사본` ← `원본 절대경로`\n")
        mf.write("# 반영은 원본 경로에. 승인표 '원본 적용경로'를 이 매핑으로 확인할 것. (stage마다 재생성)\n\n")
        for b, src in manifest_pairs:
            mf.write(f"- `{b}` ← `{src}`\n")

    print(f"리뷰 폴더: {review_dir}")
    print(f"복사: {copied}")
    print(f"REVIEW_BRIEF.md: {brief_status}")
    print("\n다음 단계:")
    print("  1) Claude가 REVIEW_BRIEF.md 작성/갱신(무엇·이미 내린 결정·봐줄 항목)")
    print(f"  2) cd '{review_dir}' && codex exec --skip-git-repo-check --sandbox read-only - > REVIEW_r1.md")
    print("     ('-'는 stdin — SKILL.md §2의 <<'PEER_REVIEW_PROMPT' 프롬프트를 넣어라. 빈 입력 금지)")
    print("  3) Claude triage → ⛔사람 승인 → 반영+테스트 → '반영 내역(vN)' 기록 → 필요시 반복")


if __name__ == "__main__":
    main()
