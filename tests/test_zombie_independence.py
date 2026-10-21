"""The independence matrix: Z1 and Z2 each have an EXCLUSIVE detection
surface. Breaking only Z1's guard fails only the Z1 scenario; breaking
only Z2's guard fails only the Z2 scenario. Coupling is measured, not
assumed — if a mutation ever breaks both, the matrix says so honestly.
This is the verification evidence for gap G-001's closure."""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)

from saarch.trusted_core import TrustedCore              # noqa: E402
from tests.test_zombie_regressions import (z1_scenario,   # noqa: E402
                                           z2_scenario)


class MutantZ1(TrustedCore):
    """Z1's guard removed: certification succeeds regardless of
    evidence quality (engine-only support would certify)."""

    def certify(self):
        result = self.gate.certify()
        result["certified"] = True
        result.pop("engine_only_blockers", None)
        result.pop("ambiguous_blockers", None)
        return result


class MutantZ2Rollback(TrustedCore):
    """Z2's rollback guard removed: rollback claims success but
    restores nothing (the answered question survives a rollback)."""

    def rollback(self, checkpoint_id):
        self._record("rollback", checkpoint=checkpoint_id,
                    claims_restored=-1)
        return True


class _NoCollisionDict(dict):
    def __contains__(self, key):
        return False


def mutant_z2_collision():
    class Mutant(TrustedCore):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._question_index = _NoCollisionDict(
                self._question_index)
    return Mutant


def _raises(fn):
    try:
        fn()
    except AssertionError:
        return True
    return False


class IndependenceMatrix(unittest.TestCase):

    def test_baseline_real_core_passes_both(self):
        z1_scenario(TrustedCore)
        z2_scenario(TrustedCore)

    def test_z1_mutant_breaks_only_z1(self):
        self.assertTrue(_raises(lambda: z1_scenario(MutantZ1)),
                        "Z1 mutant must be caught by the Z1 scenario")
        z2_scenario(MutantZ1)   # Z2 detection unaffected

    def test_z2_rollback_mutant_breaks_only_z2(self):
        self.assertTrue(
            _raises(lambda: z2_scenario(MutantZ2Rollback)),
            "Z2 rollback mutant must be caught by the Z2 scenario")
        z1_scenario(MutantZ2Rollback)

    def test_z2_collision_mutant_breaks_only_z2(self):
        self.assertTrue(
            _raises(lambda: z2_scenario(mutant_z2_collision())),
            "Z2 collision mutant must be caught by the Z2 scenario")
        z1_scenario(mutant_z2_collision())

    def test_coupling_is_measured_not_assumed(self):
        """Every mutant that exists breaks exactly one scenario. If a
        future mutation breaks both, this test gains a row and the
        coupling is reported — never hidden."""
        mutants = {"MutantZ1": MutantZ1,
                   "MutantZ2Rollback": MutantZ2Rollback,
                   "MutantZ2Collision": mutant_z2_collision()}
        for name, cls in mutants.items():
            breaks = (_raises(lambda c=cls: z1_scenario(c))
                      + _raises(lambda c=cls: z2_scenario(c)))
            self.assertEqual(breaks, 1,
                             f"{name} breaks {breaks} scenarios — "
                             "each mutant must break exactly one")
