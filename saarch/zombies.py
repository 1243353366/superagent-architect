"""Zombies: verified failures kept walking until their disposition is written.

A zombie is a security failure that is, by policy, NOT allowed to die
quietly:

  05 - VERIFY     the failure reproduces against a fresh in-memory core
  05 - PRESERVE   the original exploit and the minimized repro are both kept
  05 - RETIRE     only with a written disposition (fix + regression test)

What makes the schema useful, and hostile to attacker reuse:

  * SELF-CONTAINED repro - the recipe runs against a fresh in-memory
    TrustedCore. No live system, no secrets, no network. An attacker who
    steals the ledger gains zero access to anything.
  * TAMPER-EVIDENT - records are hash-chained; any edit to a zombie or the
    ledger order is detectable by one verify() call. Forging is wasted
    effort, detectable at a glance.
  * DEFENDER-CHEAP, ATTACKER-EXPENSIVE - minimized repros replay in
    milliseconds inside CI, but exploiting what they describe requires
    reconstructing the whole system context, and even a working repro
    grants no authority: the defenses under test live in the artifact.
  * NO SILENT DEATH - retiring requires a documented disposition; evidence
    cannot disappear without a trace.
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

ZOMBIE_STATUSES = ("walking", "minimized", "fixed", "retired")


def _hash_record(prev: str, payload: str) -> str:
    return hashlib.sha256((prev + "|" + payload).encode()).hexdigest()[:16]


@dataclass
class Zombie:
    id: str
    title: str
    status: str = "walking"            # walking -> minimized -> fixed -> retired
    discovered: Dict[str, Any] = field(default_factory=dict)
    repro: Dict[str, Any] = field(default_factory=dict)
    original: Dict[str, Any] = field(default_factory=dict)
    disposition: Optional[Dict[str, Any]] = None

    def chain_payload(self) -> str:
        return json.dumps({
            "id": self.id, "title": self.title, "status": self.status,
            "discovered": self.discovered, "repro": self.repro,
            "original": self.original,
            "disposition": self.disposition,
        }, sort_keys=True, default=str)


class ZombieLedger:
    """Tamper-evident append-only ledger of zombie records.

    verify() recomputes the hash chain; any mutation of a record, its
    order, or removal of evidence is detected. Nothing here grants system
    access — the ledger is data, self-contained by design.
    """

    def __init__(self) -> None:
        self.zombies: List[Zombie] = []
        self._prev = "GENESIS"

    # -- lifecycle ---------------------------------------------------------
    def raise_zombie(self, title: str, discovered: Dict[str, Any],
                     repro: Dict[str, Any],
                     original: Optional[Dict[str, Any]] = None) -> Zombie:
        z = Zombie(id=f"Z{len(self.zombies) + 1}", title=title,
                   discovered=discovered, repro=repro,
                   original=original or {})
        self._append(z)
        return z

    def minimize(self, zombie_id: str, minimized_repro: Dict[str, Any],
                 retained_breach: str) -> Zombie:
        z = self._get(zombie_id)
        self._require_status(z, "walking")
        z.repro = minimized_repro
        z.discovered["retained_breach"] = retained_breach
        z.status = "minimized"
        self._rechain()
        return z

    def mark_fixed(self, zombie_id: str, fix_summary: str) -> Zombie:
        z = self._get(zombie_id)
        self._require_status(z, "minimized")
        z.status = "fixed"
        z.disposition = {"fix_summary": fix_summary}
        self._rechain()
        return z

    def retire(self, zombie_id: str, regression_test: str,
               fix_note: str, retired_at: str) -> Zombie:
        """Retiring demands a documented disposition. No disposition, no
        retirement — a zombie cannot die silently."""
        z = self._get(zombie_id)
        self._require_status(z, "fixed")
        if not regression_test or not fix_note or not retired_at:
            raise ValueError("retirement requires regression test, fix "
                             "note, and date — undocumented zombies stay "
                             "walking")
        z.status = "retired"
        z.disposition = {"regression_test": regression_test,
                         "fix_note": fix_note, "retired_at": retired_at}
        self._rechain()
        return z

    # -- integrity ---------------------------------------------------------
    def verify(self) -> List[str]:
        problems = []
        prev = "GENESIS"
        for z in self.zombies:
            expect = _hash_record(prev, z.chain_payload())
            if expect != getattr(z, "_hash", expect):
                problems.append(f"{z.id}: record was mutated after chaining")
            prev = getattr(z, "_hash", expect)
        return problems

    def to_json(self) -> str:
        return json.dumps([{"_hash": getattr(z, "_hash", ""),
                            "record": json.loads(z.chain_payload())}
                           for z in self.zombies], indent=1, default=str)

    # -- internals ----------------------------------------------------------
    def _append(self, z: Zombie) -> None:
        z._hash = _hash_record(self._prev, z.chain_payload())  # noqa: SLF001
        self._prev = z._hash
        self.zombies.append(z)

    def _rechain(self) -> None:
        prev = "GENESIS"
        for z in self.zombies:
            z._hash = _hash_record(prev, z.chain_payload())  # noqa: SLF001
            prev = z._hash
        self._prev = prev

    def _get(self, zombie_id: str) -> Zombie:
        for z in self.zombies:
            if z.id == zombie_id:
                return z
        raise KeyError(f"no such zombie: {zombie_id}")

    @staticmethod
    def _require_status(z: Zombie, want: str) -> None:
        if z.status != want:
            raise ValueError(f"{z.id} is '{z.status}', not '{want}'; the "
                             f"lifecycle is {ZOMBIE_STATUSES}")
