#!/usr/bin/env python3
import argparse
import sys
import time

try:
    from openstack import connection
    from openstack import exceptions
except Exception as e:
    print("openstacksdk is required to run this script. Error:", e, file=sys.stderr)
    sys.exit(2)

DEFAULT_POLL_DELAY = 10
DEFAULT_POLL_TIMEOUT = 3600

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--cloud", required=False, help="Name of cloud in clouds.yaml (optional)")
    p.add_argument("--server", required=True, help="Server ID to migrate")
    p.add_argument("--target", required=False, help="Target compute host name (optional)")
    p.add_argument("--wait", action="store_true", help="Wait for migration to finish")
    p.add_argument("--dry-run", action="store_true", help="Do not perform migration; print actions")
    p.add_argument("--timeout", type=int, default=DEFAULT_POLL_TIMEOUT, help="Wait timeout in seconds")
    p.add_argument("--delay", type=int, default=DEFAULT_POLL_DELAY, help="Polling delay in seconds")
    return p.parse_args()

def connect(cloud=None):
    if cloud:
        return connection.from_config(cloud=cloud)
    return connection.from_config()

def trigger_live_migrate(conn, server_id, target=None, dry_run=False):
    if dry_run:
        print(f"[DRY-RUN] Would trigger live migration for server {server_id} target={target}")
        return None
    server = conn.compute.get_server(server_id)
    if not server:
        raise exceptions.ResourceNotFound(f"Server not found: {server_id}")
    print(f"Triggering live migration for server {server_id} (target={target})")
    if target:
        conn.compute.live_migrate_server(server, host=target)
    else:
        conn.compute.live_migrate_server(server)
    return True

def wait_for_migration(conn, server_id, timeout=DEFAULT_POLL_TIMEOUT, delay=DEFAULT_POLL_DELAY):
    start = time.time()
    print("Waiting for migration completion indicators...")
    while True:
        server = conn.compute.get_server(server_id)
        status = getattr(server, "status", None)
        if status and status.lower() in ["active", "shutoff", "error"]:
            print(f"Observed server status: {status}")
            return True
        if (time.time() - start) > timeout:
            raise TimeoutError(f"Timeout waiting for migration for server {server_id}")
        time.sleep(delay)

def main():
    args = parse_args()
    try:
        conn = connect(cloud=args.cloud)
    except Exception as exc:
        print("Failed to connect to cloud:", exc, file=sys.stderr)
        sys.exit(2)

    try:
        trigger_live_migrate(conn, args.server, args.target, dry_run=args.dry_run)
        if args.wait and not args.dry_run:
            wait_for_migration(conn, args.server, timeout=args.timeout, delay=args.delay)
    except Exception as exc:
        print("Migration failed:", exc, file=sys.stderr)
        sys.exit(3)

    print("Done")
    sys.exit(0)

if __name__ == "__main__":
    main()
