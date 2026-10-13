import json
import math
import sys

import osmium

TYPE_MAP = {
    "maxspeed": "speed",
    "average_speed": "section",
    "traffic_signals": "red_light",
}

# musi być zgodne z filtrem w workflow
ROAD_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "road",
}

EXTRA_SPEED_KEYS = {"source:maxspeed", "zone:maxspeed"}

CELL = 0.002              # rozmiar komórki siatki w stopniach (~220 m)
SAMPLE_M = 40.0           # co ile metrów próbkujemy odcinek drogi
MAX_DIST_M = 40.0         # maksymalna odległość punkt-droga
NO_SPEED_PENALTY_M = 6.0  # kara dla dróg bez maxspeed (woli drogę z limitem)
M_LAT = 110540.0
M_LON = 111320.0


def tags(obj):
    return {t.k: t.v for t in obj.tags}


def is_speed_key(k):
    return k.startswith("maxspeed") or k in EXTRA_SPEED_KEYS


def speed_tags(t):
    return {k: v for k, v in t.items() if is_speed_key(k)}


def seg_dist(plat, plon, la1, lo1, la2, lo2):
    """Odległość punktu od odcinka w metrach (lokalne rzutowanie)."""
    k = math.cos(math.radians(plat))
    ax, ay = (lo1 - plon) * M_LON * k, (la1 - plat) * M_LAT
    bx, by = (lo2 - plon) * M_LON * k, (la2 - plat) * M_LAT
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


# ---------------------------------------------------------------
# Etap 1: fotoradary (węzły) i relacje enforcement
# ---------------------------------------------------------------
class CamReader(osmium.SimpleHandler):
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


cams_file, roads_file, dst = sys.argv[1], sys.argv[2], sys.argv[3]

rd = CamReader()
rd.apply_file(cams_file)

cams = {}

# 1) pojedyncze węzły
for nid, (lat, lon, t) in rd.nodes.items():
    enf = t.get("enforcement")
    if t.get("highway") != "speed_camera" and enf not in TYPE_MAP:
        continue
    cams[f"n{nid}"] = {
        "id": f"n{nid}",
        "lat": lat,
        "lon": lon,
        "type": TYPE_MAP.get(enf, "speed"),
        "direction": t.get("direction") or t.get("camera:direction"),
        "section": None,
        "role": None,
        "_cam": speed_tags(t),
        "_rel": {},
    }

# 2) relacje enforcement (odcinkowy pomiar itd.)
for rid, t, members in rd.rels:
    kind = TYPE_MAP.get(t.get("enforcement"))
    if not kind:
        continue
    rel_speed = speed_tags(t)
    for mtype, ref, role in members:
        if mtype != "n" or ref not in rd.nodes:
            continue
        lat, lon, nt = rd.nodes[ref]
        p = cams.setdefault(f"n{ref}", {
            "id": f"n{ref}",
            "lat": lat,
            "lon": lon,
            "direction": nt.get("direction") or nt.get("camera:direction"),
            "_cam": speed_tags(nt),
            "_rel": {},
        })
        p["type"] = kind
        p["section"] = f"r{rid}"
        p["role"] = role  # device / from / to
        p["_rel"].update(rel_speed)

plist = list(cams.values())
print(f"Punktów: {len(plist)}", flush=True)

# siatka: komórka -> indeksy punktów; hot = komórki w pobliżu punktów
by_cell = {}
hot = set()
for i, p in enumerate(plist):
    c = (math.floor(p["lat"] / CELL), math.floor(p["lon"] / CELL))
    by_cell.setdefault(c, []).append(i)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            hot.add((c[0] + di, c[1] + dj))


# ---------------------------------------------------------------
# Etap 2: dopasowanie najbliższej drogi
# ---------------------------------------------------------------
class Roads(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.best = {}  # indeks punktu -> (score, dist, info)

    def way(self, w):
        hw = w.tags.get("highway")
        if hw not in ROAD_CLASSES:
            return
        try:
            pts = [(n.lat, n.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(pts) < 2:
            return

        cos_f = math.cos(math.radians(pts[0][0]))
        info = None

        for i in range(len(pts) - 1):
            la1, lo1 = pts[i]
            la2, lo2 = pts[i + 1]
            length = math.hypot((la2 - la1) * M_LAT, (lo2 - lo1) * M_LON * cos_f)
            steps = int(length / SAMPLE_M) + 1

            seg_c = None
            for j in range(steps + 1):
                t = j / steps
                k = (
                    math.floor((la1 + (la2 - la1) * t) / CELL),
                    math.floor((lo1 + (lo2 - lo1) * t) / CELL),
                )
                if k in hot:
                    if seg_c is None:
                        seg_c = set()
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            seg_c.update(by_cell.get((k[0] + di, k[1] + dj), ()))
            if not seg_c:
                continue

            for ci in seg_c:
                p = plist[ci]
                d = seg_dist(p["lat"], p["lon"], la1, lo1, la2, lo2)
                if d > MAX_DIST_M:
                    continue
                if info is None:
                    sp = {t_.k: t_.v for t_ in w.tags if is_speed_key(t_.k)}
                    has_speed = any(k_.startswith("maxspeed") for k_ in sp)
                    info = (w.id, hw, w.tags.get("ref"), sp, has_speed)
                score = d + (0 if info[4] else NO_SPEED_PENALTY_M)
                cur = self.best.get(ci)
                if cur is None or score < cur[0]:
                    self.best[ci] = (score, d, info)


roads = Roads()
roads.apply_file(roads_file, locations=True, idx="flex_mem")

# ---------------------------------------------------------------
# Składanie wyniku
# ---------------------------------------------------------------
out = []
n_road = n_speed = n_hgv = 0

for i, p in enumerate(plist):
    road = None
    road_speed = {}
    best = roads.best.get(i)
    if best:
        _, dist, (wid, hw, ref, sp, _hs) = best
        road_speed = sp
        road = {"way": f"w{wid}", "highway": hw, "dist": round(dist, 1)}
        if ref:
            road["ref"] = ref
        n_road += 1

    merged, src = {}, {}
    for name, dct in (("road", road_speed), ("relation", p["_rel"]), ("camera", p["_cam"])):
        for k, v in dct.items():
            merged[k] = v
            src[k] = name

    rec = {k: v for k, v in p.items() if not k.startswith("_")}
    rec["lat"] = round(p["lat"], 6)
    rec["lon"] = round(p["lon"], 6)
    rec["maxspeed"] = merged.get("maxspeed")
    rec["maxspeed_src"] = src.get("maxspeed")
    rec["maxspeed_hgv"] = merged.get("maxspeed:hgv")
    rec["hgv_src"] = src.get("maxspeed:hgv")
    rec["speeds"] = merged or None
    rec["road"] = road

    if rec["maxspeed"]:
        n_speed += 1
    if rec["maxspeed_hgv"]:
        n_hgv += 1
    out.append({k: v for k, v in rec.items() if v is not None})

with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"))

print(f"{dst}: {len(out)} punktów, z drogą: {n_road}, "
      f"z maxspeed: {n_speed}, z maxspeed_hgv: {n_hgv}")
