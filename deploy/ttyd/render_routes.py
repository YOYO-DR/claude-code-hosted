#!/usr/bin/env python3
"""Regenera dynamic/projects.yml a partir de ports.json. Sin dependencias
externas (PyYAML no esta garantizado en el sistema) — la forma es fija y
simple, se arma como texto."""
import json
import os

BASE = "/opt/panel/deploy"
PORTS_FILE = f"{BASE}/ttyd/ports.json"
OUT_FILE = f"{BASE}/traefik/dynamic/projects.yml"

with open(PORTS_FILE) as f:
    ports = json.load(f)

lines = ["http:"]
if not ports:
    lines.append("  routers: {}")
    lines.append("  services: {}")
else:
    lines.append("  routers:")
    for slug in ports:
        lines.append(f"    proj-{slug}:")
        lines.append(f'      rule: "PathPrefix(`/projects/{slug}/terminal`)"')
        lines.append('      entryPoints: ["websecure"]')
        lines.append("      tls: {}")
        lines.append('      priority: 100')  # gana sobre el catch-all del panel
        lines.append('      middlewares: ["ttyd-auth@file"]')
        lines.append(f"      service: proj-{slug}")
    lines.append("  services:")
    for slug, port in ports.items():
        lines.append(f"    proj-{slug}:")
        lines.append("      loadBalancer:")
        lines.append("        servers:")
        lines.append(f'          - url: "http://127.0.0.1:{port}"')

tmp = OUT_FILE + ".tmp"
with open(tmp, "w") as f:
    f.write("\n".join(lines) + "\n")
os.replace(tmp, OUT_FILE)
