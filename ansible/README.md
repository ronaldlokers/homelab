# Node configuration

Host state for the three CM5 control plane nodes. Everything here previously
existed only as copy-paste bash inside runbooks, which meant a rebuilt node
came back subtly different — most consequentially without the etcd bind mount
onto NVMe, silently back on the eMMC card whose 1200–2300ms apply latency took
a day to diagnose (#159).

## Running it

```
cd ansible
ansible-playbook site.yml --check --diff   # what would change
ansible-playbook site.yml                  # change it
```

Always the first command before the second. Every task is idempotent, and the
play runs one node at a time.

## What it covers

- `open-iscsi` and `nfs-common`, which Longhorn and the Immich library need
- inotify limits, as a drop-in in `/etc/sysctl.d/`
- persistent journald, as a drop-in plus `/var/log/journal`
- the etcd bind mount from NVMe, and its `fstab` entry

## What it deliberately does not do

**Install k3s.** The install flags differ per node (`--cluster-init` on the
first only) and installing k3s onto a node that already runs it is a different
operation from configuring one. A rebuild follows `docs/setup.md` for that
step, then runs this.

**Move etcd data.** Bind-mounting over a directory that already holds a live
etcd database hides it from k3s. The playbook detects that case and stops,
pointing at `docs/runbooks/etcd-migrate-to-nvme.md`, which stops k3s and copies
the data first. It will happily create the mount on a fresh node where the
target is empty.

## A fault this found

`/etc/sysctl.conf` is never read on these nodes: Debian normally symlinks it
into `/etc/sysctl.d/99-sysctl.conf`, and that symlink does not exist here. The
inotify limits written there had therefore never applied on any node —

```
configured in /etc/sysctl.conf: 524288 / 512
runtime:                        129511 / 128
```

— including on a node that had rebooted five days earlier. The drop-in this
role writes lives in `/etc/sysctl.d/`, which systemd-sysctl reads directly.
