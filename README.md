# draino

`draino` is a Python TUI for maintenance workflows that need to coordinate Kubernetes node draining with OpenStack Nova and Octavia instance movement.

## Workflow

For a selected target, the app runs this sequence:

1. Cordon the Kubernetes node.
2. Disable `nova-compute` scheduling on the mapped compute host.
3. List Nova instances on that host and classify them into migratable and amphora-backed.
4. Live migrate non-amphora instances.
5. Fail over load balancers associated with amphora instances still on the host.
6. Wait until the compute host is empty.
7. Drain the Kubernetes node.

## Requirements

- Python 3.11+
- `kubectl` configured for the target cluster
- `openstack` CLI configured with the required Nova and Octavia permissions
- The OpenStack client must include load balancer commands

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Run

```bash
draino
draino --config draino.yaml
```

Press `Enter` to start the workflow for the selected target. Use the arrow keys to change the selected target.

## Optional config

```yaml
openstack_cloud: mycloud
kubectl_drain_timeout: 30m
poll_interval_seconds: 10
wait_timeout_seconds: 3600
targets:
  - k8s_node: node01.example.net
    nova_compute_host: node01.tenant.example.net
    display_name: node01
```

If `targets` is not provided, `draino` discovers Kubernetes nodes and Nova compute hosts and auto-matches them by short hostname.
