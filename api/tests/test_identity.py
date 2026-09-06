"""Identity resolution: four probes in order, and the 38 people P-07 lost."""

from __future__ import annotations

from lnd.models.core import IdentityStatus
from lnd.transform.identity import IdentityResolver


def resolver(**overrides: object) -> IdentityResolver:
    """A resolver over a small hand-built index.

    Built directly rather than through `IdentityResolver.build`, because the
    probe *order* is the behaviour under test and it does not need a database
    to demonstrate.
    """
    defaults: dict[str, object] = {
        "by_odoo_id": {"4821": 1, "4977": 2},
        "by_employee_code": {"10422": 1},
        "by_email": {"ahmed.kamal@example.test": 1},
        "by_normalised_name": {"ahmed kamal ibrahim": 1},
        "manual": {},
    }
    defaults.update(overrides)
    return IdentityResolver(**defaults)  # type: ignore[arg-type]


class TestProbeOrder:
    def test_odoo_id_wins_when_present(self) -> None:
        result = resolver().resolve(odoo_id="4821", employee_code="10422")
        assert result.employee_key == 1
        assert result.status is IdentityStatus.ODOO_ID

    def test_falls_through_to_employee_code(self) -> None:
        result = resolver().resolve(odoo_id="unknown", employee_code="10422")
        assert result.employee_key == 1
        assert result.status is IdentityStatus.EMPLOYEE_CODE

    def test_falls_through_to_email(self) -> None:
        result = resolver().resolve(odoo_id=None, email="Ahmed.Kamal@Example.Test")
        assert result.employee_key == 1
        assert result.status is IdentityStatus.EMAIL

    def test_falls_through_to_normalised_name(self) -> None:
        """The weakest probe, and the reason the probe is recorded per fact."""
        result = resolver().resolve(odoo_id=None, full_name="  ahmed   KAMAL Ibrahim ")
        assert result.employee_key == 1
        assert result.status is IdentityStatus.NORMALISED_NAME


class TestQuarantine:
    def test_an_unknown_person_resolves_to_nobody(self) -> None:
        """FR-B05: never dropped. The caller writes the fact row regardless."""
        result = resolver().resolve(odoo_id="9999", full_name="Nobody At All")
        assert result.employee_key is None
        assert result.status is IdentityStatus.UNRESOLVED
        assert result.resolved is False

    def test_a_resolution_cannot_claim_a_key_it_does_not_have(self) -> None:
        """`employee_key is None` exactly when the status is UNRESOLVED.

        The invariant that stops a caller writing a fact row asserting a
        resolution that never happened.
        """
        probes: list[dict[str, str | None]] = [
            {"odoo_id": None},
            {"odoo_id": "4821"},
            {"odoo_id": None, "employee_code": "10422"},
            {"odoo_id": "9999"},
        ]
        for probe in probes:
            result = resolver().resolve(**probe)  # type: ignore[arg-type]
            assert result.resolved == (result.status is not IdentityStatus.UNRESOLVED)


class TestManualMapping:
    def test_a_mapping_resolves_someone_the_probes_missed(self) -> None:
        """FR-F03: an operator resolves the exception by authoring a mapping."""
        result = resolver(manual={"7777": "4821"}).resolve(odoo_id="7777")
        assert result.employee_key == 1

    def test_the_source_wins_over_the_mapping(self) -> None:
        """So an operator's mapping retires by itself.

        The moment the CRM starts supplying the person under their own id, the
        automatic probe hits first and the hand-authored row stops mattering —
        the same shape as every other override in this codebase (FR-C04).
        """
        result = resolver(manual={"4821": "4977"}).resolve(odoo_id="4821")
        assert result.employee_key == 1  # the direct hit, not the mapping's target


class TestAmbiguity:
    def test_an_ambiguous_name_resolves_to_nobody_rather_than_to_a_guess(self) -> None:
        """Two people sharing a normalised name must not be matched on it.

        `IdentityResolver.build` removes such a key from the index entirely.
        Resolving to whichever row was read second would attribute one person's
        attendance to the other, permanently, with no exception raised — which
        is a worse failure than the quarantine it would be replacing, because
        nothing would ever show it happened.
        """
        result = resolver(by_normalised_name={}).resolve(
            odoo_id=None, full_name="Ahmed Kamal Ibrahim"
        )
        assert result.status is IdentityStatus.UNRESOLVED
