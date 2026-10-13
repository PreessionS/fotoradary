import datetime
import glob
import json
import math
import pathlib

out = pathlib.Path("site")
(out / "tiles").mkdir(parents=True, exist_ok=True)

# Scalanie krajów, duplikaty z pogranicza usuwane po id
unique = {}
for fn in glob.glob("data/*.json"):
    with open(fn, encoding="utf-8") as f:
        for p in json.load(f):
            unique[p["id"]] = p

# Kafelki 1° x 1°
tiles = {}
for p in unique.values():
    key = f'{math.floor(p["lat"])}_{math.floor(p["lon"])}'
    tiles.setdefault(key, []).append(p)

for key, pts in tiles.items():
    with open(out / "tiles" / f"{key}.json", "w", encoding="utf-8") as f:
        json.dump(pts, f, separators=(",", ":"))

index = {
    "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "count": len(unique),
    "tiles": sorted(tiles),
    "attribution": "© OpenStreetMap contributors, ODbL 1.0",
}
with open(out / "index.json", "w", encoding="utf-8") as f:
    json.dump(index, f, indent=1)

print(f"Razem: {len(unique)} punktów, {len(tiles)} kafelków")
