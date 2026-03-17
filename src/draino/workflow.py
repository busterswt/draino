from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import StatusEvent, StepState, TargetNode
from .ops import ClusterOperations, DrainoError


@dataclass
class WorkflowStep:
    name: str
    description: str


class MaintenanceWorkflow:
    def __init__(self, ops: ClusterOperations, emit: Callable[[StatusEvent], None]):
        self.ops = ops
        self.emit = emit

    def run(self, target: TargetNode) -> None:
        self._run_step("cordon", f"Cordon {target.k8s_node}", lambda: self.ops.cordon(target))
        self._run_step(
            "disable_compute",
            f"Disable nova-compute on {target.nova_compute_host}",
            lambda: self.ops.disable_compute_service(target),
        )
        servers, migratable, amphora = self._classify(target)
        self.emit(
            StatusEvent(
                step="classify",
                state=StepState.SUCCESS,
                message=f"Found {len(servers)} servers: {len(migratable)} migratable, {len(amphora)} amphora",
                details={
                    "all_servers": [server.id for server in servers],
                    "migratable": [server.id for server in migratable],
                    "amphora": [server.id for server in amphora],
                },
            )
        )
        for server in migratable:
            self._run_step(
                f"migrate:{server.id}",
                f"Live migrate {server.name} ({server.id})",
                lambda server_id=server.id: self.ops.migrate_server(server_id),
            )
        failover_lb_ids: list[str] = []
        for amphora_server in amphora:
            amphorae = [
                item for item in self.ops.list_amphorae() if item.compute_id == amphora_server.id and item.loadbalancer_id
            ]
            for item in amphorae:
                if item.loadbalancer_id in failover_lb_ids:
                    continue
                failover_lb_ids.append(item.loadbalancer_id)
                self._run_step(
                    f"failover:{item.loadbalancer_id}",
                    f"Fail over load balancer {item.loadbalancer_id} for amphora {amphora_server.id}",
                    lambda lb_id=item.loadbalancer_id: self.ops.failover_loadbalancer(lb_id),
                )
        self.emit(StatusEvent(step="wait_for_empty", state=StepState.RUNNING, message="Waiting for host to empty"))
        remaining_migratable, remaining_amphora = self.ops.wait_for_host_empty(target, self._emit_poll)
        if remaining_migratable or remaining_amphora:
            raise DrainoError(
                "Host did not empty before timeout. "
                f"Remaining migratable={ [server.id for server in remaining_migratable] }, "
                f"remaining amphora={ [server.id for server in remaining_amphora] }"
            )
        self.emit(StatusEvent(step="wait_for_empty", state=StepState.SUCCESS, message="Nova host is empty"))
        self._run_step("drain", f"Drain Kubernetes node {target.k8s_node}", lambda: self.ops.drain(target))

    def _classify(self, target: TargetNode):
        self.emit(StatusEvent(step="classify", state=StepState.RUNNING, message="Collecting Nova and Octavia state"))
        return self.ops.list_servers_for_host(target)

    def _run_step(self, step: str, description: str, fn) -> None:
        self.emit(StatusEvent(step=step, state=StepState.RUNNING, message=description))
        result = fn()
        self.emit(
            StatusEvent(
                step=step,
                state=StepState.SUCCESS,
                message=description,
                details={"stdout": getattr(result, "stdout", ""), "stderr": getattr(result, "stderr", "")},
            )
        )

    def _emit_poll(self, step: str, details: dict[str, list[str]]) -> None:
        self.emit(
            StatusEvent(
                step="wait_for_empty",
                state=StepState.RUNNING,
                message="Polling remaining instances",
                details=details,
            )
        )
