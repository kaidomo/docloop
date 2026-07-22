# The variable layer: `policy.yaml` · 가변층: `policy.yaml`

> Moved from README (2026-07-22).

Every organization writes documents its own way — which sections come in which order, which
ones are mandatory, the house glossary, the words you're not allowed to use, the tone, and what
"done" means. In docloop all of that lives in **one file you edit**, `policy.yaml`. Move to a
team with different rules and you swap that one file — nothing inside docloop changes.
조직마다 문서 쓰는 방식이 다르다 — 섹션 순서, 필수 섹션, 사내 용어, 금칙어, 톤, 그리고 "완료"의
기준. docloop에서는 그 모두가 **당신이 직접 고치는 한 파일** `policy.yaml`에 들어간다. 팀이나
문서 표준이 바뀌면 이 파일 하나만 교체하면 되고, docloop 내부는 건드릴 게 없다.

How you actually do it:
실제로 하는 법:

```bash
cp /path/to/docloop/templates/policy.example.yaml ./policy.yaml   # copy the example into your work folder
# then open policy.yaml and edit it to match your team's rules
```

Limitation: `policy.yaml` holds rules that can be written down as plain values — an order, a
list, a word. Anything that needs *steps* or conditions (read-order over sources, conditional
stages, type-specific checks) belongs with the prompts and validators, not here.
한계: `policy.yaml`에는 값으로 적을 수 있는 규칙만 담는다 — 순서·목록·단어 같은 것. *절차*나
조건 분기가 필요한 것(출처 읽는 순서, 조건부 스테이지, 타입별 검사)은 여기가 아니라 프롬프트·
검증기 쪽에 둔다.

See **Technical details** below for the exact scope of the file.
이 파일이 정확히 어디까지 담는지는 아래 **기술 상세**를 참고.

## Technical details · 기술 상세

Your org's section order, required sections, glossary, forbidden words, tone, and
Definition of Done live in **one file** (`policy.yaml`) — never in the engine. Swap
orgs, swap that one file. See `templates/policy.example.yaml`.
조직별 규칙(섹션 순서, 필수 섹션, 용어, 금칙어, 톤, Definition of Done)은 엔진이 아니라 **한 파일**
(`policy.yaml`)에 둔다. 조직이 바뀌면 이 파일 하나만 교체한다. `templates/policy.example.yaml` 참고.
