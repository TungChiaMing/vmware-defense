#!/usr/bin/env python3
"""Convert Telegraf/InfluxDB VMware dashboard to vmware_exporter/Prometheus format.

Source template: Grafana dashboard 8159 (Telegraf vsphere plugin via InfluxDB).
Target: pryorda/vmware_exporter metrics scraped by Prometheus.
"""
import json
import os
import re
import sys

DS_UID = "PBFA97CFB590B2093"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(SCRIPT_DIR, "vmware-template-influxdb.json")
DEFAULT_DST = os.path.join(SCRIPT_DIR, "grafana", "dashboards", "vmware-vsphere-prom.json")
DROP_TAGS = {"vSphere Telegraf"}

# Map (Telegraf measurement, field) → PromQL expression template.
# {f} is replaced by a comma-separated label filter list (e.g. host_name=~"$esxi").
METRIC_MAP = {
    ('vsphere_host_cpu', 'usage_average'):
        '(vmware_host_cpu_usage{{{f}}} / vmware_host_cpu_max{{{f}}}) * 100',
    ('vsphere_host_mem', 'usage_average'):
        '(vmware_host_memory_usage{{{f}}} / vmware_host_memory_max{{{f}}}) * 100',
    ('vsphere_host_mem', 'totalCapacity_average'):
        'vmware_host_memory_max{{{f}}}',
    ('vsphere_host_sys', 'uptime_latest'):
        '(time() - vmware_host_boot_timestamp_seconds{{{f}}}) * 1000',
    ('vsphere_host_net', 'bytesRx_average'):
        'vmware_host_net_bytesRx_average{{{f}}}',
    ('vsphere_host_net', 'bytesTx_average'):
        'vmware_host_net_bytesTx_average{{{f}}}',
    ('vsphere_host_net', 'usage_average'):
        'vmware_host_net_usage_average{{{f}}}',
    ('vsphere_datastore_disk', 'capacity_latest'):
        'vmware_datastore_capacity_size{{{f}}}',
    ('vsphere_datastore_disk', 'used_latest'):
        '(vmware_datastore_capacity_size{{{f}}} - vmware_datastore_freespace_size{{{f}}})',
    ('vsphere_vm_cpu', 'usage_average'):
        'vmware_vm_cpu_usage_average{{{f}}}',
    ('vsphere_vm_cpu', 'ready_summation'):
        'vmware_vm_cpu_ready_summation{{{f}}}',
    ('vsphere_vm_mem', 'usage_average'):
        'vmware_vm_mem_usage_average{{{f}}}',
    ('vsphere_vm_mem', 'entitlement_average'):
        'vmware_vm_memory_max{{{f}}}',
    ('vsphere_vm_net', 'usage_average'):
        'vmware_vm_net_usage_average{{{f}}}',
    ('vsphere_vm_virtualDisk', 'numberReadAveraged_average'):
        'vmware_vm_disk_read_average{{{f}}}',
    ('vsphere_vm_virtualDisk', 'numberWriteAveraged_average'):
        'vmware_vm_disk_write_average{{{f}}}',
    ('vsphere_vm_virtualDisk', 'totalReadLatency_average'):
        'vmware_vm_disk_maxTotalLatency_latest{{{f}}}',
    ('vsphere_vm_virtualDisk', 'totalWriteLatency_average'):
        'vmware_vm_disk_maxTotalLatency_latest{{{f}}}',
}

# Telegraf tag → vmware_exporter label
LABEL_MAP = {
    'vcenter': None,
    'clustername': 'cluster_name',
    'esxhostname': 'host_name',
    'vmname': 'vm_name',
    'dsname': 'ds_name',
    'source': 'ds_name',
}

# Telegraf legend $tag_X → PromQL {{label}}
LEGEND_TAG_MAP = {
    '$tag_clustername': '{{cluster_name}}',
    '$tag_esxhostname': '{{host_name}}',
    '$tag_vmname': '{{vm_name}}',
    '$tag_source': '{{ds_name}}',
    '$tag_vcenter': 'vcsim',
}

VAR_QUERY_MAP = {
    'clustername': 'label_values(vmware_host_power_state, cluster_name)',
    'esxi': 'label_values(vmware_host_power_state, host_name)',
    'datastore': 'label_values(vmware_datastore_capacity_size, ds_name)',
    'virtualmachine': 'label_values(vmware_vm_power_state, vm_name)',
}

# Hard-coded query overrides for specific panels (by title) where the original
# Flux uses pivot/join/count and a generic conversion won't work.
TITLE_OVERRIDES = {
    'vSphere Overview': [
        ('C', 'count(count by (dc_name) (vmware_host_power_state))', 'vCenter Summary'),
        ('E', 'count(count by (cluster_name) (vmware_host_power_state{cluster_name!=""}))', 'Cluster Summary'),
        ('F', 'count(count by (host_name) (vmware_host_power_state))', 'ESXi Summary'),
        ('G', 'count(vmware_vm_power_state)', 'VM Summary'),
        ('H', 'count(vmware_datastore_capacity_size)', 'Datastore Summary'),
    ],
    '$clustername Overview': [
        ('A', '(time() - avg by (cluster_name) (vmware_host_boot_timestamp_seconds{cluster_name=~"$clustername"})) * 1000', '{{cluster_name}}'),
        ('D', 'avg by (cluster_name) ((vmware_host_cpu_usage{cluster_name=~"$clustername"} / vmware_host_cpu_max) * 100)', '{{cluster_name}}'),
        ('B', 'avg by (cluster_name) ((vmware_host_memory_usage{cluster_name=~"$clustername"} / vmware_host_memory_max) * 100)', '{{cluster_name}}'),
        ('C', 'avg by (cluster_name) ((vmware_datastore_capacity_size - vmware_datastore_freespace_size) / vmware_datastore_capacity_size * 100)', '{{cluster_name}}'),
    ],
    '$esxi Overprovisioned - CPU': [
        ('A', 'sum by (host_name) (vmware_host_num_cpu{host_name=~"$esxi"})', '{{host_name}}'),
        ('B', 'sum by (host_name) (vmware_vm_num_cpu{host_name=~"$esxi"})', '{{host_name}}'),
        ('C', 'sum by (host_name) (vmware_vm_num_cpu{host_name=~"$esxi"}) / sum by (host_name) (vmware_host_num_cpu{host_name=~"$esxi"})', '{{host_name}}'),
    ],
    '$esxi Overprovisioned - RAM': [
        ('A', 'sum by (host_name) (vmware_host_memory_max{host_name=~"$esxi"}) / 1024', '{{host_name}}'),
        ('B', 'sum by (host_name) (vmware_vm_memory_max{host_name=~"$esxi"})', '{{host_name}}'),
        ('C', 'sum by (host_name) (vmware_vm_memory_max{host_name=~"$esxi"}) / (sum by (host_name) (vmware_host_memory_max{host_name=~"$esxi"}))', '{{host_name}}'),
    ],
    'Datastores - Usage Capacity': [
        ('A', '(vmware_datastore_capacity_size{ds_name=~"$datastore"} - vmware_datastore_freespace_size{ds_name=~"$datastore"}) / vmware_datastore_capacity_size{ds_name=~"$datastore"} * 100', '{{ds_name}}'),
    ],
    '$datastore': [
        ('A', '(vmware_datastore_capacity_size{ds_name=~"$datastore"} - vmware_datastore_freespace_size{ds_name=~"$datastore"}) / vmware_datastore_capacity_size{ds_name=~"$datastore"} * 100', '{{ds_name}}'),
    ],
    'Cluster Storage Adapter': [
        ('A', 'sum by (host_name) (vmware_host_disk_read_average{cluster_name=~"$clustername"})', '{{host_name}} read'),
        ('B', 'sum by (host_name) (vmware_host_disk_write_average{cluster_name=~"$clustername"})', '{{host_name}} write'),
    ],
}


def extract_filters(flux_query):
    """Extract PromQL label filters from a Flux query."""
    filters = []
    for m in re.finditer(r'r\["(\w+)"\]\s*=~\s*/\$\{(\w+):regex\}/', flux_query):
        tag, var = m.group(1), m.group(2)
        promql_label = LABEL_MAP.get(tag)
        if promql_label:
            filters.append(f'{promql_label}=~"${var}"')
    return filters


def extract_measurement_field(target):
    query = target.get('query', '') or ''
    measurement = None
    m = re.search(r'_measurement"\]\s*==\s*"([^"]+)"', query)
    if m:
        measurement = m.group(1)
    elif target.get('measurement'):
        measurement = target['measurement']

    field = None
    m = re.search(r'_field"\]\s*==\s*"([^"]+)"', query)
    if m:
        field = m.group(1)
    else:
        select = target.get('select') or []
        if select and select[0]:
            params = select[0][0].get('params', [])
            if params:
                field = params[0]

    return measurement, field


def build_expr(measurement, field, filters):
    tmpl = METRIC_MAP.get((measurement, field))
    if not tmpl:
        return None
    return tmpl.format(f=','.join(filters))


def convert_legend(alias):
    if not alias:
        return ''
    for k, v in LEGEND_TAG_MAP.items():
        alias = alias.replace(k, v)
    return alias


def convert_target(target):
    measurement, field = extract_measurement_field(target)
    flux_query = target.get('query', '') or ''
    filters = extract_filters(flux_query)
    expr = build_expr(measurement, field, filters)

    new = {
        "datasource": {"type": "prometheus", "uid": DS_UID},
        "expr": expr or "",
        "refId": target.get('refId', 'A'),
        "legendFormat": convert_legend(target.get('alias', '')),
        "editorMode": "code",
        "range": True,
        "instant": False,
    }
    if expr is None:
        new["expr"] = ""  # leave blank for unsupported
    return new


def convert_panel(panel):
    if panel.get('type') == 'row':
        panel['panels'] = [convert_panel(p) for p in panel.get('panels', [])]
        panel.pop('datasource', None)
        return panel

    panel['datasource'] = {"type": "prometheus", "uid": DS_UID}

    title = panel.get('title', '')
    override = TITLE_OVERRIDES.get(title)
    if override:
        new_targets = []
        for refid, expr, legend in override:
            new_targets.append({
                "datasource": {"type": "prometheus", "uid": DS_UID},
                "expr": expr,
                "refId": refid,
                "legendFormat": legend,
                "editorMode": "code",
                "range": False,
                "instant": True,
            })
        panel['targets'] = new_targets
    else:
        new_targets = []
        for t in panel.get('targets', []):
            if t.get('datasource', {}).get('type') == '__expr__':
                continue  # drop math expression refs (override path handles these)
            new_targets.append(convert_target(t))
        panel['targets'] = new_targets

    return panel


def convert_variables(templating):
    new_list = []
    for var in templating.get('list', []):
        name = var.get('name', '')
        if name == 'inter':
            new_list.append(var)
            continue
        if name == 'vcenter':
            new_list.append({
                "name": "vcenter",
                "label": "vCenter Server",
                "type": "constant",
                "query": "vcsim",
                "current": {"text": "vcsim", "value": "vcsim", "selected": True},
                "hide": 2,
                "skipUrlSync": False,
            })
            continue

        q = VAR_QUERY_MAP.get(name)
        if not q:
            continue
        new_list.append({
            "name": name,
            "label": var.get('label', name),
            "type": "query",
            "datasource": {"type": "prometheus", "uid": DS_UID},
            "definition": q,
            "query": {"query": q, "refId": "StandardVariableQuery"},
            "refresh": 2,
            "regex": "",
            "includeAll": True,
            "multi": True,
            "current": {"text": ["All"], "value": ["$__all"]},
            "options": [],
            "sort": 1,
            "skipUrlSync": False,
        })
    templating['list'] = new_list
    return templating


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    dst = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DST
    with open(src) as f:
        d = json.load(f)

    for k in ['__inputs', '__elements', '__requires']:
        d.pop(k, None)

    d['panels'] = [convert_panel(p) for p in d.get('panels', [])]

    if 'templating' in d:
        d['templating'] = convert_variables(d['templating'])

    # Drop import wizard URL link that references original gnetId
    d['links'] = [l for l in d.get('links', []) if l.get('type') != 'dashboards']

    # Drop tags carried over from the InfluxDB/Telegraf template
    d['tags'] = [t for t in d.get('tags', []) if t not in DROP_TAGS]

    d['title'] = 'VMware vSphere - Overview (Prometheus)'
    d['uid'] = 'vmware-vsphere-prom'
    d['id'] = None
    d['version'] = 1
    d.pop('gnetId', None)

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'w') as f:
        json.dump(d, f, indent=2)

    print(f"OK: {src} -> {dst}")


if __name__ == '__main__':
    main()
