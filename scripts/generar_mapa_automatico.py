"""
generar_mapa_automatico.py
Script autónomo (sin Streamlit) para:
1. Descargar datos Excel desde SharePoint
2. Descargar KMZ desde GitHub
3. Generar HTML del mapa
4. Capturar PNG con Playwright
5. Subir HTML + PNG a GitHub Pages

Ejecutado por GitHub Actions cada lunes 8am Lima.
"""

import os
import re
import sys
import json
import base64
import tempfile
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import requests
import pandas as pd
import numpy as np

# ── Leer secrets desde variables de entorno (GitHub Actions secrets) ──
ONEDRIVE_URL_AQUAI  = os.environ.get("ONEDRIVE_URL_AQUAI",  "")
ONEDRIVE_URL_AQUAII = os.environ.get("ONEDRIVE_URL_AQUAII", "")
GITHUB_TOKEN_KMZ    = os.environ.get("GITHUB_TOKEN_KMZ",    "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
GITHUB_OWNER        = os.environ.get("GITHUB_OWNER",        "controloperacionalprize-boss")
GITHUB_REPO         = os.environ.get("GITHUB_REPO",         "mapa_html")
GITHUB_BRANCH       = os.environ.get("GITHUB_BRANCH",       "main")
GITHUB_FILE         = "mapa_mosca.html"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ============================================================
# FUNCIONES DE NORMALIZACIÓN (idénticas al app principal)
# ============================================================
def norm_mod(val):
    if not val:
        return None
    s = str(val).strip().upper()
    match = re.search(r'MOD\s*0*(\d+)', s)
    if match:
        return int(match.group(1))
    match = re.match(r'^M\s*0*(\d+)$', s)
    if match:
        return int(match.group(1))
    match = re.match(r'^0*(\d+)$', s)
    if match:
        return int(match.group(1))
    return None

def norm_tur(val):
    if not val:
        return None
    s = str(val).strip().upper()
    match = re.search(r'M\d+[-\s]T\s*0*(\d+)', s)
    if match:
        tur_n = int(match.group(1))
        return tur_n if tur_n <= 20 else None
    match = re.search(r'\bT\s*0*(\d+)\b', s)
    if match:
        tur_n = int(match.group(1))
        return tur_n if tur_n <= 20 else None
    match = re.match(r'^0*(\d+)$', s)
    if match:
        n = int(match.group(1))
        return n if n <= 20 else None
    return None

def norm_lote(val):
    if not val:
        return None
    s = str(val).strip().upper()
    if not s or s in ('NAN', 'NONE', ''):
        return None
    match = re.match(r'^(\d+)\.0+$', s)
    if match:
        return str(int(match.group(1)))
    s = s.replace('-', '')
    match = re.match(r'^(\d+)[A-Z]*$', s)
    if match:
        return str(int(match.group(1)))
    return s if s else None

def fundo_to_aq(fundo):
    if not fundo:
        return None
    fundo_upper = str(fundo).upper().strip()
    mapping = {
        'ARENA AZUL':   'AQ1',
        'QURI ALLPA':   'AQ2',
        'VIVADIS':      'AQ2',
        'KAWSAY ALLPA': 'AQ2',
        'SANTA TERESA': 'AQ2',
        'AYLLU ALLPA':  'AQ2',
        'AMPLIACION':   'AQ2',
    }
    return mapping.get(fundo_upper)

def get_semaforo_category(val):
    v = float(val)
    if v <= 0:   return 0
    elif v <= 1: return 1
    elif v <= 2: return 2
    elif v <= 3: return 3
    else:        return 4

# ============================================================
# CARGAR DATOS EXCEL
# ============================================================
def load_trampas_anexadas():
    log("Cargando datos Excel desde SharePoint...")

    ARCHIVOS = {
        "AQI":  (ONEDRIVE_URL_AQUAI,  "Bdatos"),
        "AQII": (ONEDRIVE_URL_AQUAII, "BDatos AQU II"),
    }

    COLS = ["LATITUD", "LONGITUD", "FECHA", "FUNDO", "MODULO",
            "TURNO", "TRAMPA", "CAPTURAS", "LOTE", "EMPRESA",
            "SEMANA", "AÑO", "TIPO DE TRAMPA"]

    DTYPE_MAP = {
        "FUNDO": str, "MODULO": str, "TURNO": str, "TRAMPA": str,
        "LOTE": str, "EMPRESA": str, "TIPO DE TRAMPA": str,
        "CAPTURAS": str, "SEMANA": str, "AÑO": str,
    }

    dfs = []
    for key, (url, sheet) in ARCHIVOS.items():
        if not url:
            log(f"  ⚠️ {key}: URL vacía, saltando")
            continue
        try:
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/octet-stream"}
            resp = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "")
                if "html" in content_type:
                    log(f"  ⚠️ {key}: Link expirado o requiere login")
                    continue
                import io
                df_tmp = pd.read_excel(
                    io.BytesIO(resp.content),
                    sheet_name=sheet,
                    engine="openpyxl",
                    usecols=lambda c: c in COLS,
                    dtype=DTYPE_MAP,
                )
                dfs.append(df_tmp)
                log(f"  ✅ {key}: {len(df_tmp)} filas")
            else:
                log(f"  ⚠️ {key}: HTTP {resp.status_code}")
        except Exception as e:
            log(f"  ❌ {key}: {e}")

    if not dfs:
        log("❌ Sin datos disponibles")
        sys.exit(1)

    df = pd.concat(dfs, ignore_index=True, copy=False)

    rename_map = {
        "LATITUD": "lat", "LONGITUD": "lon", "FECHA": "fecha",
        "FUNDO": "fundo", "MODULO": "modulo", "TURNO": "turno",
        "TRAMPA": "trampa", "CAPTURAS": "capturas", "LOTE": "lote",
        "EMPRESA": "empresa", "SEMANA": "semana", "AÑO": "anio",
        "TIPO DE TRAMPA": "tipo_trampa",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    df["lat"]      = pd.to_numeric(df["lat"],      errors="coerce")
    df["lon"]      = pd.to_numeric(df["lon"],      errors="coerce")
    df["capturas"] = pd.to_numeric(df["capturas"], errors="coerce").fillna(0).astype(int)
    df["fecha"]    = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce").dt.date
    df["anio"]     = pd.to_numeric(df["anio"],     errors="coerce").astype('Int64')
    df["semana"]   = pd.to_numeric(df["semana"],   errors="coerce").astype('Int64')

    for c in ["empresa", "fundo", "modulo", "turno", "trampa", "lote", "tipo_trampa"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    mask_lat_ok = df["lat"].isna() | df["lat"].between(-90, 90)
    mask_lon_ok = df["lon"].isna() | df["lon"].between(-180, 180)
    df = df[mask_lat_ok & mask_lon_ok].copy()
    df = df[df["anio"] == 2026].copy()

    log(f"  📊 Total registros 2026: {len(df)}")
    return df

# ============================================================
# CARGAR KMZ DESDE GITHUB
# ============================================================
def download_kmz_from_github():
    log("Descargando KMZ desde GitHub...")
    api_url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/CAMPO_RENDIMIENTO/"
        "contents/MODULOS_PRIZE_PAIJAN.kmz"
    )
    headers = {"Accept": "application/vnd.github.v3.raw"}
    if GITHUB_TOKEN_KMZ:
        headers["Authorization"] = f"token {GITHUB_TOKEN_KMZ}"

    try:
        req  = urllib.request.Request(api_url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        log(f"  ✅ KMZ: {len(data):,} bytes")
        return data
    except urllib.error.HTTPError as e:
        log(f"  ❌ KMZ HTTP {e.code}: {e.reason}")
        return None
    except Exception as ex:
        log(f"  ❌ KMZ Error: {ex}")
        return None

def load_kmz_local(kmz_path):
    import zipfile
    from lxml import etree

    log(f"Parseando KMZ: {kmz_path}")
    kmz_file = Path(kmz_path)
    if not kmz_file.exists():
        log(f"❌ KMZ no encontrado: {kmz_file}")
        return []

    with zipfile.ZipFile(kmz_file, 'r') as kmz:
        kml_files = [f for f in kmz.namelist() if f.lower().endswith('.kml')]
        if not kml_files:
            log("❌ No se encontró .kml dentro del KMZ")
            return []
        kml_content = kmz.read(kml_files[0])

    parser = etree.XMLParser(recover=True, encoding='utf-8')
    root   = etree.fromstring(kml_content, parser=parser)
    nsmap  = {
        'kml': 'http://www.opengis.net/kml/2.2',
        'gx':  'http://www.google.com/kml/ext/2.2'
    }

    polygons = []
    folders  = root.xpath('.//kml:Folder', namespaces=nsmap)
    if not folders:
        folders = root.xpath('.//*[local-name()="Folder"]')

    target_folders = []
    for folder in folders:
        fname_el = folder.xpath('.//kml:name/text()', namespaces=nsmap)
        if not fname_el:
            fname_el = folder.xpath('.//*[local-name()="name"]/text()')
        if fname_el:
            fname = fname_el[0].upper()
            if ('AQ1' in fname or 'AQ2' in fname) and 'MODULO' in fname:
                target_folders.append((folder, fname_el[0]))

    if not target_folders:
        log("❌ No se encontraron carpetas AQ1/AQ2 MODULO en KMZ")
        return []

    placemarks = []
    for target_folder, folder_name in target_folders:
        fps = target_folder.xpath('.//kml:Placemark', namespaces=nsmap)
        if not fps:
            fps = target_folder.xpath('.//*[local-name()="Placemark"]')
        for pm in fps:
            placemarks.append((pm, folder_name))

    for placemark, folder_name in placemarks:
        name_xpath = placemark.xpath('.//kml:name/text()', namespaces=nsmap)
        if not name_xpath:
            name_xpath = placemark.xpath('.//*[local-name()="name"]/text()')
        name = name_xpath[0].strip() if name_xpath else ""

        desc_xpath = placemark.xpath('.//kml:description/text()', namespaces=nsmap)
        if not desc_xpath:
            desc_xpath = placemark.xpath('.//*[local-name()="description"]/text()')
        desc = desc_xpath[0].strip() if desc_xpath else ""

        fundo_aq = None
        mod_n    = None

        fundo_match = re.search(r'(AQ\d+)', folder_name, re.IGNORECASE)
        if fundo_match:
            fundo_aq = fundo_match.group(1).upper()

        mod_match = re.search(r'MODULO\s*0*(\d+)', folder_name, re.IGNORECASE)
        if mod_match:
            mod_n = int(mod_match.group(1))

        if not fundo_aq or not mod_n:
            continue

        coords = []
        coord_xpath = placemark.xpath('.//kml:Polygon//kml:coordinates/text()', namespaces=nsmap)
        if not coord_xpath:
            coord_xpath = placemark.xpath('.//*[local-name()="Polygon"]//*[local-name()="coordinates"]/text()')
        if not coord_xpath:
            coord_xpath = placemark.xpath('.//*[local-name()="coordinates"]/text()')

        if coord_xpath:
            for coord_tuple in coord_xpath[0].strip().split():
                parts = coord_tuple.split(',')
                if len(parts) >= 2:
                    try:
                        coords.append([float(parts[1]), float(parts[0])])
                    except ValueError:
                        pass

        if len(coords) < 3:
            continue

        tur_n = None
        tur_desc_match = re.search(r'<td[^>]*>\s*Turno\s*</td>\s*<td[^>]*>\s*(\d+)', desc, re.IGNORECASE)
        if tur_desc_match:
            tur_n = int(tur_desc_match.group(1))
        if tur_n is None:
            tur_match = re.search(r'[Tt]urno[:\s]*(\d+)', desc)
            if tur_match:
                tur_n = int(tur_match.group(1))
        if tur_n is None:
            tur_match = re.search(r'T[\s\-]?(\d+)', name, re.IGNORECASE)
            if tur_match:
                tur_n = int(tur_match.group(1))
        if tur_n is None:
            tur_n = 1
        if tur_n > 20:
            continue

        lote = None
        lote_desc_match = re.search(r'<td[^>]*>\s*Lote\s*</td>\s*<td[^>]*>\s*(\d+)', desc, re.IGNORECASE)
        if lote_desc_match:
            lote = norm_lote(lote_desc_match.group(1))
        if not lote:
            lote_match = re.search(r'[Ll]ote[:\s]*(\d+)', desc)
            if lote_match:
                lote = norm_lote(lote_match.group(1))
        if not lote:
            lote = f"PZ_{name.replace(' ', '_').strip()}"

        polygons.append({
            "name":      name,
            "coords":    coords,
            "mod_n":     mod_n,
            "tur_n":     tur_n,
            "fundo_aq":  fundo_aq,
            "lote":      lote,
            "lote_name": lote,
        })

    log(f"  ✅ KMZ: {len(polygons)} polígonos válidos")
    return polygons

# ============================================================
# CALCULAR CENTROIDES
# ============================================================
def calcular_lotes_con_centroide(valid_df, kmz_polygons):
    poly_index = {}
    for poly in kmz_polygons:
        fundo_aq = str(poly.get("fundo_aq", "")).upper().strip()
        mod_n    = poly.get("mod_n")
        tur_n    = poly.get("tur_n")
        lote_n   = norm_lote(str(poly.get("lote_name", "")))
        if fundo_aq and mod_n and tur_n:
            key    = f"{fundo_aq}|{mod_n}|{tur_n}|{lote_n}"
            coords = poly.get("coords", [])
            if coords:
                clat = sum(c[0] for c in coords) / len(coords)
                clon = sum(c[1] for c in coords) / len(coords)
                poly_index[key] = {"lat": clat, "lon": clon, "name": poly.get("name", "")}

    lotes_markers = []
    for row in valid_df.to_dict("records"):
        fundo_aq = fundo_to_aq(str(row.get("fundo", "")))
        mod_n    = norm_mod(str(row.get("modulo", "")))
        tur_n    = norm_tur(str(row.get("turno",  "")))
        lote_n   = norm_lote(str(row.get("lote",  "")))
        if not fundo_aq or not mod_n or not tur_n:
            continue

        key       = f"{fundo_aq}|{mod_n}|{tur_n}|{lote_n}"
        centroide = poly_index.get(key)

        if centroide:
            trampa_val    = str(row.get("trampa", "")).upper()
            es_perimetral = "CASERAS PERIMETRALES" in trampa_val

            if es_perimetral:
                poly_match = next(
                    (p for p in kmz_polygons
                     if str(p.get("fundo_aq", "")).upper() == fundo_aq
                     and p.get("mod_n") == mod_n
                     and p.get("tur_n") == tur_n
                     and norm_lote(str(p.get("lote_name", ""))) == lote_n),
                    None
                )
                if poly_match and len(poly_match.get("coords", [])) >= 3:
                    from shapely.geometry import Polygon as ShapelyPoly, Point
                    from shapely.ops import nearest_points as shapely_nearest
                    coords       = poly_match["coords"]
                    shapely_c    = [(c[1], c[0]) for c in coords]
                    poly_shp     = ShapelyPoly(shapely_c)
                    centroid_pt  = Point(centroide["lon"], centroide["lat"])
                    pt_borde, _  = shapely_nearest(poly_shp.exterior, centroid_pt)
                    lat_final    = pt_borde.y
                    lon_final    = pt_borde.x
                else:
                    lat_final = centroide["lat"]
                    lon_final = centroide["lon"]
            else:
                lat_final = centroide["lat"]
                lon_final = centroide["lon"]
            con_kmz = True
        else:
            lat_excel = row.get("lat")
            lon_excel = row.get("lon")
            try:
                lat_val = float(lat_excel) if lat_excel is not None else None
                lon_val = float(lon_excel) if lon_excel is not None else None
            except (ValueError, TypeError):
                lat_val = None
                lon_val = None

            if (lat_val is not None and lon_val is not None
                    and lat_val != -9999.0 and lon_val != -9999.0
                    and not pd.isna(lat_val) and not pd.isna(lon_val)):
                lat_final = lat_val
                lon_final = lon_val
                con_kmz   = False
            else:
                continue

        lotes_markers.append({
            "lat":      lat_final,
            "lon":      lon_final,
            "capturas": float(row.get("capturas", 0)),
            "fundo":    str(row.get("fundo",  "")),
            "modulo":   str(row.get("modulo", "")),
            "turno":    str(row.get("turno",  "")),
            "lote":     str(row.get("lote",   "")),
            "trampa":   str(row.get("trampa", "")),
            "key_kmz":  key,
            "con_kmz":  con_kmz,
            "modulo_n": mod_n,
            "turno_n":  tur_n,
            "fundo_aq": fundo_aq,
        })

    log(f"  ✅ Marcadores: {len(lotes_markers)} ({sum(1 for m in lotes_markers if m['con_kmz'])} con KMZ)")
    return lotes_markers

# ============================================================
# GENERAR CONTORNOS GAUSSIANOS
# ============================================================
def generar_contornos_gauss(lotes, polygons_kmz, num_niveles=10, grosor_lineas=3, opacidad_fill=0.65):
    from scipy.ndimage import gaussian_filter
    from scipy.spatial import cKDTree
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    if len(lotes) < 3:
        return {"fills": [], "lines": [], "opacidad": opacidad_fill}

    lats = np.array([d["lat"]      for d in lotes])
    lons = np.array([d["lon"]      for d in lotes])
    caps = np.array([d["capturas"] for d in lotes], dtype=float)
    z_max_real = float(caps.max()) if len(caps) > 0 else 3.0

    GRID = 200
    all_poly_lats = [c[0] for poly in polygons_kmz for c in poly.get("coords", [])]
    all_poly_lons = [c[1] for poly in polygons_kmz for c in poly.get("coords", [])]

    if all_poly_lats:
        lat_min, lat_max = min(all_poly_lats), max(all_poly_lats)
        lon_min, lon_max = min(all_poly_lons), max(all_poly_lons)
    else:
        lat_min, lat_max = lats.min(), lats.max()
        lon_min, lon_max = lons.min(), lons.max()

    dlat = (lat_max - lat_min) * 0.05 or 0.005
    dlon = (lon_max - lon_min) * 0.05 or 0.005
    lat_min -= dlat; lat_max += dlat
    lon_min -= dlon; lon_max += dlon

    grid_lat = np.linspace(lat_min, lat_max, GRID)
    grid_lon = np.linspace(lon_min, lon_max, GRID)
    lon_g, lat_g = np.meshgrid(grid_lon, grid_lat)

    metros_por_px_lat = ((lat_max - lat_min) * 111000) / GRID
    metros_por_px_lon = ((lon_max - lon_min) * 111000 * np.cos(np.radians(np.mean(lats)))) / GRID

    coords_pts  = np.column_stack([lons, lats])
    tree        = cKDTree(coords_pts)
    dists_nn, _ = tree.query(coords_pts, k=2)
    dist_vecino = float(np.median(dists_nn[:, 1]))
    sigma       = max(0.0005, min(dist_vecino * 0.8, 0.003))

    Z_num = np.zeros((GRID, GRID))
    Z_den = np.zeros((GRID, GRID))
    for la, lo, ca in zip(lats, lons, caps):
        dx    = lon_g - lo
        dy    = lat_g - la
        dist2 = dx**2 + dy**2
        radio = (3 * sigma) ** 2
        peso  = np.where(dist2 <= radio, np.exp(-dist2 / (2 * sigma**2)), 0.0)
        Z_num += peso * ca
        Z_den += peso

    Z = np.where(Z_den > 1e-10, Z_num / Z_den, np.nan)

    sigma_px = max(0.3, min((50 / metros_por_px_lat + 50 / metros_por_px_lon) / 2, 1.5))
    Z_temp     = gaussian_filter(np.where(np.isnan(Z), 0.0, Z), sigma=sigma_px)
    Z_den_temp = gaussian_filter(np.where(np.isnan(Z), 0.0, 1.0), sigma=sigma_px)
    Z          = np.where(Z_den_temp > 0.01, Z_temp / Z_den_temp, np.nan)

    mask = np.zeros((GRID, GRID), dtype=bool)
    if polygons_kmz:
        try:
            from shapely.geometry import Polygon as ShapelyPolygon, Point
            from shapely.ops import unary_union
            from shapely.prepared import prep
            shapes = []
            for poly in polygons_kmz:
                coords = poly.get("coords", [])
                if len(coords) >= 3:
                    shp = ShapelyPolygon([(c[1], c[0]) for c in coords])
                    if not shp.is_valid:
                        shp = shp.buffer(0)
                    if shp.is_valid and not shp.is_empty:
                        shapes.append(shp)
            if shapes:
                union     = unary_union(shapes).buffer(0.0001)
                prep_u    = prep(union)
                flat_lons = lon_g.ravel()
                flat_lats = lat_g.ravel()
                inside    = np.array([prep_u.contains(Point(flon, flat)) for flon, flat in zip(flat_lons, flat_lats)])
                mask      = inside.reshape(GRID, GRID)
            else:
                mask[:] = True
        except ImportError:
            mask[:] = True
    else:
        mask[:] = True

    Z_masked = np.where(mask, Z, np.nan)
    z_valid  = Z_masked[~np.isnan(Z_masked)]
    if len(z_valid) == 0:
        return {"fills": [], "lines": [], "opacidad": opacidad_fill}

    z_max_grilla = float(z_valid.max())
    if z_max_grilla > 0:
        Z_masked = Z_masked * (z_max_real / z_max_grilla)

    z_valid = Z_masked[~np.isnan(Z_masked)]
    z_min   = float(z_valid.min())
    vmin    = 0
    vmax    = max(4.0, z_max_real)

    colors_semaforo = [
        (0 / vmax, "#90EE90"),
        (1 / vmax, "#00FF00"),
        (2 / vmax, "#FFFF00"),
        (3 / vmax, "#FFA500"),
        (4 / vmax, "#FF0000"),
        (1.0,      "#FF0000"),
    ]
    cmap        = mcolors.LinearSegmentedColormap.from_list("semaforo", colors_semaforo, N=256)
    N_bands     = 256
    levels_cont = np.linspace(vmin, vmax, N_bands)

    levels_lines_base  = [l for l in [1, 2, 3, 4] if z_min < l < z_max_real]
    extra              = np.linspace(z_min, z_max_real, num_niveles + 1)[1:-1]
    levels_lines_extra = [float(l) for l in extra if l not in levels_lines_base and z_min < l < z_max_real]
    levels_lines       = sorted(set(levels_lines_base + levels_lines_extra))

    fig, ax = plt.subplots()
    cf = ax.contourf(lon_g, lat_g, Z_masked, levels=levels_cont, cmap=cmap, vmin=vmin, vmax=vmax)
    cl = ax.contour(lon_g, lat_g, Z_masked, levels=levels_lines, colors="black", linewidths=grosor_lineas) if levels_lines else None

    fills = []
    for i in range(len(cf.allsegs)):
        level_val = levels_cont[i] if i < len(levels_cont) else 0
        rgba      = cmap((level_val - vmin) / max(vmax - vmin, 1))
        color_hex = mcolors.to_hex(rgba)
        for seg in cf.allsegs[i]:
            if len(seg) >= 3:
                fills.append({"color": color_hex, "coords": [[float(p[1]), float(p[0])] for p in seg]})

    lines = []
    if cl is not None:
        for i, level in enumerate(levels_lines):
            segs = cl.allsegs[i] if i < len(cl.allsegs) else []
            for seg in segs:
                if len(seg) >= 2:
                    lines.append({"level": float(level), "coords": [[float(p[1]), float(p[0])] for p in seg], "grosor": grosor_lineas})

    plt.close(fig)
    log(f"  ✅ Contornos: {len(fills)} fills, {len(lines)} líneas")
    return {"fills": fills, "lines": lines, "opacidad": opacidad_fill}

# ============================================================
# SUBIR ARCHIVO A GITHUB
# ============================================================
def push_file_github(api_url, contenido, branch, mensaje, headers, es_binario=False):
    sha = None
    try:
        req  = urllib.request.Request(api_url + f"?ref={branch}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        sha  = data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return False, f"Error leyendo archivo ({e.code})"
    except Exception as ex:
        return False, f"Error de red: {ex}"

    content_b64 = (
        base64.b64encode(contenido).decode()
        if es_binario
        else base64.b64encode(contenido.encode("utf-8")).decode()
    )
    payload = {"message": mensaje, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha

    try:
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(api_url, data=data, headers=headers, method="PUT")
        urllib.request.urlopen(req, timeout=60)
        return True, ""
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return False, f"GitHub API error {e.code}: {body}"
    except Exception as ex:
        return False, f"Error: {ex}"

# ============================================================
# TRAMPAS A GENERAR AUTOMÁTICAMENTE
# Agrega o quita trampas aquí según necesites
# ============================================================
TRAMPAS_AUTO = [
    "CASERAS SIPRIVI",
    "SENASA",
]

# ============================================================
# HELPERS: procesar datos para UNA trampa específica
# ============================================================
def procesar_trampa(df_semana, trampa_nombre, kmz_polygons):
    """
    Filtra por trampa, agrega por lote, calcula marcadores y contornos.
    Retorna (lotes_markers, lotes_etiquetas, contornos, n_registros)
    """
    dff = df_semana[
        df_semana["trampa"].str.upper() == trampa_nombre.upper()
    ].copy()

    if dff.empty:
        log(f"  ⚠️ Sin registros para trampa: {trampa_nombre}")
        return [], [], {"fills": [], "lines": [], "opacidad": 0.65}, 0

    log(f"  📊 {trampa_nombre}: {len(dff)} registros")

    dff["lote"]     = dff["lote"].fillna("").astype(str).str.strip()
    dff["lat"]      = dff["lat"].fillna(-9999.0)
    dff["lon"]      = dff["lon"].fillna(-9999.0)
    dff["modulo_n"] = dff["modulo"].apply(lambda x: norm_mod(str(x)))
    dff["turno_n"]  = dff["turno"].apply(lambda x: norm_tur(str(x)))
    dff["lote_n"]   = dff["lote"].apply(lambda x: norm_lote(str(x)))

    dff_agg = (
        dff
        .groupby(["fundo", "modulo_n", "turno_n", "lote_n", "trampa"], as_index=False)
        .agg({
            "capturas":    "sum",
            "tipo_trampa": "first",
            "lat":         "first",
            "lon":         "first",
            "modulo":      "first",
            "turno":       "first",
            "lote":        "first",
        })
    )

    lotes_markers = calcular_lotes_con_centroide(dff_agg, kmz_polygons)

    # ── Etiquetas por turno ──
    turnos_agrupados = defaultdict(lambda: {
        "lats": [], "lons": [], "capturas": 0,
        "lotes": [], "fundo": "", "modulo": "", "turno": "", "con_kmz": False
    })
    for m in lotes_markers:
        key = f"{m['fundo_aq']}|{m['modulo_n']}|{m['turno_n']}"
        g   = turnos_agrupados[key]
        g["lats"].append(m["lat"])
        g["lons"].append(m["lon"])
        g["capturas"] += m["capturas"]
        g["fundo"]   = m["fundo"]
        g["modulo"]  = m["modulo"]
        g["turno"]   = m["turno"]
        g["con_kmz"] = g["con_kmz"] or m.get("con_kmz", False)
        lote_val = m.get("lote", "")
        if lote_val and lote_val not in g["lotes"]:
            g["lotes"].append(lote_val)

    lotes_etiquetas = [
        {
            "lat":      sum(g["lats"]) / len(g["lats"]),
            "lon":      sum(g["lons"]) / len(g["lons"]),
            "capturas": g["capturas"],
            "fundo":    g["fundo"],
            "modulo":   g["modulo"],
            "turno":    g["turno"],
            "lotes":    sorted(g["lotes"]),
            "n_lotes":  len(g["lotes"]),
            "con_kmz":  g["con_kmz"],
        }
        for g in turnos_agrupados.values() if g["lats"]
    ]

    # ── Contornos gaussianos ──
    lotes_para_contorno = [
        {"lat": m["lat"], "lon": m["lon"], "capturas": m["capturas"]}
        for m in lotes_markers if m.get("con_kmz")
    ]
    keys_con_datos     = set(m["key_kmz"] for m in lotes_markers if m.get("con_kmz"))
    polygons_con_datos = [
        p for p in kmz_polygons
        if f"{str(p.get('fundo_aq','')).upper().strip()}|{p.get('mod_n')}|{p.get('tur_n')}|{norm_lote(str(p.get('lote_name', '')))}" in keys_con_datos
    ]

    contornos = {"fills": [], "lines": [], "opacidad": 1.0}
    if len(lotes_para_contorno) >= 3:
        contornos = generar_contornos_gauss(
            lotes_para_contorno, polygons_con_datos,
            num_niveles=10, grosor_lineas=1, opacidad_fill=1.0
        )

    return lotes_markers, lotes_etiquetas, contornos, len(dff)


def capturar_png_playwright(html_with_data):
    """Abre el HTML con Playwright y retorna los bytes del PNG."""
    from playwright.sync_api import sync_playwright
    import io

    tmp_html = tempfile.NamedTemporaryFile(
        delete=False, suffix=".html", mode="w", encoding="utf-8"
    )
    tmp_html.write(html_with_data)
    tmp_html.close()

    png_bytes = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-web-security", "--allow-file-access-from-files"]
            )
            page = browser.new_page(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=2,
            )
            page.goto(f"file://{tmp_html.name}", wait_until="networkidle")
            page.wait_for_timeout(3000)

            try:
                page.evaluate("() => { activarModoPNGGeneral(); }")
            except Exception:
                pass

            page.wait_for_timeout(2000)

            # Ajustar zoom al contenido
            try:
                page.evaluate("""
                    () => {
                        if (!window.map) return;
                        let bounds = null;
                        window.map.eachLayer(function(layer) {
                            if (layer.getBounds) {
                                try {
                                    const b = layer.getBounds();
                                    if (b && b.isValid()) {
                                        const c = b.getCenter();
                                        if (Math.abs(c.lat) > 0.5 && Math.abs(c.lng) > 0.5) {
                                            bounds = bounds ? bounds.extend(b) : b;
                                        }
                                    }
                                } catch(e) {}
                            }
                        });
                        if (bounds && bounds.isValid()) {
                            window.map.fitBounds(bounds, { padding: [10, 10] });
                        }
                    }
                """)
            except Exception:
                pass

            page.wait_for_timeout(3000)

            try:
                page.evaluate("""
                    () => {
                        const s = document.createElement('style');
                        s.textContent = '* { animation: none !important; }';
                        document.head.appendChild(s);
                        document.querySelectorAll('.leaflet-marker-icon').forEach(el => {
                            el.style.visibility = 'visible';
                            el.style.opacity = '1';
                        });
                        if (window.map) window.map.invalidateSize(true);
                    }
                """)
            except Exception:
                pass

            try:
                page.wait_for_selector(".leaflet-tile-loaded", timeout=15000)
            except Exception:
                pass

            page.wait_for_timeout(4000)

            try:
                png_bytes = page.locator("#mapContainer").screenshot()
            except Exception:
                png_bytes = page.screenshot(full_page=False)

            browser.close()
    finally:
        try:
            os.unlink(tmp_html.name)
        except Exception:
            pass

    return png_bytes


# ============================================================
# MAIN
# ============================================================
def main():
    log("=" * 55)
    log("🗺️  GENERADOR AUTOMÁTICO MAPA MOSCA")
    log(f"📅  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (hora UTC)")
    log("=" * 55)

    # ── 1. Cargar datos ──
    df = load_trampas_anexadas()

    # ── 2. Semana más reciente ──
    semana_actual = 22
    log(f"📊 Semana más reciente: S{semana_actual}")

    df_semana = df[df["semana"] == semana_actual].copy()
    log(f"📊 Total registros S{semana_actual}: {len(df_semana)}")

    # ── 3. Cargar KMZ (una sola vez para todas las trampas) ──
    kmz_bytes    = download_kmz_from_github()
    kmz_polygons = []
    if kmz_bytes:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".kmz")
        tmp.write(kmz_bytes)
        tmp.close()
        kmz_polygons = load_kmz_local(tmp.name)
        os.unlink(tmp.name)
    else:
        log("⚠️ Sin KMZ, continuando sin polígonos")

    # ── 4. Leer HTML base (una sola vez) ──
    html_base_path = Path(__file__).parent.parent / "mapa_streamlit_js.html"
    if not html_base_path.exists():
        log(f"❌ No se encontró mapa_streamlit_js.html en {html_base_path}")
        sys.exit(1)

    with open(html_base_path, "r", encoding="utf-8") as f:
        html_base = f.read()

    # ── 5. Headers GitHub (una sola vez) ──
    ts        = datetime.now().strftime("%Y-%m-%d %H:%M")
    gh_headers = {
        "Authorization":        f"Bearer {GITHUB_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "Content-Type":         "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_repo = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"
    carpeta   = f"S{semana_actual}"

    # ── 6. Iterar sobre cada trampa ──
    from PIL import Image
    import io as _io

    resultados = []

    for trampa in TRAMPAS_AUTO:
        log("-" * 55)
        log(f"🪤  Procesando trampa: {trampa}")

        # Nombre de archivo igual al patrón existente
        trampa_sufijo = trampa.replace(" ", "_").upper()[:20]
        sufijo        = f"A2026_S{semana_actual}_T-{trampa_sufijo}"

        # ── Procesar datos ──
        lotes_markers, lotes_etiquetas, contornos, n_reg = procesar_trampa(
            df_semana, trampa, kmz_polygons
        )

        if not lotes_markers:
            log(f"  ⏭️ Saltando {trampa} (sin datos)")
            resultados.append({"trampa": trampa, "ok": False, "motivo": "sin datos"})
            continue

        # ── Serializar JSON ──
        map_data_optimized = [
            {
                "lat":      m["lat"],
                "lon":      m["lon"],
                "capturas": m["capturas"],
                "fundo":    m["fundo"],
                "modulo":   m["modulo"],
                "turno":    m["turno"],
                "lote":     m["lote"],
                "trampa":   m["trampa"],
                "con_kmz":  m.get("con_kmz", False),
            }
            for m in lotes_markers
        ]

        viz_config = {
            "modo_color":        "Curvas de Nivel",
            "metodo_interp":     "Lotes KMZ",
            "num_niveles":       10,   # slider "Número de líneas" = 10
            "grosor_lineas":     1,    # slider "Grosor líneas" = 1
            "mostrar_etiquetas": False,
            "opacidad_relleno":  100,  # slider "Opacidad (%)" = 100
            "buffer_val":        0.010,
            "mostrar_vectores":  False,
            "n_arrows":          15,
            "escala_flecha":     6,
            "head_size":         6,
            "min_mag":           0.05,
            "color_flechas":     "#1a1aff",
            "semaforización": {
                "blanco": True, "verde": True, "amarillo": True,
                "naranja": True, "rojo": True,
            }
        }

        data_json = json.dumps({
            "data":        map_data_optimized,
            "config":      viz_config,
            "recordCount": n_reg,
            "polygons":    kmz_polygons,
            "githubToken": "",
            "contornos":   contornos,
            "lotes":       lotes_etiquetas,
        }, separators=(",", ":"), ensure_ascii=False)

        html_with_data = (
            f'<script>\nwindow.streamlitData = {data_json};\n</script>\n'
            + html_base
        )
        html_with_data = html_with_data.replace(
            "<head>",
            "<head>\n"
            '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\n'
            '<meta http-equiv="Pragma" content="no-cache">\n'
            '<meta http-equiv="Expires" content="0">\n'
        )

        mensaje = f"Auto: {trampa} {ts} | {sufijo}"

        # ── Subir HTML histórico ──
        nombre_h = f"historico/{carpeta}/mapa_{sufijo}.html"
        ok_h, res_h = push_file_github(
            f"{base_repo}/{nombre_h}",
            html_with_data, GITHUB_BRANCH, mensaje, gh_headers
        )
        if ok_h:
            log(f"  ✅ HTML: {nombre_h}")
        else:
            log(f"  ⚠️ HTML falló: {res_h}")

        # ── Capturar PNG ──
        log(f"  📸 Capturando PNG...")
        try:
            png_bytes = capturar_png_playwright(html_with_data)

            if png_bytes:
                img = Image.open(_io.BytesIO(png_bytes))
                log(f"  📐 {img.width}×{img.height}px")

                nombre_png = f"historico_png/{carpeta}/mapa_{sufijo}.png"
                ok_png, res_png = push_file_github(
                    f"{base_repo}/{nombre_png}",
                    png_bytes, GITHUB_BRANCH,
                    mensaje, gh_headers, es_binario=True
                )
                if ok_png:
                    url_png = f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/{nombre_png}"
                    log(f"  ✅ PNG: {url_png}")
                    resultados.append({"trampa": trampa, "ok": True, "url": url_png})
                else:
                    log(f"  ⚠️ PNG no subido: {res_png}")
                    resultados.append({"trampa": trampa, "ok": False, "motivo": res_png})
            else:
                log(f"  ⚠️ PNG vacío")
                resultados.append({"trampa": trampa, "ok": False, "motivo": "png vacío"})

        except Exception as e:
            import traceback
            log(f"  ❌ Error PNG: {e}")
            log(traceback.format_exc())
            resultados.append({"trampa": trampa, "ok": False, "motivo": str(e)})

    # ── 7. Resumen final ──
    log("=" * 55)
    log("📋 RESUMEN")
    for r in resultados:
        estado = "✅" if r["ok"] else "❌"
        detalle = r.get("url", r.get("motivo", ""))
        log(f"  {estado} {r['trampa']}: {detalle}")
    log("=" * 55)
    log("✅ PROCESO COMPLETADO")
    log("=" * 55)

if __name__ == "__main__":
    main()