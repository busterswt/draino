# node_maintenance role

Purpose: automate safe maintenance operations for compute nodes:

- Drain and cordon a Kubernetes node
- Disable Nova compute scheduling
- Live-migrate Nova instances off the host via openstacksdk helper
- Optionally delete the Nova compute service record when empty

It also supports restore:

- Re-enable Nova compute scheduling
- Uncordon the Kubernetes node
- Optionally remove taints

## Requirements

- Ansible 2.12+
- `kubernetes.core` collection
- `openstack.cloud` collection
- `kubectl` available on the control host
- `openstack` CLI available on the control host for service operations
- `openstacksdk` available, or allow the role to install it when `dry_run: false`
- Valid Kubernetes and OpenStack credentials on the control host

## Defaults

`dry_run` is **true by default**. The role will print intended actions unless you explicitly set `dry_run: false`.

## Example playbook

```yaml
- hosts: localhost
  gather_facts: false
  roles:
    - role: node_maintenance
      vars:
        action: drain
        dry_run: false
        openstack_cloud: mycloud
        target_hosts:
          - k8s_node: compute-01
            nova_compute_host: compute-01
```

## Restore example

```yaml
- hosts: localhost
  gather_facts: false
  roles:
    - role: node_maintenance
      vars:
        action: restore
        dry_run: false
        target_hosts:
          - k8s_node: compute-01
            nova_compute_host: compute-01
        taints_to_remove:
          - "maintenance=true:NoSchedule"
```

## Notes

- The role uses `kubernetes.core.k8s_drain` for drain and uncordon.
- The role uses `openstack.cloud.server_info` and `openstack.cloud.compute_service_info` for discovery.
- Live migration is handled by `files/migrate_server.py` using openstacksdk.
- Some actions still use CLI commands, guarded by `dry_run`.
