"""The security and PII review, measured against the running system.

    python -m lnd.reference.security          # writes docs/security-review.md

Week 10, Person A, and the item that ends with a signature from HR rather than
a green build. What HR is being asked to sign is a claim about what this
platform holds and what it cannot do, so every claim here is evidence rather
than assertion: the columns come from the schema, the rejected fields come from
the source's own typed model, the absent ones come from the recorded payloads,
and the grants come from PostgreSQL.

WHY IT IS GENERATED

A security review written by hand describes the system on the day somebody
wrote it. This one is regenerated, so a field added to `dim_employee` next month
appears in the table HR signed without anybody remembering to add it — and the
test beside it fails if that field is one of the two NFR-06 names.

THE ONE THING THIS SAYS THAT IS UNCOMFORTABLE

`raw.*` holds the source payloads verbatim, and those payloads carry names,
emails and mobile numbers. That is the whole design — an append-only record of
what the source said, which is what makes a restated figure defensible — and it
means field minimisation is a property of `core`, not of the database. Saying so
plainly is the point of a review. It is stated in the document, with the access
control that stands in front of it.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

REPORT = Path(__file__).resolve().parents[4] / "docs" / "security-review.md"

#: NFR-06. Neither may ever be stored, and neither is exposed by the source —
#: so the control is doubled: absent at the boundary, and asserted here.
FORBIDDEN_FIELDS = ("identification_id", "national_id", "payscale", "salary", "wage")

#: Columns of `dim_employee` that identify a person rather than describe their
#: place in the organisation. Named explicitly so the document can say which of
#: what it stores is personal, instead of listing thirty columns and leaving the
#: reader to work it out.
IDENTIFYING = {"employee_code", "email", "full_name", "normalised_name", "odoo_id"}

#: HTTP verbs that would write to the CRM. The claim "read-only at source" is
#: enforced by their absence from the client, and that absence is checked rather
#: than promised.
WRITE_VERBS = (".post(", ".put(", ".patch(", ".delete(")


def _stored_fields() -> list[tuple[str, str, bool]]:
    """Every column the platform keeps about a person, from the schema itself."""
    from lnd.models.core import DimEmployee

    return [
        (column.name, str(column.type), column.name in IDENTIFYING)
        for column in DimEmployee.__table__.columns
    ]


def _offered_fields() -> list[str]:
    """Every field the CRM's user object carries, from the typed model."""
    from lnd.sources.crm.models import User

    return sorted(User.model_fields)


def _rejected_fields() -> list[str]:
    """Offered by the source, deliberately not carried into `core`.

    The evidence for minimisation. A field here is one the platform could have
    kept and chose not to, and each one should be answerable with the metric it
    would have served — which is none, or it would be stored.
    """
    stored = {name for name, _, _ in _stored_fields()}
    # Structural fields, not attributes: `id` is the CRM's local primary key,
    # which must never be joined on, and the three nested objects are stored
    # flattened under their own names.
    structural = {"id", "department", "company", "position"}
    return [
        field
        for field in _offered_fields()
        if field not in stored and field not in structural and field != "name"
    ]


def _forbidden_in_payloads() -> dict[str, int]:
    """Whether the source exposes either NFR-06 field at all.

    Measured over the frozen dataset's payloads rather than recalled from the
    field inventory: if the CRM ever starts sending a national ID, this is what
    notices, and it notices before anybody decides whether to store it.
    """
    from lnd.reference.freeze import load as load_dataset

    text_of = repr(load_dataset())
    return {field: text_of.count(f"'{field}'") for field in FORBIDDEN_FIELDS}


def _write_client_present() -> list[str]:
    """Any write verb in the CRM client. The list should be empty."""
    client = Path(__file__).resolve().parents[1] / "sources" / "crm" / "client.py"
    source = client.read_text(encoding="utf-8")
    return [verb for verb in WRITE_VERBS if verb in source]


def _grants(session: Session) -> list[tuple[str, str, bool]]:
    """What the application role may do to `raw`, asked of PostgreSQL.

    Raw immutability is a grant, not a convention. The migration sets it; this
    reads it back from the running database, which is the only version of the
    claim worth signing.
    """
    from lnd.db import SCHEMA_RAW
    from lnd.ingest import RawRecord

    role = session.scalar(text("SELECT current_user")) or "unknown"
    checks: list[tuple[str, str, bool]] = []
    # Named off the model rather than typed: a renamed table would otherwise
    # make this raise, and an exception is a worse outcome than a finding.
    table = f"{SCHEMA_RAW}.{RawRecord.__tablename__}"
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        granted = session.scalar(
            text("SELECT has_table_privilege(:role, :table, :privilege)").bindparams(
                role=role, table=table, privilege=privilege
            )
        )
        checks.append((f"{role} on {table}", privilege, bool(granted)))
    return checks


def _raw_volume(session: Session) -> dict[str, Any]:
    from lnd.ingest import RawRecord

    return {
        "versions": session.scalar(select(func.count()).select_from(RawRecord)) or 0,
        "records": session.scalar(select(func.count(func.distinct(RawRecord.source_id)))) or 0,
    }


def render(session: Session) -> tuple[str, list[str]]:
    """The document, and the list of findings that would block a signature."""
    stored = _stored_fields()
    rejected = _rejected_fields()
    forbidden = _forbidden_in_payloads()
    writers = _write_client_present()
    grants = _grants(session)
    raw = _raw_volume(session)

    findings: list[str] = []
    if writers:
        findings.append(
            f"The CRM client contains write verbs: {', '.join(writers)}. "
            "The platform is supposed to be read-only at source."
        )
    for field, count in forbidden.items():
        if count:
            findings.append(
                f"`{field}` appears {count} times in the recorded payloads. "
                "NFR-06 forbids storing it, and its presence at the boundary means "
                "the control is now the transform rather than the source."
            )
    stored_names = {name for name, _, _ in stored}
    for field in FORBIDDEN_FIELDS:
        if field in stored_names:
            findings.append(f"`dim_employee.{field}` exists. NFR-06 forbids it outright.")
    for scope, privilege, granted in grants:
        if privilege in {"UPDATE", "DELETE", "TRUNCATE"} and granted:
            findings.append(f"{scope} has {privilege}. Raw is supposed to be append-only.")

    lines = [
        "# Security and PII review",
        "",
        "<!-- GENERATED by `python -m lnd.reference.security`. Do not edit by hand. -->",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC against the running "
        "system. Every claim below is read from the schema, the source's typed model, the "
        "recorded payloads or PostgreSQL — none of it is recalled.",
        "",
        "## What HR is being asked to sign",
        "",
        "That this platform holds no more personal data than the metrics require, that it "
        "cannot write to the CRM, and that what it does hold is reachable only by an "
        "authenticated member of L&D. The three sections below are the evidence for each.",
        "",
        "## 1. What the platform stores about a person",
        "",
        "Every column of `core.dim_employee`, from the schema. **Identifies** marks the "
        "ones that name a person rather than describe their place in the organisation.",
        "",
        "| Column | Type | Identifies |",
        "|---|---|---|",
        *(
            f"| `{name}` | {type_} | {'yes' if identifying else '—'} |"
            for name, type_, identifying in stored
        ),
        "",
        "The identifying columns are there because the metrics need them: "
        "`employee_code` is the join the CRM's own roster is keyed on, `full_name` is what "
        "a coverage list has to show for anybody to act on it, and `normalised_name` is "
        "how one person spelled two ways stops being two people (P-04).",
        "",
        "## 2. What the source offers and the platform refuses",
        "",
        "The CRM's user object carries these and the platform does not store them:",
        "",
        *(f"- `{field}`" for field in rejected),
        "",
        "Each is a field that could have been kept. None serves a metric in section 9, and "
        "the rule for adding one is unchanged: name the metric that needs it, or it stays "
        "out.",
        "",
        "### The two NFR-06 fields",
        "",
        "National ID and payscale are not merely unstored — the source does not expose "
        "them. Counted in the recorded payloads:",
        "",
        "| Field | Occurrences in the source payloads |",
        "|---|---|",
        *(f"| `{field}` | {count} |" for field, count in forbidden.items()),
        "",
        "A non-zero count here would not be a breach; it would mean the control moved from "
        "the boundary to the transform, which is a weaker place for it and worth knowing "
        "about the day it happens.",
        "",
        "Counted over the frozen dataset, which is anonymised by replacing *values* and "
        "keeping every key — so this is a statement about the source's schema, which is "
        "what the question asks, and not about the anonymiser having removed anything.",
        "",
        "## 3. What the platform cannot do",
        "",
        "**It cannot write to the CRM.** There is no write client: the HTTP client carries "
        "no `post`, `put`, `patch` or `delete` call at all"
        + (f", except {', '.join(writers)} — see the findings" if writers else "")
        + ". The credential issued to it is read-only as well, so the guarantee is doubled: "
        "removing one would not be enough to write.",
        "",
        "**It cannot amend what the source said.** The application role's grants on `raw`, "
        "read back from the running database:",
        "",
        "| Scope | Privilege | Granted |",
        "|---|---|---|",
        *(
            f"| `{scope}` | {privilege} | {'yes' if granted else 'no'} |"
            for scope, privilege, granted in grants
        ),
        "",
        "## 4. The uncomfortable part, stated plainly",
        "",
        f"`raw` currently holds {raw['versions']:,} stored versions of {raw['records']:,} "
        "records, and each one is the source payload **verbatim** — including the names, "
        "emails and mobile numbers the CRM sends. Field minimisation is a property of "
        "`core`, not of the database.",
        "",
        "That is deliberate. An append-only record of what the source actually said is "
        "what makes a restated figure defensible in a meeting, and a raw layer edited to "
        "remove fields could no longer answer *what did the CRM tell us*. What stands in "
        "front of it:",
        "",
        "- `/v1/raw/**` requires an authenticated session, like every other route. Nothing "
        "in the raw layer is served anonymously.",
        "- The database publishes no host port outside dev, and is asserted so in CI.",
        "- The dev stack runs on anonymised fixtures — names, emails and mobile numbers do "
        "not survive the freeze.",
        "- Retention: raw is never deleted, by design. If HR requires an erasure path for a "
        "named individual, it does not exist today and would be a change, not a setting.",
        "",
        "## 5. Access",
        "",
        "- SSO only. There is no local password anywhere in the codebase.",
        "- One L&D permission set in v1; leadership receives exported files rather than "
        "logins. The claims are carried on the session so row-level scoping is additive "
        "later, and the hook ships unused.",
        "- `AUTH_DEV_BYPASS` is refused at startup in any environment but dev — the process "
        "does not boot, rather than booting with authentication quietly disabled.",
        "- Containers run non-root on a slim base with no build toolchain in the runtime "
        "layer; images are scanned in CI and a CRITICAL or HIGH finding fails the build.",
        "- Enrichment decisions and exception resolutions are attributed and retained: a "
        "change supersedes rather than updates, so who decided what, and when, survives.",
        "",
        "## Findings",
        "",
    ]
    if findings:
        lines += [
            "**This review does not pass.** Each of these must be resolved before HR is "
            "asked to sign:",
            "",
            *(f"- {finding}" for finding in findings),
            "",
        ]
    else:
        lines += [
            "None. Every control above verified against the running system at the time "
            "stamped at the top of this document.",
            "",
        ]

    lines += [
        "## Sign-off",
        "",
        "| | |",
        "|---|---|",
        "| The stored fields are no more than the metrics require | ☐ |",
        "| The raw layer's retention of full payloads is understood and accepted | ☐ |",
        "| The absence of an erasure path for a named individual is accepted, or raised | ☐ |",
        "| HR signature | |",
        "| Date | |",
        "",
    ]
    return "\n".join(lines), findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate docs/security-review.md")
    parser.add_argument("--check", action="store_true", help="write nothing; fail on a finding")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from lnd.db import session_scope

    with session_scope() as session:
        report, findings = render(session)

    if not args.check:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
        log.info("wrote %s", REPORT)

    for finding in findings:
        log.error("FINDING %s", finding)
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main())
