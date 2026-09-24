import json, sys
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import transform
from shapely import affinity
import pyproj

ISO2CODE = {'RU-AL':2,'RU-KR':10,'RU-KO':11,'UA-43':93,'RU-ME':12,'RU-UD':18,'RU-CU':21,'RU-ALT':22,'RU-PER':90,
 'RU-ARK':29,'RU-BEL':31,'RU-VLA':33,'RU-VLG':35,'RU-KGD':39,'RU-KLU':40,'RU-KOS':44,'RU-KGN':45,'RU-LIP':48,
 'RU-MAG':49,'RU-MOW':50,'RU-MUR':51,'RU-NIZ':52,'RU-NGR':53,'RU-NVS':54,'RU-PSK':60,'RU-ROS':61,'RU-SVE':66,
 'RU-SMO':67,'RU-TOM':70,'RU-CHE':74,'RU-YAR':76,'RU-NEN':83}
# Субъекты РФ по Конституции РФ, которых нет в слое RUS Natural Earth (границы — по административным
# границам Natural Earth; в ДЭГ-2026 федеральная платформа там не применялась).
EXTRA_NAMES = {'UA-43': 'Республика Крым', 'UA-40': 'Севастополь', 'UA-14': 'Донецкая Народная Республика',
               'UA-09': 'Луганская Народная Республика', 'UA-23': 'Запорожская область', 'UA-65': 'Херсонская область'}
g = json.load(open(sys.argv[1] if len(sys.argv) > 1 else '../data/external/ne_10m_admin_1_states_provinces.geojson', encoding='utf-8'))
fs = [f for f in g['features'] if f['properties']['adm0_a3']=='RUS' or f['properties'].get('iso_3166_2') in EXTRA_NAMES]
proj = pyproj.Transformer.from_crs('EPSG:4326', '+proj=aea +lat_1=52 +lat_2=64 +lat_0=0 +lon_0=100 +datum=WGS84 +units=m', always_xy=True)
def fwd(x, y, z=None):
    import numpy as np
    x = np.where(np.asarray(x) < -30, np.asarray(x) + 360, x)  # Chukotka across antimeridian
    return proj.transform(x, y)
geoms = []
for f in fs:
    s = shape(f['geometry'])
    p = transform(fwd, s)
    polys = list(p.geoms) if isinstance(p, MultiPolygon) else [p]
    big = max(q.area for q in polys)
    polys = [q.buffer(0) if not q.is_valid else q for q in polys if q.area > max(2.5e8, big*0.002)]   # drop tiny islands
    iso = f['properties']['iso_3166_2']
    geoms.append((iso, EXTRA_NAMES.get(iso) or f['properties'].get('name_ru'), MultiPolygon(polys)))
from shapely.ops import unary_union
allg = unary_union([g_ for _,_,g_ in geoms])
minx, miny, maxx, maxy = allg.bounds
W = 1000
k = W / (maxx - minx)
H = (maxy - miny) * k
print('viewBox', W, round(H))
def path(mp, tol):
    mp = mp.simplify(tol / k, preserve_topology=True)
    out = []
    polys = list(mp.geoms) if isinstance(mp, MultiPolygon) else [mp]
    for q in polys:
        for ring in [q.exterior] + list(q.interiors):
            pts = [((x - minx) * k, (maxy - y) * k) for x, y in ring.coords]
            s = 'M' + 'L'.join(f'{x:.1f},{y:.1f}' for x, y in pts[:-1]) + 'Z'
            if len(pts) > 3: out.append(s)
    return ''.join(out)
res = []
for iso, nm, mp in geoms:
    code = ISO2CODE.get(iso)
    if mp.is_empty: print("EMPTY", iso, nm); continue
    c = mp.representative_point()
    res.append({'iso': iso, 'code': code, 'name': nm, 'd': path(mp, 0.6 if code else 0.9),
                'cx': round((c.x - minx) * k, 1), 'cy': round((maxy - c.y) * k, 1)})
json.dump({'w': W, 'h': round(H), 'regions': res}, open('geo.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
import os; print(os.path.getsize('geo.json'), sum(1 for r in res if r['code']))
