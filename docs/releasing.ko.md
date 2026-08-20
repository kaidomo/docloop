# Releasing docloop

English: [releasing.md](releasing.md)

`VERSION`은 docloop 제품 릴리스 버전의 단일 진실 공급원(SSOT)입니다. `CHANGELOG.md`의 첫
릴리스 헤딩과 릴리스 태그는 이 값의 투영(projection)입니다. 개별 서브시스템이 쓰는
`TOOL_VERSION` 상수는 프로토콜 호환성 마커이며, 의도적으로 독립적입니다.

docloop은 현재 안정(stable) Semantic Versioning 값과 태그만 지원합니다: `X.Y.Z`와
`vX.Y.Z`. 프리릴리스와 빌드 메타데이터는 릴리스 검증기가 허용하지 않습니다.

## 릴리스 준비

1. `VERSION`을 의도한 안정 버전으로 갱신하고, `CHANGELOG.md`의 첫 헤딩도 같은 값으로
   갱신합니다. 릴리스 노트는 그곳에서 정리합니다.
2. 로컬 계약(contract) 검사와 회귀 검사를 실행합니다.

   ```bash
   docloop --version
   python3 tools/check_release.py
   python3 tests/test_release.py
   python3 tests/run_tests.py
   ```

3. 리뷰된 변경을 `main`에 머지합니다. 머지되지 않은 브랜치에 태그를 달지 않습니다. 태그를
   만들기 전에 정확한 머지 커밋을 확인하고 CI가 통과했는지 검증합니다.

## 릴리스 태그 생성

최신 상태의 `main`에서, `v` 뒤에 `VERSION`의 내용이 정확히 이어지는 이름으로 annotated
태그를 만듭니다. 메인테이너에게 서명 identity가 설정돼 있다면 annotated 태그에 서명하는
것을 권장합니다.

```bash
git switch main
git pull --ff-only origin main
version="$(cat VERSION)"
git tag -s "v${version}" -m "docloop v${version}"   # recommended when signing is configured
# Or, when signing is unavailable:
# git tag -a "v${version}" -m "docloop v${version}"
git show "v${version}"
git push origin "refs/tags/v${version}"
```

푸시하기 전에 태그와 서명 상태를 수동으로 점검합니다. 자동화된 릴리스 계약은 annotated
태그를 요구하고 서명 검증 상태를 리포트하지만, 암호학적 신뢰(cryptographic trust)를
주장하지는 않습니다. 신뢰를 강제하려면 별도로 검토된 신뢰 서명자(identity 또는 key) 목록과
검증 메커니즘이 필요합니다.

태그-푸시 워크플로는 전체 히스토리를 포함해 기존 태그를 체크아웃하고, 전체 테스트 스위트를
실행하고, VERSION/CHANGELOG/태그의 일치 여부를 검증하고, 태그 객체가 annotated이며
`origin/main`에서 도달 가능한 커밋으로 peel되는지 확인한 뒤에야 GitHub Release를
생성합니다. 이 워크플로는 태그를 생성·이동·교체하지 않습니다. 자동 생성된 GitHub 노트는
정리된 CHANGELOG를 보완할 뿐이며, 패키지·중복 소스 아카이브·체크섬 자산은 게시하지
않습니다.

## 실패 복구

- 검증이 실패하면 `main`의 소스를 고치고 새 패치 버전을 준비합니다. 실패한 릴리스 태그를
  강제로 이동하거나 교체하지 않습니다.
- 검증 후 게시(publication)가 실패하면, 먼저 해당 태그에 GitHub Release가 이미 존재하는지
  확인합니다. 안전할 때만 실패한 job을 재실행하고, 복구 수단으로 태그를 이동하지 않습니다.
- 게시된 내용이 잘못됐다면 새 버전에서 바로잡습니다. 게시된 태그는 불변(immutable)으로
  취급합니다.

레포지토리 관리자는 `v*` 태그를 업데이트·삭제로부터 보호하는 ruleset을 추가하고, 가능한
곳에서는 GitHub Release immutability를 활성화해야 합니다. Actions 설정은 레포지토리
`GITHUB_TOKEN`이 Release를 생성할 수 있도록 허용해야 하며, 워크플로는 검증이 성공한 뒤
게시(publication) job에만 `contents: write` 권한을 부여합니다.

### 잔류 draft Release 복구 절차

release 워크플로 실행 중 오류가 나면 draft Release가 남을 수 있습니다. 이후 재실행은 기존 Release가 draft가 아닌 published 상태와 일치하는지 검증하므로, 남은 draft는 내용이 의도한 값과 일치하더라도 fail-closed 됩니다. 복구 절차:

1. 해당 tag의 draft Release 존재 여부를 확인합니다.
2. draft의 tag/name/body가 의도한 값과 일치하는지 확인합니다(기록용 — 삭제 여부를 바꾸지 않습니다).
3. 기존 draft가 있으면 일치 여부와 무관하게 항상 삭제합니다.
4. 삭제 후 workflow를 재dispatch합니다.

draft를 검증 없이 자동으로 publish하지 않는 것이 이 워크플로의 fail-closed 정책입니다.

## 신뢰 서명자 등록은 dispatch 사전조건입니다

`.github/release_allowed_signers`에는 메인테이너의 실제 프로덕션 SSH 공개키가 하나 이상
들어 있어야 릴리스 워크플로를 의미 있게 dispatch할 수 있습니다. 추가 서명 키는 한 줄에
하나씩 나열할 수 있습니다. 이것이 없으면 (`verify` job의 "Validate tag and changelog
notes" 단계에서 호출되는) `tools/check_release.py`가 `ReleaseError`를 발생시키고, 어떤
릴리스 증거(evidence)도 동결되기 전에 실행을 hard-fail시킵니다 — 워크플로는 등록되지 않은
서명자로는 게시 단계로 진행하지 않습니다.

이 앞단 게이트와는 별개로, `verify` job의 evidence 단계와 `publish` job의 `EXPECTED_*`
비교는 `sha256sum` 커맨드 치환(command substitution)을 통해 `signer_sha`, `notes_sha`,
`workflow_sha`를 각각 독립적으로 계산합니다. 로컬 재현(격리된 클론, `.github/release_allowed_signers`
부재 상태)으로 확인한 바에 따르면, 이 하드닝 이전에는 `$(...)` 안에서 `sha256sum`이
실패해도 `set -euo pipefail` 아래에서조차 해당 단계가 중단되지 **않았습니다** — 빈
문자열로 축소되고, 이후의 단순한 `test "" = ""` 비교가 무조건 통과해버렸습니다. 이는
evidence-freezing 및 expectation-comparison 로직에 잠재된 fail-open이었습니다. 실제로는
악용 가능하지 않았던 이유는 오직, 앞선 `tools/check_release.py`의 서명자 검사가 이미
실행을 먼저 hard-fail시켰기 때문입니다 — 즉 end-to-end로 관측된 동작은 "업스트림 게이트에
의해 fail-closed되지만, 그 뒤에는 쓰이지 않은 채 fail-open 버그가 놓여 있는" 상태였습니다.
이 PR은 이 잠재적 버그를 구조적으로 닫습니다: `signer_sha`, `notes_sha`, `workflow_sha`
(그리고 evidence 단계의 `tag_object`/`tag_commit`) 각각을 이제 로컬 변수에 할당하고,
사용하기 전에 `test -n`으로 확인한 뒤에야 `$GITHUB_OUTPUT`에 기록하거나 해당
`EXPECTED_*` 대응값과 비교합니다 — 그래서 누락되거나 읽을 수 없는 입력은 이제 계산 시점에
바로 hard-fail하며, 빈 기대값을 조용히 동결하지 않습니다.
