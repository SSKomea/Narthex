"""Capability-boundary enforcement for Cathedral and Bazaar modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import AgentSpec, Mode, NodeSpec


BAZAAR_PROPOSAL_PREFIXES = ("propose", "consult", "review", "read")


def capability_grants(granted: Iterable[str], required: str) -> bool:
    """Return True when a granted capability set satisfies ``required``.

    Wildcards are intentionally narrow: ``execute.*`` matches
    ``execute.write`` but not arbitrary names.  A bare ``*`` is an explicit
    administrator escape hatch and should be used sparingly.
    """
    granted_set = set(granted)
    if "*" in granted_set or required in granted_set:
        return True
    for capability in granted_set:
        if capability.endswith(".*") and required.startswith(capability[:-1]):
            return True
    return False


def intersect_capabilities(left: Iterable[str], right: Iterable[str]) -> frozenset[str]:
    """Return the safest exact representation of two capability sets.

    A wildcard can cover an exact capability on the other side.  When that
    happens the exact capability is kept, so a broad declaration cannot
    silently widen a narrower approval scope.
    """
    left_set = frozenset(left)
    right_set = frozenset(right)
    result: set[str] = set()
    for left_cap in left_set:
        for right_cap in right_set:
            if left_cap == right_cap:
                result.add(left_cap)
            elif capability_grants((right_cap,), left_cap):
                result.add(left_cap)
            elif capability_grants((left_cap,), right_cap):
                result.add(right_cap)
    return frozenset(result)


@dataclass(frozen=True)
class CapabilityPolicy:
    """Calculates the effective capabilities for one node execution."""

    bazaar_prefixes: tuple[str, ...] = BAZAAR_PROPOSAL_PREFIXES

    def declared(self, agent: AgentSpec, node: NodeSpec) -> frozenset[str]:
        if node.capabilities is None:
            return agent.capabilities
        return intersect_capabilities(agent.capabilities, node.capabilities)

    def _proposal_only(self, declared: Iterable[str]) -> frozenset[str]:
        return frozenset(
            capability
            for capability in declared
            if any(
                capability == prefix or capability.startswith(prefix + ".")
                for prefix in self.bazaar_prefixes
            )
        )

    def proposal_capabilities(
        self, agent: AgentSpec, node: NodeSpec, mode: Mode
    ) -> frozenset[str]:
        declared = self.declared(agent, node)
        if mode is Mode.BAZAAR:
            allowed = set(self._proposal_only(declared))
            extra = frozenset(node.metadata.get("bazaar_capabilities", ()))
            # A node cannot opt back into execution in Bazaar: the whitelist
            # may only add proposal/consult/review/read capabilities.
            allowed.update(intersect_capabilities(declared, self._proposal_only(extra)))
            return frozenset(allowed)
        # Cathedral proposals may explore, but cannot execute or approve.
        return self._proposal_only(declared)

    def effective_capabilities(
        self,
        agent: AgentSpec,
        node: NodeSpec,
        mode: Mode,
        *,
        approved: bool = False,
    ) -> frozenset[str]:
        declared = self.declared(agent, node)
        if mode is Mode.BAZAAR:
            return self.proposal_capabilities(agent, node, mode)
        if not approved:
            return self.proposal_capabilities(agent, node, mode)
        scope = node.approval.scope
        if not scope:
            # Least privilege: an omitted scope grants only proposal-side
            # capabilities.  Execution must be requested explicitly.
            return self.proposal_capabilities(agent, node, mode)
        return intersect_capabilities(declared, scope)

    def can_execute(
        self,
        agent: AgentSpec,
        node: NodeSpec,
        mode: Mode,
        required: str,
        *,
        approved: bool = False,
    ) -> bool:
        caps = self.effective_capabilities(agent, node, mode, approved=approved)
        return capability_grants(caps, required)
