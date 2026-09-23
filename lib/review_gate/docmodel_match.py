"""Explicit-input adaptation of docauth's approved section-subset matching."""
import hashlib
from pathlib import Path
try:
    from .validate_docmodel import load, validate
    from .validate_docmodel_approvals import validate as validate_approvals
    from .validate_convention_intake import declares_profile_not_applicable
except ImportError:
    from validate_docmodel import load, validate
    from validate_docmodel_approvals import validate as validate_approvals
    from validate_convention_intake import declares_profile_not_applicable


def select_candidate(intake, candidates, approvals_raw):
    if not declares_profile_not_applicable(intake):
        raise ValueError("candidate mode requires an inapplicable convention profile")
    errors = validate_approvals(Path("approvals.yaml"), content=approvals_raw)
    if errors:
        raise ValueError("invalid docmodel approvals: " + "; ".join(errors))
    approvals = load(Path("approvals.yaml"), content=approvals_raw)["approvals"]
    observed = set(intake["profile_applicability"]["observed_sections"])
    matches = []
    for name, raw in candidates.items():
        errors = validate(Path(name), content=raw)
        if errors:
            raise ValueError("invalid candidate " + name + ": " + "; ".join(errors))
        doc = load(Path(name), content=raw)
        titles = {section["title"] for section in doc["sections"]}
        approved = any(row["status"] == "approved" and row["docmodel_path"] == name
                       and row["docmodel_sha256"] == hashlib.sha256(raw).hexdigest() for row in approvals)
        if approved and doc["meta"]["approval_state"] == "approved" and titles and titles.issubset(observed):
            matches.append(name)
    selection = intake.get("docmodel_selection")
    if isinstance(selection, dict) and selection.get("approval") == "answered":
        if selection.get("response") not in matches:
            raise ValueError("explicit docmodel selection is not an approved matching candidate")
        return selection["response"]
    return matches[0] if len(matches) == 1 else None
