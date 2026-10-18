"""Narthex runtime engine: execution, gates, consensus, snapshots, replay."""

from __future__ import annotations

import dataclasses
import json
import secrets
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..audit import AuditLog
from ..capabilities import CapabilityPolicy, capability_grants
from ..errors import NarthexError
from ..events import EventKind
from ..governance import ExecutionSignals, GovernanceDecision, TopologyGovernor, text_divergence
from ..models import Mode, NodeSpec, Topology
from .agents import (
    Agent,
    AgentContext,
    AgentOutput,
    AgentRegistry,
    Review,
    ReviewContext,
    ReviewDecision,
)
from .approval import ApprovalContext, ApprovalProvider, AutoApprovalProvider
from .snapshot import Snapshot, create_snapshot, safe_component
from .state import NodeResult, RunResult, RunState
from .tools import ToolInvoker, ToolRegistry


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class RuntimeOptions:
    run_root: str | Path = "runs"
    run_id: str | None = None
    auto_approve: bool | None = None
    approved_nodes: frozenset[str] = frozenset()
    denied_nodes: frozenset[str] = frozenset()
    max_rounds: int | None = None
    snapshot_every_node: bool | None = None

    def merged(self, override: "RuntimeOptions | None") -> "RuntimeOptions":
        if override is None:
            return self
        return dataclasses.replace(
            self,
            run_root=override.run_root if override.run_root != "runs" else self.run_root,
            run_id=override.run_id if override.run_id is not None else self.run_id,
            auto_approve=(
                override.auto_approve
                if override.auto_approve is not None
                else self.auto_approve
            ),
            approved_nodes=override.approved_nodes or self.approved_nodes,
            denied_nodes=override.denied_nodes or self.denied_nodes,
            max_rounds=(
                override.max_rounds if override.max_rounds is not None else self.max_rounds
            ),
            snapshot_every_node=(
                override.snapshot_every_node
                if override.snapshot_every_node is not None
                else self.snapshot_every_node
            ),
        )


class NarthexRuntime:
    """Execute a topology with per-node governance decisions."""

    def __init__(
        self,
        topology: Topology,
        agents: AgentRegistry | Mapping[str, Agent],
        *,
        tools: ToolRegistry | None = None,
        options: RuntimeOptions | None = None,
        workdir: str | Path | None = None,
        approval_provider: ApprovalProvider | None = None,
    ) -> None:
        topology.validate()
        self.topology = topology
        self.agents = agents if isinstance(agents, AgentRegistry) else AgentRegistry(agents)
        self.tools = tools or ToolRegistry()
        self.options = options or RuntimeOptions()
        self.run_root = Path(workdir or self.options.run_root)
        self.policy = CapabilityPolicy()
        self.governor = TopologyGovernor(topology)
        self.approval_provider = approval_provider
        self._snapshot_paths: list[str] = []

    # ------------------------------------------------------------------
    # Public execution API
    # ------------------------------------------------------------------
    def run(
        self,
        task: str,
        *,
        options: RuntimeOptions | None = None,
    ) -> RunResult:
        merged = self.options.merged(options)
        return self._run(task, merged, restore_state=None, start_node=None, replay_snapshot=None)

    def replay(
        self,
        snapshot: Snapshot | str | Path,
        *,
        from_node: str | None = None,
        task: str | None = None,
        options: RuntimeOptions | None = None,
    ) -> RunResult:
        snap = snapshot if isinstance(snapshot, Snapshot) else Snapshot.load(snapshot)
        topology = Topology.from_dict(snap.topology)
        merged = self.options.merged(options)
        runtime = NarthexRuntime(
            topology,
            self.agents,
            tools=self.tools,
            options=merged,
            workdir=self.run_root,
            approval_provider=self.approval_provider,
        )
        state = RunState.from_dict(snap.state)
        start_node = from_node or snap.node_id
        plan = topology.order()
        if start_node not in plan:
            raise NarthexError(f"replay node not found in topology: {start_node!r}")
        start_index = plan.index(start_node)

        # Keep results before the replay point and discard outcomes that will
        # be recomputed on this branch.
        for node_id in plan[start_index:]:
            state.results.pop(node_id, None)
            if node_id in state.completed:
                state.completed.remove(node_id)
        state.total_tokens = sum(result.token_cost for result in state.results.values())
        state.max_divergence = max(
            (result.divergence for result in state.results.values()), default=0.0
        )
        state.parent_run_id = snap.run_id
        state.branch_id = f"{snap.branch_id or snap.run_id}/replay-{secrets.token_hex(2)}"
        state.replay_from = start_node
        state.status = "running"
        state.error = None
        if task is not None:
            state.task = task

        return runtime._run(
            state.task,
            merged,
            restore_state=state,
            start_node=start_node,
            replay_snapshot=snap,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _run(
        self,
        task: str,
        options: RuntimeOptions,
        *,
        restore_state: RunState | None,
        start_node: str | None,
        replay_snapshot: Snapshot | None,
    ) -> RunResult:
        self.topology.validate()
        run_id = self._allocate_run_id(options.run_id)
        run_dir = self.run_root / safe_component(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir()
        audit_path = run_dir / "audit.jsonl"
        audit = AuditLog(audit_path, run_id)
        self._snapshot_paths = []

        if restore_state is None:
            state = RunState(run_id=run_id, task=task, status="running", branch_id=run_id)
        else:
            state = restore_state
            state.run_id = run_id
            state.task = task
            state.status = "running"

        plan = self.topology.order()
        start_index = plan.index(start_node) if start_node else 0
        approval_provider = self.approval_provider or AutoApprovalProvider(
            auto_approve=True if options.auto_approve is None else options.auto_approve,
            approved_nodes=options.approved_nodes,
            denied_nodes=options.denied_nodes,
        )

        if replay_snapshot is None:
            audit.emit(
                EventKind.RUN_STARTED,
                mode=state.last_mode,
                topology=self.topology.name,
                task=task,
                plan=list(plan),
            )
        else:
            audit.emit(
                EventKind.REPLAY_STARTED,
                mode=replay_snapshot.mode,
                topology=self.topology.name,
                task=task,
                snapshot=replay_snapshot.snapshot_id,
                snapshot_path=replay_snapshot.path,
                from_node=start_node,
                parent_run_id=replay_snapshot.run_id,
            )

        try:
            for index in range(start_index, len(plan)):
                node_id = plan[index]
                if node_id in state.completed:
                    continue
                node_task = self.topology.nodes[node_id].task or task
                node_result = self._execute_node(
                    node_id=node_id,
                    task=node_task,
                    state=state,
                    audit=audit,
                    run_dir=run_dir,
                    artifacts_dir=artifacts_dir,
                    plan=plan,
                    plan_index=index,
                    options=options,
                    approval_provider=approval_provider,
                )
                if node_result.status != "completed":
                    break

            if state.status == "running":
                state.status = "completed"
                audit.emit(
                    EventKind.RUN_COMPLETED,
                    mode=state.last_mode,
                    completed=list(state.completed),
                    total_tokens=state.total_tokens,
                    max_divergence=state.max_divergence,
                )
                if replay_snapshot is not None:
                    audit.emit(
                        EventKind.REPLAY_COMPLETED,
                        mode=state.last_mode,
                        from_node=start_node,
                        parent_run_id=replay_snapshot.run_id,
                    )
            elif state.status == "blocked":
                audit.emit(
                    EventKind.RUN_BLOCKED,
                    mode=state.last_mode,
                    completed=list(state.completed),
                    error=state.error,
                )
            elif state.status == "failed":
                audit.emit(
                    EventKind.RUN_FAILED,
                    mode=state.last_mode,
                    completed=list(state.completed),
                    error=state.error,
                )
        except Exception as exc:  # defensive boundary
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            audit.emit(
                EventKind.RUN_FAILED,
                mode=state.last_mode,
                completed=list(state.completed),
                error=state.error,
                traceback=traceback.format_exc(),
            )
        finally:
            audit.close()

        run_result = self._build_result(state, run_dir, audit_path, plan)
        self._write_summary(run_dir, run_result)
        return run_result

    # ------------------------------------------------------------------
    # Node execution
    # ------------------------------------------------------------------
    def _execute_node(
        self,
        *,
        node_id: str,
        task: str,
        state: RunState,
        audit: AuditLog,
        run_dir: Path,
        artifacts_dir: Path,
        plan: tuple[str, ...],
        plan_index: int,
        options: RuntimeOptions,
        approval_provider: ApprovalProvider,
    ) -> NodeResult:
        node = self.topology.nodes[node_id]
        agent_spec = self.topology.agents[node.agent]
        agent = self.agents.get(node.agent)
        incoming = self.topology.incoming(node_id)
        consult_sources = tuple(
            edge.source for edge in incoming if edge.type.value == "consult"
        )
        authorize_sources = tuple(
            edge.source for edge in incoming if edge.type.value == "authorize"
        )
        signals = ExecutionSignals(
            incoming_consult=consult_sources,
            incoming_authorize=authorize_sources,
            cumulative_tokens=state.total_tokens,
            divergence=state.max_divergence,
        )
        decision = self.governor.decide(node, signals)

        if node.mode is Mode.BAZAAR and decision.mode is Mode.CATHEDRAL:
            audit.emit(
                EventKind.POLICY_OVERRIDE,
                node_id=node_id,
                agent_id=node.agent,
                mode=decision.mode,
                requested_mode=node.mode.value,
                reasons=list(decision.reasons),
            )
        audit.emit(
            EventKind.MODE_DECISION,
            node_id=node_id,
            agent_id=node.agent,
            mode=decision.mode,
            reasons=list(decision.reasons),
            forced=decision.forced,
            snapshot_required=decision.snapshot_required,
        )

        if state.last_mode is None:
            state.last_mode = decision.mode.value
        elif state.last_mode != decision.mode.value:
            self._record_mode_switch(
                state=state,
                audit=audit,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                node_id=node_id,
                old_mode=state.last_mode,
                decision=decision,
                reason="governor_decision",
            )

        started_at = _utc_now()
        audit.emit(
            EventKind.NODE_STARTED,
            node_id=node_id,
            agent_id=node.agent,
            mode=decision.mode,
            phase=node.phase,
            risk=sorted(node.risk),
            reasons=list(decision.reasons),
        )
        if options.snapshot_every_node:
            self._write_snapshot(
                state=state,
                audit=audit,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                node_id=node_id,
                reason="node_start",
                mode=decision.mode.value,
            )

        proposal_caps = self.policy.proposal_capabilities(agent_spec, node, decision.mode)
        invoker = ToolInvoker(
            self.tools,
            run_id=state.run_id,
            node_id=node_id,
            agent_id=node.agent,
            mode=decision.mode,
            capabilities=proposal_caps,
            artifacts_dir=artifacts_dir,
            audit=audit,
            metadata=node.metadata,
        )
        context = AgentContext(
            run_id=state.run_id,
            topology_name=self.topology.name,
            node=node,
            agent=agent_spec,
            task=task,
            mode=decision.mode,
            capabilities=proposal_caps,
            tools=invoker,
            upstream={key: value.to_view() for key, value in state.results.items()},
        )
        try:
            output = agent.propose(context)
            if not isinstance(output, AgentOutput):
                raise TypeError(
                    f"agent {node.agent!r} returned {type(output).__name__}, "
                    "expected AgentOutput"
                )
        except Exception as exc:
            return self._fail_node(
                state=state,
                audit=audit,
                node=node,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                started_at=started_at,
                decision=decision,
                error=f"proposal failed: {type(exc).__name__}: {exc}",
                traceback_text=traceback.format_exc(),
            )

        node_tokens = output.token_cost or 0
        state.total_tokens += node_tokens
        audit.emit(
            EventKind.PROPOSE,
            node_id=node_id,
            agent_id=node.agent,
            mode=decision.mode,
            content=output.content,
            confidence=output.confidence,
            token_cost=output.token_cost,
            actions=[action.to_dict() for action in output.actions],
        )

        if decision.mode is Mode.BAZAAR:
            return self._run_bazaar(
                node=node,
                agent=agent,
                task=task,
                output=output,
                node_tokens=node_tokens,
                state=state,
                audit=audit,
                run_dir=run_dir,
                artifacts_dir=artifacts_dir,
                plan=plan,
                plan_index=plan_index,
                options=options,
                approval_provider=approval_provider,
                started_at=started_at,
                decision=decision,
                consult_sources=consult_sources,
            )
        return self._run_cathedral(
            node=node,
            agent=agent,
            task=task,
            output=output,
            node_tokens=node_tokens,
            state=state,
            audit=audit,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            plan=plan,
            plan_index=plan_index,
            approval_provider=approval_provider,
            started_at=started_at,
            decision=decision,
            divergence=state.max_divergence,
        )

    # ------------------------------------------------------------------
    # Bazaar channel
    # ------------------------------------------------------------------
    def _run_bazaar(
        self,
        *,
        node: NodeSpec,
        agent: Agent,
        task: str,
        output: AgentOutput,
        node_tokens: int,
        state: RunState,
        audit: AuditLog,
        run_dir: Path,
        artifacts_dir: Path,
        plan: tuple[str, ...],
        plan_index: int,
        options: RuntimeOptions,
        approval_provider: ApprovalProvider,
        started_at: str,
        decision: GovernanceDecision,
        consult_sources: tuple[str, ...],
    ) -> NodeResult:
        max_rounds = max(1, options.max_rounds or self.topology.governor.max_rounds)
        current = output
        reviews: list[dict[str, Any]] = []
        current_decision = decision

        for round_index in range(max_rounds):
            round_reviews: list[Review] = []
            round_records: list[dict[str, Any]] = []
            for source_id in consult_sources:
                source_node = self.topology.nodes[source_id]
                reviewer_spec = self.topology.agents[source_node.agent]
                reviewer = self.agents.get(source_node.agent)
                review = reviewer.review(
                    ReviewContext(
                        run_id=state.run_id,
                        topology_name=self.topology.name,
                        node=node,
                        agent=reviewer_spec,
                        mode=Mode.BAZAAR,
                        proposal=current,
                        proposal_agent_id=node.agent,
                        task=task,
                        upstream={key: value.to_view() for key, value in state.results.items()},
                        round_index=round_index,
                    )
                )
                node_tokens += review.token_cost
                state.total_tokens += review.token_cost
                round_reviews.append(review)
                round_records.append(
                    {
                        "reviewer": source_node.agent,
                        "direction": "source_reviews_target",
                        "round": round_index,
                        **review.to_dict(),
                    }
                )
                self._audit_review(
                    audit,
                    node_id=node.id,
                    reviewer_id=source_node.agent,
                    review=review,
                    mode=Mode.BAZAAR,
                    round_index=round_index,
                    direction="source_reviews_target",
                )

                source_result = state.results.get(source_id)
                if source_result is not None:
                    reverse_ctx = ReviewContext(
                        run_id=state.run_id,
                        topology_name=self.topology.name,
                        node=source_node,
                        agent=self.topology.agents[node.agent],
                        mode=Mode.BAZAAR,
                        proposal=AgentOutput(
                            content=source_result.output,
                            confidence=source_result.confidence,
                        ),
                        proposal_agent_id=source_node.agent,
                        task=task,
                        upstream={key: value.to_view() for key, value in state.results.items()},
                        round_index=round_index,
                    )
                    reverse = agent.review(reverse_ctx)
                    node_tokens += reverse.token_cost
                    state.total_tokens += reverse.token_cost
                    round_reviews.append(reverse)
                    round_records.append(
                        {
                            "reviewer": node.agent,
                            "direction": "target_reviews_source",
                            "source_node": source_id,
                            "round": round_index,
                            **reverse.to_dict(),
                        }
                    )
                    self._audit_review(
                        audit,
                        node_id=node.id,
                        reviewer_id=node.agent,
                        review=reverse,
                        mode=Mode.BAZAAR,
                        round_index=round_index,
                        direction="target_reviews_source",
                        source_node=source_id,
                    )

            reviews.extend(round_records)
            endorse_count = sum(
                1
                for review in round_reviews
                if review.decision in (ReviewDecision.ENDORSE, ReviewDecision.APPROVE)
            )
            hard_stop = any(
                review.decision in (ReviewDecision.DENY, ReviewDecision.ESCALATE)
                for review in round_reviews
            )
            total_reviews = len(round_reviews)
            consensus = (
                total_reviews == 0
                or endorse_count / total_reviews
                >= self.topology.governor.consensus_threshold
            )
            challenge_ratio = 1.0 - endorse_count / total_reviews if total_reviews else 0.0
            upstream_texts = [
                state.results[source_id].output
                for source_id in consult_sources
                if source_id in state.results
            ]
            divergence = text_divergence([current.content, *upstream_texts])
            state.max_divergence = max(state.max_divergence, divergence)

            if consensus:
                audit.emit(
                    EventKind.CONSENSUS_REACHED,
                    node_id=node.id,
                    agent_id=node.agent,
                    mode=Mode.BAZAAR,
                    round=round_index,
                    endorse_ratio=endorse_count / total_reviews if total_reviews else 1.0,
                    divergence=divergence,
                )
            else:
                audit.emit(
                    EventKind.CHALLENGE,
                    node_id=node.id,
                    agent_id=node.agent,
                    mode=Mode.BAZAAR,
                    round=round_index,
                    reason="consensus_not_reached",
                    endorse_ratio=endorse_count / total_reviews if total_reviews else 0.0,
                    divergence=divergence,
                )

            arbitration_signals = ExecutionSignals(
                incoming_consult=consult_sources,
                cumulative_tokens=state.total_tokens,
                divergence=divergence,
                round_index=round_index,
                challenge_ratio=challenge_ratio,
                no_consensus=(not consensus and round_index + 1 >= max_rounds),
            )
            redecision = self.governor.decide(node, arbitration_signals)
            if hard_stop or redecision.mode is Mode.CATHEDRAL:
                reasons = list(redecision.reasons)
                if hard_stop:
                    reasons.append("bazaar_hard_stop")
                arbitration = dataclasses.replace(
                    redecision, mode=Mode.CATHEDRAL, reasons=tuple(reasons)
                )
                self._record_mode_switch(
                    state=state,
                    audit=audit,
                    run_dir=run_dir,
                    plan=plan,
                    plan_index=plan_index,
                    node_id=node.id,
                    old_mode=state.last_mode or Mode.BAZAAR.value,
                    decision=arbitration,
                    reason="bazaar_arbitration",
                )
                return self._run_cathedral(
                    node=node,
                    agent=agent,
                    task=task,
                    output=current,
                    node_tokens=node_tokens,
                    state=state,
                    audit=audit,
                    run_dir=run_dir,
                    artifacts_dir=artifacts_dir,
                    plan=plan,
                    plan_index=plan_index,
                    approval_provider=approval_provider,
                    started_at=started_at,
                    decision=arbitration,
                    divergence=divergence,
                    reviews=reviews,
                )

            if consensus:
                current_decision = decision
                break

            if round_index + 1 < max_rounds:
                caps = self.policy.proposal_capabilities(
                    self.topology.agents[node.agent], node, Mode.BAZAAR
                )
                context = AgentContext(
                    run_id=state.run_id,
                    topology_name=self.topology.name,
                    node=node,
                    agent=self.topology.agents[node.agent],
                    task=task,
                    mode=Mode.BAZAAR,
                    capabilities=caps,
                    tools=ToolInvoker(
                        self.tools,
                        run_id=state.run_id,
                        node_id=node.id,
                        agent_id=node.agent,
                        mode=Mode.BAZAAR,
                        capabilities=caps,
                        artifacts_dir=artifacts_dir,
                        audit=audit,
                        metadata=node.metadata,
                    ),
                    upstream={key: value.to_view() for key, value in state.results.items()},
                    round_index=round_index + 1,
                )
                revised = agent.revise(context, current, round_reviews)
                node_tokens += revised.token_cost or 0
                state.total_tokens += revised.token_cost or 0
                audit.emit(
                    EventKind.REVISE,
                    node_id=node.id,
                    agent_id=node.agent,
                    mode=Mode.BAZAAR,
                    round=round_index + 1,
                    content=revised.content,
                    confidence=revised.confidence,
                )
                current = revised

        caps = self.policy.effective_capabilities(
            self.topology.agents[node.agent], node, Mode.BAZAAR
        )
        if self._actions_require_privilege(current, caps):
            # A Bazaar proposal that wants a side effect is frozen into the
            # Cathedral approval chain before anything is executed.
            arbitration = dataclasses.replace(
                current_decision,
                mode=Mode.CATHEDRAL,
                reasons=("bazaar_privileged_action",),
                forced=True,
                snapshot_required=True,
            )
            self._record_mode_switch(
                state=state,
                audit=audit,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                node_id=node.id,
                old_mode=state.last_mode or Mode.BAZAAR.value,
                decision=arbitration,
                reason="bazaar_privileged_action",
            )
            return self._run_cathedral(
                node=node,
                agent=agent,
                task=task,
                output=current,
                node_tokens=node_tokens,
                state=state,
                audit=audit,
                run_dir=run_dir,
                artifacts_dir=artifacts_dir,
                plan=plan,
                plan_index=plan_index,
                approval_provider=approval_provider,
                started_at=started_at,
                decision=arbitration,
                divergence=state.max_divergence,
                reviews=reviews,
            )

        actions, tools, action_error = self._execute_actions(
            node=node,
            output=current,
            caps=caps,
            state=state,
            audit=audit,
            artifacts_dir=artifacts_dir,
            mode=Mode.BAZAAR,
        )
        if action_error is not None:
            return self._fail_node(
                state=state,
                audit=audit,
                node=node,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                started_at=started_at,
                decision=current_decision,
                error=action_error,
                token_cost=node_tokens,
                divergence=state.max_divergence,
                reviews=reviews,
            )

        result = NodeResult(
            node_id=node.id,
            agent_id=node.agent,
            mode=Mode.BAZAAR.value,
            status="completed",
            output=current.content,
            confidence=current.confidence,
            token_cost=node_tokens,
            capabilities=sorted(caps),
            reviews=reviews,
            actions=actions,
            tools=tools,
            decision_reasons=list(current_decision.reasons),
            divergence=state.max_divergence,
            started_at=started_at,
            ended_at=_utc_now(),
        )
        return self._complete_node(state, audit, result)

    # ------------------------------------------------------------------
    # Cathedral channel
    # ------------------------------------------------------------------
    def _run_cathedral(
        self,
        *,
        node: NodeSpec,
        agent: Agent,
        task: str,
        output: AgentOutput,
        node_tokens: int,
        state: RunState,
        audit: AuditLog,
        run_dir: Path,
        artifacts_dir: Path,
        plan: tuple[str, ...],
        plan_index: int,
        approval_provider: ApprovalProvider,
        started_at: str,
        decision: GovernanceDecision,
        divergence: float,
        reviews: list[dict[str, Any]] | None = None,
    ) -> NodeResult:
        reviews = list(reviews or [])
        if state.last_mode != Mode.CATHEDRAL.value:
            self._record_mode_switch(
                state=state,
                audit=audit,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                node_id=node.id,
                old_mode=state.last_mode or Mode.BAZAAR.value,
                decision=decision,
                reason="enter_cathedral",
            )

        self._write_snapshot(
            state=state,
            audit=audit,
            run_dir=run_dir,
            plan=plan,
            plan_index=plan_index,
            node_id=node.id,
            reason="approval_gate",
            mode=Mode.CATHEDRAL.value,
        )
        audit.emit(
            EventKind.APPROVAL_REQUESTED,
            node_id=node.id,
            agent_id=node.agent,
            mode=Mode.CATHEDRAL,
            approvers=list(node.approval.approvers),
            scope=sorted(node.approval.scope),
            actions=[action.to_dict() for action in output.actions],
            reasons=list(decision.reasons),
        )

        approver_reviews: list[Review] = []
        blocked: Review | None = None
        if node.approval.required:
            for approver_id in node.approval.approvers:
                reviewer = self.agents.get(approver_id)
                reviewer_spec = self.topology.agents[approver_id]
                review = reviewer.review(
                    ReviewContext(
                        run_id=state.run_id,
                        topology_name=self.topology.name,
                        node=node,
                        agent=reviewer_spec,
                        mode=Mode.CATHEDRAL,
                        proposal=output,
                        proposal_agent_id=node.agent,
                        task=task,
                        upstream={key: value.to_view() for key, value in state.results.items()},
                    )
                )
                approver_reviews.append(review)
                node_tokens += review.token_cost
                state.total_tokens += review.token_cost
                self._audit_review(
                    audit,
                    node_id=node.id,
                    reviewer_id=approver_id,
                    review=review,
                    mode=Mode.CATHEDRAL,
                    round_index=0,
                    direction="approver_reviews_target",
                )
                if review.decision in (ReviewDecision.DENY, ReviewDecision.ESCALATE):
                    blocked = review
                    break
                if review.decision not in (ReviewDecision.APPROVE, ReviewDecision.ENDORSE):
                    blocked = Review(
                        ReviewDecision.DENY,
                        f"审批者 {approver_id} 返回了非审批决定 {review.decision.value}",
                    )
                    break

            if blocked is None:
                provider_review = approval_provider.decide(
                    ApprovalContext(
                        run_id=state.run_id,
                        topology_name=self.topology.name,
                        node=node,
                        proposal=output,
                        proposal_agent_id=node.agent,
                        task=task,
                        approver_reviews=tuple(approver_reviews),
                        metadata={"scope": sorted(node.approval.scope)},
                    )
                )
                node_tokens += provider_review.token_cost
                state.total_tokens += provider_review.token_cost
                approver_reviews.append(provider_review)
                self._audit_review(
                    audit,
                    node_id=node.id,
                    reviewer_id="policy",
                    review=provider_review,
                    mode=Mode.CATHEDRAL,
                    round_index=0,
                    direction="policy_gate",
                )
                if provider_review.decision in (
                    ReviewDecision.DENY,
                    ReviewDecision.ESCALATE,
                ):
                    blocked = provider_review
                elif provider_review.decision not in (
                    ReviewDecision.APPROVE,
                    ReviewDecision.ENDORSE,
                ):
                    blocked = Review(
                        ReviewDecision.DENY,
                        f"策略审批返回了非审批决定 {provider_review.decision.value}",
                    )
        else:
            audit.emit(
                EventKind.APPROVAL_GRANTED,
                node_id=node.id,
                agent_id=node.agent,
                mode=Mode.CATHEDRAL,
                skipped=True,
                reason="approval_not_required",
            )

        reviews.extend(review.to_dict() for review in approver_reviews)
        if blocked is not None:
            audit.emit(
                EventKind.NODE_BLOCKED,
                node_id=node.id,
                agent_id=node.agent,
                mode=Mode.CATHEDRAL,
                decision=blocked.decision.value,
                rationale=blocked.rationale,
            )
            self._write_snapshot(
                state=state,
                audit=audit,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                node_id=node.id,
                reason="approval_blocked",
                mode=Mode.CATHEDRAL.value,
            )
            state.status = "blocked"
            state.error = (
                f"approval {blocked.decision.value} for node {node.id}: "
                f"{blocked.rationale}"
            )
            result = NodeResult(
                node_id=node.id,
                agent_id=node.agent,
                mode=Mode.CATHEDRAL.value,
                status="blocked",
                output=output.content,
                confidence=output.confidence,
                token_cost=node_tokens,
                capabilities=[],
                reviews=reviews,
                actions=[action.to_dict() for action in output.actions],
                decision_reasons=list(decision.reasons),
                divergence=divergence,
                error=state.error,
                started_at=started_at,
                ended_at=_utc_now(),
            )
            state.results[node.id] = result
            return result

        audit.emit(
            EventKind.APPROVAL_GRANTED,
            node_id=node.id,
            agent_id=node.agent,
            mode=Mode.CATHEDRAL,
            scope=sorted(node.approval.scope),
        )
        caps = self.policy.effective_capabilities(
            self.topology.agents[node.agent], node, Mode.CATHEDRAL, approved=True
        )
        actions, tools, action_error = self._execute_actions(
            node=node,
            output=output,
            caps=caps,
            state=state,
            audit=audit,
            artifacts_dir=artifacts_dir,
            mode=Mode.CATHEDRAL,
        )
        if action_error is not None:
            return self._fail_node(
                state=state,
                audit=audit,
                node=node,
                run_dir=run_dir,
                plan=plan,
                plan_index=plan_index,
                started_at=started_at,
                decision=decision,
                error=action_error,
                token_cost=node_tokens,
                divergence=divergence,
                reviews=reviews,
            )

        result = NodeResult(
            node_id=node.id,
            agent_id=node.agent,
            mode=Mode.CATHEDRAL.value,
            status="completed",
            output=output.content,
            confidence=output.confidence,
            token_cost=node_tokens,
            capabilities=sorted(caps),
            reviews=reviews,
            actions=actions,
            tools=tools,
            decision_reasons=list(decision.reasons),
            divergence=divergence,
            started_at=started_at,
            ended_at=_utc_now(),
        )
        return self._complete_node(state, audit, result)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _actions_require_privilege(
        self, output: AgentOutput, caps: frozenset[str]
    ) -> bool:
        for action in output.actions:
            try:
                tool = self.tools.get(action.tool)
            except Exception:
                continue
            if not capability_grants(caps, tool.required_capability):
                return True
        return False

    def _execute_actions(
        self,
        *,
        node: NodeSpec,
        output: AgentOutput,
        caps: frozenset[str],
        state: RunState,
        audit: AuditLog,
        artifacts_dir: Path,
        mode: Mode,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        records: list[dict[str, Any]] = []
        tool_records: list[dict[str, Any]] = []
        invoker = ToolInvoker(
            self.tools,
            run_id=state.run_id,
            node_id=node.id,
            agent_id=node.agent,
            mode=mode,
            capabilities=caps,
            artifacts_dir=artifacts_dir,
            audit=audit,
            metadata=node.metadata,
        )
        for action in output.actions:
            record = action.to_dict()
            try:
                tool = self.tools.get(action.tool)
            except Exception as exc:
                return records, tool_records, f"{type(exc).__name__}: {exc}"
            if not capability_grants(caps, tool.required_capability):
                if mode is Mode.BAZAAR:
                    record["status"] = "deferred"
                    record["reason"] = (
                        f"Bazaar 不允许执行 {tool.required_capability!r} 能力，"
                        "等待 Cathedral 门禁"
                    )
                    records.append(record)
                    audit.emit(
                        EventKind.ACTION_DEFERRED,
                        node_id=node.id,
                        agent_id=node.agent,
                        mode=mode,
                        tool=action.tool,
                        required=tool.required_capability,
                        reason="bazaar_execution_boundary",
                    )
                    continue
                audit.emit(
                    EventKind.TOOL_DENIED,
                    node_id=node.id,
                    agent_id=node.agent,
                    mode=mode,
                    tool=action.tool,
                    required=tool.required_capability,
                    reason="approved_scope_missing",
                    capabilities=sorted(caps),
                )
                return (
                    records,
                    tool_records,
                    f"tool {action.tool!r} requires {tool.required_capability!r}, "
                    f"outside approved scope {sorted(caps)}",
                )
            try:
                result = invoker.invoke(action.tool, **action.args)
            except Exception as exc:
                return records, tool_records, f"{type(exc).__name__}: {exc}"
            record["status"] = "executed"
            record["result"] = result
            records.append(record)
            tool_records.append({"tool": action.tool, "result": result})
            if isinstance(result, Mapping) and result.get("written"):
                audit.emit(
                    EventKind.ARTIFACT_CREATED,
                    node_id=node.id,
                    agent_id=node.agent,
                    mode=mode,
                    path=result.get("path"),
                )
        return records, tool_records, None

    def _complete_node(
        self, state: RunState, audit: AuditLog, result: NodeResult
    ) -> NodeResult:
        state.results[result.node_id] = result
        if result.node_id not in state.completed:
            state.completed.append(result.node_id)
        audit.emit(
            EventKind.NODE_COMPLETED,
            node_id=result.node_id,
            agent_id=result.agent_id,
            mode=result.mode,
            status=result.status,
            confidence=result.confidence,
            token_cost=result.token_cost,
            divergence=result.divergence,
            capabilities=result.capabilities,
        )
        return result

    def _fail_node(
        self,
        *,
        state: RunState,
        audit: AuditLog,
        node: NodeSpec,
        run_dir: Path,
        plan: tuple[str, ...],
        plan_index: int,
        started_at: str,
        decision: GovernanceDecision,
        error: str,
        traceback_text: str | None = None,
        token_cost: int = 0,
        divergence: float = 0.0,
        reviews: list[dict[str, Any]] | None = None,
    ) -> NodeResult:
        state.status = "failed"
        state.error = f"node {node.id}: {error}"
        audit.emit(
            EventKind.NODE_FAILED,
            node_id=node.id,
            agent_id=node.agent,
            mode=decision.mode,
            error=error,
            traceback=traceback_text,
        )
        self._write_snapshot(
            state=state,
            audit=audit,
            run_dir=run_dir,
            plan=plan,
            plan_index=plan_index,
            node_id=node.id,
            reason="node_failed",
            mode=decision.mode.value,
        )
        result = NodeResult(
            node_id=node.id,
            agent_id=node.agent,
            mode=decision.mode.value,
            status="failed",
            output="",
            confidence=0.0,
            token_cost=token_cost,
            capabilities=[],
            reviews=list(reviews or []),
            decision_reasons=list(decision.reasons),
            divergence=divergence,
            error=error,
            started_at=started_at,
            ended_at=_utc_now(),
        )
        state.results[node.id] = result
        return result

    def _audit_review(
        self,
        audit: AuditLog,
        *,
        node_id: str,
        reviewer_id: str,
        review: Review,
        mode: Mode,
        round_index: int,
        direction: str,
        source_node: str | None = None,
    ) -> None:
        event_kind = {
            ReviewDecision.ENDORSE: EventKind.ENDORSE,
            ReviewDecision.CHALLENGE: EventKind.CHALLENGE,
            ReviewDecision.APPROVE: EventKind.APPROVE,
            ReviewDecision.DENY: EventKind.DENY,
            ReviewDecision.ESCALATE: EventKind.ESCALATE,
        }[review.decision]
        audit.emit(
            event_kind,
            node_id=node_id,
            agent_id=reviewer_id,
            mode=mode,
            decision=review.decision.value,
            rationale=review.rationale,
            confidence=review.confidence,
            round=round_index,
            direction=direction,
            source_node=source_node,
        )

    def _record_mode_switch(
        self,
        *,
        state: RunState,
        audit: AuditLog,
        run_dir: Path,
        plan: tuple[str, ...],
        plan_index: int,
        node_id: str,
        old_mode: str,
        decision: GovernanceDecision,
        reason: str,
    ) -> None:
        self._write_snapshot(
            state=state,
            audit=audit,
            run_dir=run_dir,
            plan=plan,
            plan_index=plan_index,
            node_id=node_id,
            reason=f"mode_switch_{reason}",
            mode=decision.mode.value,
        )
        audit.emit(
            EventKind.MODE_SWITCH,
            node_id=node_id,
            mode=decision.mode,
            from_mode=old_mode,
            to_mode=decision.mode.value,
            reasons=list(decision.reasons),
            trigger=reason,
        )
        state.last_mode = decision.mode.value

    def _write_snapshot(
        self,
        *,
        state: RunState,
        audit: AuditLog,
        run_dir: Path,
        plan: tuple[str, ...],
        plan_index: int,
        node_id: str,
        reason: str,
        mode: str,
    ) -> Path:
        snapshot_id = f"snap-{state.run_id}-{audit.seq + 1:04d}"
        snapshot = create_snapshot(
            snapshot_id=snapshot_id,
            run_id=state.run_id,
            event_seq=audit.seq,
            node_id=node_id,
            reason=reason,
            mode=mode,
            branch_id=state.branch_id,
            parent_run_id=state.parent_run_id,
            task=state.task,
            topology=self.topology.to_dict(),
            state=state.to_dict(),
            next_nodes=list(plan[plan_index:]),
        )
        path = snapshot.save(run_dir / "snapshots", audit.seq + 1)
        self._snapshot_paths.append(str(path))
        audit.emit(
            EventKind.SNAPSHOT_CREATED,
            node_id=node_id,
            mode=mode,
            snapshot_id=snapshot_id,
            reason=reason,
            path=str(path),
            event_seq=snapshot.event_seq,
        )
        return path

    def _build_result(
        self,
        state: RunState,
        run_dir: Path,
        audit_path: Path,
        plan: tuple[str, ...],
    ) -> RunResult:
        final_output = ""
        for node_id in reversed(plan):
            if node_id in state.results and state.results[node_id].status == "completed":
                final_output = state.results[node_id].output
                break
        return RunResult(
            run_id=state.run_id,
            status=state.status,
            task=state.task,
            results=dict(state.results),
            audit_path=str(audit_path),
            run_dir=str(run_dir),
            snapshot_paths=list(self._snapshot_paths),
            final_output=final_output,
            total_tokens=state.total_tokens,
            max_divergence=state.max_divergence,
            branch_id=state.branch_id,
            parent_run_id=state.parent_run_id,
            replay_from=state.replay_from,
            error=state.error,
        )

    def _write_summary(self, run_dir: Path, result: RunResult) -> None:
        summary_path = run_dir / "summary.json"
        summary_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _allocate_run_id(self, requested: str | None) -> str:
        if requested:
            base = safe_component(requested)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            base = f"narthex-{stamp}-{secrets.token_hex(3)}"
        candidate = base
        counter = 1
        while (self.run_root / candidate).exists():
            counter += 1
            candidate = f"{base}-{counter}"
        return candidate
