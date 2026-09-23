# docloop / human-edit

Identify the explicitly supplied local document copy before editing; if it is missing or ambiguous, ask for its selection without changing files. Preserve a before-edit copy.

# Human-facing document editing

Use this skill when an existing document needs to be made readable and useful to a product or planning audience. The current published or supplied human-facing document and explicit current user decisions are the authority for this editing pass. Local, machine-oriented material is reference context; it may clarify wording but may not introduce or override a requirement.

## Editing contract

- Reorder existing content to the supplied `docmodel`. If none is supplied, preserve the current section order unless the user identifies a current human-facing example as the model; state any structure inferred from that example.
- Follow the supplied tone and nearby human-facing examples. State established requirements directly as intended behavior; simplify repetition and speculative phrasing without changing obligation strength, a target into a guarantee, or an unresolved choice into a decision.
- Keep the product requirement: user-visible behavior, actors, conditions, timing, exceptions, required records or logs, access outcomes, and acceptance criteria when they are part of the requested behavior.
- Remove code, database, API, schema, class, query, and other implementation prescriptions when they do not define a user-observable requirement. Technical product concepts remain when the behavior requires them (for example, an audit log or an access block).
- Remove work-session chatter, manifests, ticket mechanics, drafting notes, investigation tables, and other machine or process instructions from the human-facing body.
- Replace “ask the developer” or “confirm with engineering” deferrals with direct product behavior only when that behavior is already stated in the working base or an explicit current user decision. Otherwise retain an open decision with its subject, options, constraints, and owner if supplied; do not invent the answer.
- Preserve user-added rules and decisions even when they are absent from the reference material. Preserve links, macros, attachments, and meaningful labels when editing a published page.

## Source and output boundaries

Consult references only to preserve the meaning of existing requirements. If a reference conflicts with the working base, preserve the base requirement and report the conflict. Route source selection, requirement import, conflict resolution, or manifest-backed PM source reconciliation to `docloop plan`; continue independent presentation cleanup here. Independent defect discovery belongs to `review-gate`.

By default, edit only the supplied document copy. Change local reference files only when the user explicitly asks for that update. This local docloop stage does not publish externally. Return a reviewable local document or diff. Do not claim that a wording cleanup proves policy correctness or implementation correctness.

## Preservation check

Before delivering, compare before and after by requirement rather than by sentence. Start from every substantive requirement in the original, including conditions, exceptions, timing, outcomes, required records, acceptance criteria, and unresolved decisions. Map each to its equivalent in the edited text. Account for removed passages as implementation/process material, duplicate meaning preserved elsewhere, or an explicitly authorized scope change. In reverse, map every substantive requirement in the edited text to the working base or an explicit current user decision. An unmapped deletion or addition is a preservation gap, not a successful cleanup. Report the edited sections, material assumptions, and preservation gaps separately.

The deliverable is the edited human-facing document (or a reviewable diff) plus a concise preservation note. It is not a new requirements document, an implementation design, or a defect inventory.
