import json
import sys

import osmium

# enforcement=* -> nasz typ
TYPE_MAP = {
    "maxspeed": "speed",
    "average_speed": "section",
    "traffic_signals": "red_light",
}


def tags(obj):
    return {t.k: t.v for t in obj.tags}


class Reader(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.nodes = {}   # id -> (lat, lon, tagi)
        self.rels = []    # (id, tagi, [(typ, ref, rola)])

    def node(self, n):
        if n.location.valid():
            self.nodes[n.id] = (n.location.lat, n.location.lon, tags(n))

    def relation(self, r):
        t = tags(r)
        if t.get("type") == "enforcement":
            members = [(m.type, m.ref, m.role) for m in r.members]
            self.rels.append((r.id, t, members))


src, dst = sys.argv[1], sys.argv[2]
rd = Reader()
rd.apply_file(src)

points = {}

# 1) Pojedyncze węzły: fotoradary stacjonarne, czerwone światło itp.
for nid, (lat, lon, t) in rd.nodes.items():
    if t.get("highway") == "speed_camera" or "enforcement" in t:
        points[f"n{nid}"] = {
            "id": f"n{nid}",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "type": TYPE_MAP.get(t.get("enforcement"), "speed"),
            "maxspeed": t.get("maxspeed"),
            "direction": t.get("direction") or t.get("camera:direction"),
            "section": None,
            "role": None,
        }

# 2) Relacje enforcement (w tym odcinkowy pomiar prędkości)
for rid, t, members in rd.rels:
    enf = t.get("enforcement")
    kind = TYPE_MAP.get(enf, enf or "other")
    for mtype, ref, role in members:
        if mtype != "n" or ref not in rd.nodes:
            continue  # członków typu way/relation pomijamy
        lat, lon, nt = rd.nodes[ref]
        p = points.setdefault(f"n{ref}", {
            "id": f"n{ref}",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "direction": nt.get("direction") or nt.get("camera:direction"),
            "maxspeed": nt.get("maxspeed"),
        })
        p.update({
            "type": kind,
            "section": f"r{rid}",
            "role": role,  # device / from / to
            "maxspeed": t.get("maxspeed") or p.get("maxspeed"),
        })

out = list(points.values())
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"))
print(f"{dst}: {len(out)} punktów")
