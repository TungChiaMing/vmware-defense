#!/usr/bin/env python3
"""Post-modernization patches for VMware dashboards.

Two fixes:
1. vmware-stats-prom.json — High level stats panels are too narrow (w=2) so
   titles get truncated. Widen them to match overview-prom layout (w=4/5) and
   shift everything below the moved "VMs per datastore" table down.
2. vmware-overview-prom.json — Four stats show N/A because their queries return
   an empty set (vcsim doesn't populate boot times, snapshots, or have powered-
   off VMs). Append `or vector(0)` so they fall back to 0.

Re-running modernize_dashboard.py will overwrite the generated dashboards;
re-run this script afterwards to re-apply the patches.
"""
import json
import os

DASHBOARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "grafana", "dashboards")

# ---- Fix 1: vmware-stats-prom.json gridPos layout ----
# Stats panels widened to overview-prom's [4,5,5,5,5] across full 24 cols.
# VMs-per-datastore table moves below the stats; everything originally at
# y >= 7 (Host stats row + below) shifts down by 6.
STATS_NEW_POS = {
    # Row 1 (y=1)
    8:  {"h": 3, "w": 4, "x": 0,  "y": 1},   # Powered on VMs
    14: {"h": 3, "w": 5, "x": 4,  "y": 1},   # Average VM uptime
    12: {"h": 3, "w": 5, "x": 9,  "y": 1},   # Average vCPUs per VM
    32: {"h": 3, "w": 5, "x": 14, "y": 1},   # Total snapshots
    26: {"h": 3, "w": 5, "x": 19, "y": 1},   # Average host uptime
    # Row 2 (y=4)
    10: {"h": 3, "w": 4, "x": 0,  "y": 4},   # Powered off VMs
    22: {"h": 3, "w": 5, "x": 4,  "y": 4},   # Total VMs
    16: {"h": 3, "w": 5, "x": 9,  "y": 4},   # Average memory per VM
    34: {"h": 3, "w": 5, "x": 14, "y": 4},   # Average snapshot age
    30: {"h": 3, "w": 5, "x": 19, "y": 4},   # Average host core count
    # Moved table (full width below stats)
    20: {"h": 6, "w": 24, "x": 0, "y": 7},   # VMs per datastore
}
STATS_SHIFT_Y = 6  # everything originally at y >= 7 (except the moved table) shifts by +6


def patch_stats_layout(d):
    def walk(panels):
        for p in panels:
            pid = p.get("id")
            if pid in STATS_NEW_POS:
                p["gridPos"] = STATS_NEW_POS[pid]
            else:
                gp = p.get("gridPos")
                if gp and gp.get("y", 0) >= 7:
                    gp["y"] += STATS_SHIFT_Y
            if "panels" in p:
                walk(p["panels"])
    walk(d.get("panels", []))


# ---- Fix 2: vmware-overview-prom.json N/A queries ----
# Panel id → new expr (adds `or vector(0)` so vcsim's empty result shows 0)
OVERVIEW_EXPR_PATCHES = {
    10: "count(vmware_vm_power_state == 0) or vector(0)",                                       # Powered off VMs
    14: "avg(avg without (instance)(vmware_vm_boot_timestamp_seconds)) or vector(0)",           # Average VM uptime
    32: "sum(vmware_vm_snapshots) or vector(0)",                                                # Total snapshots
    34: "avg(vmware_vm_snapshot_timestamp_seconds) or vector(0)",                               # Average snapshot age
}


def patch_overview_queries(d):
    def walk(panels):
        for p in panels:
            pid = p.get("id")
            if pid in OVERVIEW_EXPR_PATCHES:
                for t in p.get("targets", []):
                    t["expr"] = OVERVIEW_EXPR_PATCHES[pid]
            if "panels" in p:
                walk(p["panels"])
    walk(d.get("panels", []))


def apply(filename, patch_fn):
    path = os.path.join(DASHBOARDS_DIR, filename)
    with open(path) as f:
        d = json.load(f)
    patch_fn(d)
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
    print(f"Patched: {filename}")


if __name__ == "__main__":
    apply("vmware-stats-prom.json", patch_stats_layout)
    apply("vmware-overview-prom.json", patch_overview_queries)
