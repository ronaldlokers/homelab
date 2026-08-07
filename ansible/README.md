# Node configuration

Host state for the three CM5 control plane nodes: packages, inotify limits,
persistent journald, and the etcd bind mount from NVMe (#159).

## Running it

```
cd ansible
ansible-playbook site.yml --check --diff   # what would change
ansible-playbook site.yml                  # change it
```

Idempotent, one node at a time.

## Not covered

**Installing k3s** — the flags differ per node and a rebuild follows
`docs/setup.md` first.

**Moving etcd data** — bind-mounting over a live etcd database hides it from
k3s. The play stops in that case and points at
`docs/runbooks/etcd-migrate-to-nvme.md`. It creates the mount on a fresh node.

## Note

`/etc/sysctl.conf` is not read on these nodes: Debian's symlink into
`/etc/sysctl.d/99-sysctl.conf` is missing, so anything placed there never
applied. This role writes to `/etc/sysctl.d/` instead.
