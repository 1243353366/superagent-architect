"""Claims-layer tests: types, evidence, dependency graph, contradiction
detection, calibration bands, and the acceptance gate."""
import unittest

from saarch.claims import (
    BAND_CERTAIN, BAND_SUPPORTED, BAND_UNKNOWN_CONFLICTED, BAND_UNKNOWN_MISSING,
    Blocker, Claim, ClaimStore, Evidence, AcceptanceGate,
)


def store_with_basic_claims():
    s = ClaimStore()
    ev = s.add_evidence(Evidence(kind="prompt_excerpt",
                                 content="The app stores data locally",
                                 source="request"))
    s.add_claim(Claim(id="R1", type="requirement",
                      statement="Data stays local", evidence=[ev],
                      predicate={"subject": "data", "property": "network_use",
                                 "value": "none", "polarity": "positive"}))
    return s


class ClaimStoreTests(unittest.TestCase):
    def test_no_evidence_is_unknown_missing(self):
        s = ClaimStore()
        s.add_claim(Claim(id="R1", type="requirement", statement="unstated thing"))
        self.assertEqual(s.band("R1"), BAND_UNKNOWN_MISSING)

    def test_supported_with_evidence(self):
        s = store_with_basic_claims()
        self.assertEqual(s.band("R1"), BAND_SUPPORTED)

    def test_certain_with_independent_kinds(self):
        s = store_with_basic_claims()
        t = s.add_evidence(Evidence(kind="test_result",
                                    content="probe: no outbound data call",
                                    source="T-local-strength"))
        s.claims["R1"].evidence.append(t)
        self.assertEqual(s.band("R1"), BAND_CERTAIN)

    def test_contradiction_needs_two_asserted_sides(self):
        # hypotheses without evidence never contradict
        s = store_with_basic_claims()
        s.add_claim(Claim(id="I1", type="interpretation",
                          statement="Alt: sync allowed",
                          predicate={"subject": "data",
                                     "property": "network_use",
                                     "value": "storage_only"}))
        self.assertEqual(s.detect_contradictions(), [])
        # ...but once both sides are asserted, the conflict is detected
        ev = s.add_evidence(Evidence(kind="user_answer",
                                     content="sync is allowed", source="call"))
        s.claims["I1"].evidence.append(ev)
        found = s.detect_contradictions()
        self.assertEqual(len(found), 1)
        self.assertEqual(s.band("R1"), BAND_UNKNOWN_CONFLICTED)
        self.assertEqual(s.band("I1"), BAND_UNKNOWN_CONFLICTED)

    def test_dependency_graph_propagates_broken_support(self):
        s = ClaimStore()
        base_ev = s.add_evidence(Evidence(kind="test_result",
                                          content="probe passed", source="T"))
        s.add_claim(Claim(id="B1", type="derived_fact",
                          statement="Base behavior holds", evidence=[base_ev]))
        # child depends on the base; base then loses all evidence
        s.add_claim(Claim(id="C1", type="requirement",
                          statement="Derived requirement",
                          evidence=["E1"], depends_on=["B1"]))
        self.assertEqual(s.band("C1"), BAND_SUPPORTED)
        s.claims["B1"].evidence = []
        self.assertEqual(s.band("C1"), BAND_UNKNOWN_MISSING)

    def test_missing_and_conflicted_are_distinct_bands(self):
        s = ClaimStore()
        s.add_claim(Claim(id="M", type="requirement", statement="no evidence"))
        ev1 = s.add_evidence(Evidence(kind="prompt_excerpt", content="a",
                                      source="s"))
        s.add_claim(Claim(id="X", type="requirement", statement="has both sides",
                          evidence=[ev1],
                          predicate={"subject": "s", "property": "p",
                                     "value": "1", "polarity": "positive"}))
        s.add_claim(Claim(id="Y", type="interpretation",
                          statement="counter",
                          evidence=[ev1],
                          predicate={"subject": "s", "property": "p",
                                     "value": "2", "polarity": "positive"}))
        s.detect_contradictions()
        self.assertEqual(s.band("M"), BAND_UNKNOWN_MISSING)
        self.assertEqual(s.band("X"), BAND_UNKNOWN_CONFLICTED)
        self.assertNotEqual(s.calibration("M")["remedy"],
                            s.calibration("X")["remedy"])

    def test_calibration_reports_remedies(self):
        s = ClaimStore()
        s.add_claim(Claim(id="M", type="requirement", statement="none"))
        self.assertIn("ask", s.calibration("M")["remedy"])


class GateTests(unittest.TestCase):
    def test_gate_blocks_unsupported_requirement(self):
        s = ClaimStore()
        s.add_claim(Claim(id="R1", type="requirement",
                          statement="A requirement with no evidence"))
        g = AcceptanceGate(s)
        r = g.certify()
        self.assertFalse(r["certified"])
        self.assertEqual(r["blockers"][0]["reason"], "unsupported")
        self.assertEqual(r["blockers"][0]["band"], BAND_UNKNOWN_MISSING)

    def test_gate_blocks_contradicted_requirement(self):
        s = ClaimStore()
        ev = s.add_evidence(Evidence(kind="prompt_excerpt", content="x",
                                    source="s"))
        s.add_claim(Claim(id="A", type="requirement", statement="a",
                          evidence=[ev],
                          predicate={"subject": "s", "property": "p",
                                     "value": "1"}))
        s.add_claim(Claim(id="B", type="interpretation", statement="b",
                          evidence=[ev],
                          predicate={"subject": "s", "property": "p",
                                     "value": "2"}))
        g = AcceptanceGate(s)
        r = g.certify()
        self.assertFalse(r["certified"])
        self.assertEqual(r["blockers"][0]["reason"], "contradicted")

    def test_gate_accepts_supported_requirement(self):
        s = store_with_basic_claims()
        r = AcceptanceGate(s).certify()
        self.assertTrue(r["certified"])
        self.assertEqual(r["accepted"], ["R1"])

    def test_gate_accepts_only_requirements(self):
        s = store_with_basic_claims()
        s.add_claim(Claim(id="I9", type="interpretation", statement="hyp"))
        r = AcceptanceGate(s).certify()
        self.assertNotIn("I9", r["accepted"])


if __name__ == "__main__":
    unittest.main()
