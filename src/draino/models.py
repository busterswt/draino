from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class CommandResult(BaseModel):
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class NovaServer(BaseModel):
    id: str
    name: str
    host: str | None = Field(default=None, alias="OS-EXT-SRV-ATTR:host")
    status: str | None = None
    project_id: str | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class Amphora(BaseModel):
    id: str
    loadbalancer_id: str | None = None
    compute_id: str | None = None
    role: str | None = None
    status: str | None = None

    model_config = {"extra": "allow"}


class TargetNode(BaseModel):
    k8s_node: str
    nova_compute_host: str
    display_name: str
    notes: str | None = None


class MaintenanceConfig(BaseModel):
    openstack_cloud: str | None = None
    kubectl_drain_extra_args: list[str] = Field(
        default_factory=lambda: [
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--force",
        ]
    )
    kubectl_drain_timeout: str = "30m"
    nova_disable_reason: str = "maintenance: drained by draino"
    poll_interval_seconds: int = 10
    wait_timeout_seconds: int = 3600
    amphora_name_pattern: str = r"^amphora-.*"
    targets: list[TargetNode] = Field(default_factory=list)


class StatusEvent(BaseModel):
    step: str
    state: StepState
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
