#!/usr/bin/env python3
"""Modernize an older Prometheus-based VMware dashboard for Grafana 13.

Use case: existing Grafana.com dashboards (gnetId 11243, 18017) that already
target vmware_exporter via PromQL, but use Angular-era panel types removed in
Grafana 12+: `graph`, `table-old`, `singlestat`.

What it does:
- Strips import-only sections (__inputs, __requires, __elements)
- Replaces datasource UID variables with the project's real Prometheus UID
- Converts deprecated panel types to modern equivalents
- Strips Angular-only fields that newer Grafana ignores or chokes on
- Lets you assign a new UID/title so multiple imports can coexist

Usage:
  modernize_dashboard.py <src> <dst> [--uid NEW_UID] [--title NEW_TITLE]
"""
import argparse
import json
import os
import sys

DS_UID = "PBFA97CFB590B2093"
DS_UID_PATTERNS = ("${DS_PROMETHEUS-VICTORIAMETRICS}", "${DS_PROMETHEUS}", "NXsTwK_7k")
DROP_TAGS = {"vSphere Telegraf"}

TYPE_MAP = {
    "graph": "timeseries",
    "table-old": "table",
    "singlestat": "stat",
}

DEPRECATED_GRAPH_FIELDS = {
    "aliasColors", "bars", "dashLength", "dashes", "fill", "fillGradient",
    "hiddenSeries", "legend", "lines", "linewidth", "nullPointMode",
    "percentage", "pointradius", "points", "renderer", "seriesOverrides",
    "spaceLength", "stack", "steppedLine", "timeRegions", "tooltip",
    "xaxis", "yaxes", "yaxis", "thresholds",
}

DEPRECATED_STAT_FIELDS = {
    "cacheTimeout", "colorBackground", "colorPostfix", "colorPrefix",
    "colorValue", "colors", "format", "gauge", "mappingType",
    "mappingTypes", "nullPointMode", "nullText", "postfix",
    "postfixFontSize", "prefix", "prefixFontSize", "rangeMaps",
    "sparkline", "tableColumn", "thresholds", "valueFontSize",
    "valueMaps", "valueName", "decimals",
}

DEPRECATED_TABLE_FIELDS = {
    "columns", "fontSize", "styles", "pageSize", "transform",
}


def _is_old_ds(value):
    if isinstance(value, str):
        return any(p in value for p in DS_UID_PATTERNS)
    if isinstance(value, dict):
        uid = value.get("uid", "")
        return uid in DS_UID_PATTERNS or uid.startswith("${")
    return False


def replace_ds_uid(obj):
    if isinstance(obj, dict):
        ds = obj.get("datasource")
        if _is_old_ds(ds):
            obj["datasource"] = {"type": "prometheus", "uid": DS_UID}
        for v in obj.values():
            replace_ds_uid(v)
    elif isinstance(obj, list):
        for v in obj:
            replace_ds_uid(v)


GRAPH_UNIT_MAP = {
    "percent": "percent", "ms": "ms", "kbytes": "kbytes",
    "decmbytes": "decmbytes", "hertz": "hertz", "short": "short",
    "none": "none",
}


def _extract_unit_from_old_graph(panel):
    """Best-effort grab the Y axis unit from old graph config."""
    yaxes = panel.get("yaxes") or []
    if yaxes and isinstance(yaxes[0], dict):
        fmt = yaxes[0].get("format")
        if fmt in GRAPH_UNIT_MAP:
            return GRAPH_UNIT_MAP[fmt]
    return None


def _extract_unit_from_old_stat(panel):
    fmt = panel.get("format")
    return GRAPH_UNIT_MAP.get(fmt, "none")


def modernize_panel_options(panel, old_type, prev_unit):
    new_type = panel["type"]
    if new_type == "timeseries":
        fc = panel.setdefault("fieldConfig", {"defaults": {}, "overrides": []})
        defaults = fc.setdefault("defaults", {})
        defaults.setdefault("color", {"mode": "palette-classic"})
        defaults.setdefault("custom", {
            "drawStyle": "line", "fillOpacity": 10, "lineWidth": 1,
            "showPoints": "never", "spanNulls": False,
            "axisPlacement": "auto",
        })
        if prev_unit:
            defaults["unit"] = prev_unit
        panel["options"] = {
            "legend": {"calcs": ["last", "min", "max"], "displayMode": "table",
                       "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"},
        }
    elif new_type == "stat":
        fc = panel.setdefault("fieldConfig", {"defaults": {}, "overrides": []})
        defaults = fc.setdefault("defaults", {})
        defaults.setdefault("color", {"mode": "thresholds"})
        defaults.setdefault("thresholds", {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}],
        })
        if prev_unit:
            defaults["unit"] = prev_unit
        panel["options"] = {
            "colorMode": "value", "graphMode": "none",
            "justifyMode": "auto", "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
        }
    elif new_type == "table":
        fc = panel.setdefault("fieldConfig", {"defaults": {}, "overrides": []})
        defaults = fc.setdefault("defaults", {})
        defaults.setdefault("custom", {"align": "auto", "cellOptions": {"type": "auto"}})
        panel["options"] = {
            "showHeader": True,
            "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""},
        }


def modernize_panels(panels):
    for p in panels:
        old_type = p.get("type")
        if old_type in TYPE_MAP:
            prev_unit = None
            if old_type == "graph":
                prev_unit = _extract_unit_from_old_graph(p)
            elif old_type == "singlestat":
                prev_unit = _extract_unit_from_old_stat(p)
            p["type"] = TYPE_MAP[old_type]
            drop = (
                DEPRECATED_GRAPH_FIELDS if old_type == "graph" else
                DEPRECATED_STAT_FIELDS if old_type == "singlestat" else
                DEPRECATED_TABLE_FIELDS
            )
            for f in drop:
                p.pop(f, None)
            modernize_panel_options(p, old_type, prev_unit)
        if "panels" in p:
            modernize_panels(p["panels"])


def normalize_variables(templating):
    """Normalize variable query field to dict form for Grafana 11+."""
    for var in templating.get("list", []):
        ds = var.get("datasource")
        if _is_old_ds(ds):
            var["datasource"] = {"type": "prometheus", "uid": DS_UID}
        q = var.get("query")
        if isinstance(q, str) and var.get("type") == "query":
            var["query"] = {"query": q, "refId": "StandardVariableQuery"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--uid")
    ap.add_argument("--title")
    args = ap.parse_args()

    with open(args.src) as f:
        d = json.load(f)

    for k in ["__inputs", "__elements", "__requires"]:
        d.pop(k, None)

    replace_ds_uid(d)
    modernize_panels(d.get("panels", []))
    if "templating" in d:
        normalize_variables(d["templating"])

    d["tags"] = [t for t in d.get("tags", []) if t not in DROP_TAGS]
    d["id"] = None
    d.pop("gnetId", None)
    d.pop("iteration", None)
    d["version"] = 1

    if args.uid:
        d["uid"] = args.uid
    if args.title:
        d["title"] = args.title

    os.makedirs(os.path.dirname(args.dst), exist_ok=True)
    with open(args.dst, "w") as f:
        json.dump(d, f, indent=2)
    print(f"OK: {args.src} -> {args.dst}")


if __name__ == "__main__":
    main()
