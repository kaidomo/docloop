# The variable layer: `policy.yaml`

한국어: [policy-layer.ko.md](policy-layer.ko.md)

> Moved from README (2026-07-22).

Every organization writes documents its own way — which sections come in which order, which
ones are mandatory, the house glossary, the words you're not allowed to use, the tone, and what
"done" means. In docloop all of that lives in **one file you edit**, `policy.yaml`. Move to a
team with different rules and you swap that one file — nothing inside docloop changes.

How you actually do it:

```bash
cp /path/to/docloop/templates/policy.example.yaml ./policy.yaml   # copy the example into your work folder
# then open policy.yaml and edit it to match your team's rules
```

Limitation: `policy.yaml` holds rules that can be written down as plain values — an order, a
list, a word. Anything that needs *steps* or conditions (read-order over sources, conditional
stages, type-specific checks) belongs with the prompts and validators, not here.

See **Technical details** below for the exact scope of the file.

## Technical details

Your org's section order, required sections, glossary, forbidden words, tone, and
Definition of Done live in **one file** (`policy.yaml`) — never in the engine. Swap
orgs, swap that one file. See `templates/policy.example.yaml`.
