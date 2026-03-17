from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .models import Amphora, CommandResult, MaintenanceConfig, NovaServer, TargetNode


class DrainoError(RuntimeError):
    """Raised for operational failures."""


def _normalize_name(value: str) -> str:
    return value.split(".", 1)[0].lower()


@dataclass
class CommandRunner:
    cloud: str | None = None

    def run(self, args: list[str], check: bool = True) -> CommandResult:
        process = subprocess.run(args, capture_output=True, text=True)
        result = CommandResult(
            args=args,
            returncode=process.returncode,
            stdout=process.stdout.strip(),
            stderr=process.stderr.strip(),
        )
        if check and result.returncode != 0:
            joined = shlex.join(args)
            raise DrainoError(
                f"Command failed: {joined}\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return result

    def openstack(self, args: list[str], check: bool = True) -> CommandResult:
        base = ["openstack"]
        if self.cloud:
            base += ["--os-cloud", self.cloud]
        return self.run(base + args, check=check)

    def kubectl(self, args: list[str], check: bool = True) -> CommandResult:
        return self.run(["kubectl"] + args, check=check)


class ClusterOperations:
    def __init__(self, config: MaintenanceConfig):
        self.config = config
        self.runner = CommandRunner(cloud=config.openstack_cloud)

    def discover_targets(self) -> list[TargetNode]:
        configured = self.config.targets
        if configured:
            return configured

        nodes = self.list_k8s_nodes()
        compute_hosts = self.list_nova_compute_hosts()
        compute_by_short = {_normalize_name(host): host for host in compute_hosts}
        targets: list[TargetNode] = []
        for node in nodes:
            short = _normalize_name(node)
            nova_host = compute_by_short.get(short, node)
            notes = None if nova_host == node else f"Auto-matched Nova host {nova_host}"
            targets.append(
                TargetNode(
                    k8s_node=node,
                    nova_compute_host=nova_host,
                    display_name=f"{node} -> {nova_host}",
                    notes=notes,
                )
            )
        return targets

    def list_k8s_nodes(self) -> list[str]:
        result = self.runner.kubectl(["get", "nodes", "-o", "json"])
        payload = json.loads(result.stdout or "{}")
        return sorted(item["metadata"]["name"] for item in payload.get("items", []))

    def list_nova_compute_hosts(self) -> list[str]:
        result = self.runner.openstack(
            ["compute", "service", "list", "--service", "nova-compute", "-f", "json"]
        )
        payload = json.loads(result.stdout or "[]")
        return sorted({item["Host"] for item in payload if item.get("Host")})

    def cordon(self, target: TargetNode) -> CommandResult:
        return self.runner.kubectl(["cordon", target.k8s_node])

    def drain(self, target: TargetNode) -> CommandResult:
        args = ["drain", target.k8s_node] + self.config.kubectl_drain_extra_args
        args.append(f"--timeout={self.config.kubectl_drain_timeout}")
        return self.runner.kubectl(args)

    def disable_compute_service(self, target: TargetNode) -> CommandResult:
        return self.runner.openstack(
            [
                "compute",
                "service",
                "set",
                "--disable",
                "--disable-reason",
                self.config.nova_disable_reason,
                target.nova_compute_host,
                "nova-compute",
            ]
        )

    def list_servers(self) -> list[NovaServer]:
        result = self.runner.openstack(["server", "list", "--all-projects", "--long", "-f", "json"])
        payload = json.loads(result.stdout or "[]")
        servers: list[NovaServer] = []
        for item in payload:
            server = NovaServer.model_validate(item)
            if not server.host and item.get("Host"):
                server.host = item.get("Host")
            servers.append(server)
        return servers

    def list_servers_for_candidate_host(self, host: str) -> list[NovaServer]:
        result = self.runner.openstack(
            ["server", "list", "--all-projects", "--host", host, "--long", "-f", "json"],
            check=False,
        )
        if result.returncode != 0:
            return []
        payload = json.loads(result.stdout or "[]")
        servers: list[NovaServer] = []
        for item in payload:
            server = NovaServer.model_validate(item)
            if not server.host:
                server.host = host
            servers.append(server)
        return servers

    def list_amphorae(self) -> list[Amphora]:
        result = self.runner.openstack(["loadbalancer", "amphora", "list", "-f", "json"], check=False)
        if result.returncode != 0:
            return []
        payload = json.loads(result.stdout or "[]")
        amphorae: list[Amphora] = []
        for item in payload:
            amphorae.append(
                Amphora(
                    id=item.get("ID", ""),
                    loadbalancer_id=item.get("Load Balancer ID"),
                    compute_id=item.get("Compute ID"),
                    role=item.get("Role"),
                    status=item.get("Status"),
                )
            )
        return amphorae

    def list_servers_for_host(self, target: TargetNode) -> tuple[list[NovaServer], list[NovaServer], list[NovaServer]]:
        servers_by_id: dict[str, NovaServer] = {}
        for host in self._host_candidates(target):
            for server in self.list_servers_for_candidate_host(host):
                servers_by_id[server.id] = server
        if not servers_by_id:
            for server in self.list_servers():
                if self._server_matches_target(server, target):
                    servers_by_id[server.id] = server
        servers = list(servers_by_id.values())
        amphora_ids = {amphora.compute_id for amphora in self.list_amphorae() if amphora.compute_id}
        pattern = re.compile(self.config.amphora_name_pattern)
        amphora_servers = [
            server for server in servers if server.id in amphora_ids or pattern.match(server.name)
        ]
        amphora_server_ids = {server.id for server in amphora_servers}
        migratable = [server for server in servers if server.id not in amphora_server_ids]
        return servers, migratable, amphora_servers

    def migrate_server(self, server_id: str) -> CommandResult:
        return self.runner.openstack(["server", "migrate", "--live-migration", server_id])

    def failover_loadbalancer(self, loadbalancer_id: str) -> CommandResult:
        return self.runner.openstack(["loadbalancer", "failover", loadbalancer_id])

    def wait_for_host_empty(
        self,
        target: TargetNode,
        event_cb: Callable[[str, dict[str, list[str]]], None] | None = None,
    ) -> tuple[list[NovaServer], list[NovaServer]]:
        deadline = time.time() + self.config.wait_timeout_seconds
        while time.time() < deadline:
            _, migratable, amphora = self.list_servers_for_host(target)
            if event_cb:
                event_cb(
                    "poll",
                    {
                        "migratable": [server.id for server in migratable],
                        "amphora": [server.id for server in amphora],
                    },
                )
            if not migratable and not amphora:
                return migratable, amphora
            time.sleep(self.config.poll_interval_seconds)
        return self.list_servers_for_host(target)[1:]

    def _server_matches_target(self, server: NovaServer, target: TargetNode) -> bool:
        candidates = self._host_candidates(target)
        host = (server.host or "").lower()
        return host in candidates or _normalize_name(host) in candidates

    def _host_candidates(self, target: TargetNode) -> set[str]:
        return {
            target.nova_compute_host.lower(),
            _normalize_name(target.nova_compute_host),
            target.k8s_node.lower(),
            _normalize_name(target.k8s_node),
        }
