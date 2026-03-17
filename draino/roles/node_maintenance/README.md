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
- kubernetes.core collection
- openstack.cloud collection
- kubectl available on the control host
- openstack CLI available on the control host for service operations
- openstacksdk available, or allow the role to install it when dry_run is false
- Valid Kubernetes and OpenStack credentials on the control host

## Defaults

dry_run is true by default.

## Compatibility note

This version uses kubectl for cordon, drain, and uncordon because kubernetes.core.k8s_drain parameters vary significantly across collection versions.
