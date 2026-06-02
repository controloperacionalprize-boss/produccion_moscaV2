import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import re

st.set_page_config(
    page_title="Mapa Epidemiológico - Mosca de la Fruta",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    #MainMenu, footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# FUNCIONES DE NORMALIZACIÓN (igual que JavaScript)
# ============================================================
def norm_mod(val) -> int | None:
    if not val:
        return None
    s = str(val).strip().upper()

    # MOD 01, MOD01, MOD 1, MOD1, MOD 03 → número
    match = re.search(r'MOD\s*0*(\d+)', s)
    if match:
        return int(match.group(1))

    # M01, M02, M03, M1, M2 (solo, sin guion después) → número
    # Pero NO capturar M01-T3 (eso es turno compuesto)
    match = re.match(r'^M\s*0*(\d+)$', s)
    if match:
        return int(match.group(1))

    # Solo número: "1", "01", "3" → número
    match = re.match(r'^0*(\d+)$', s)
    if match:
        return int(match.group(1))

    return None


def norm_tur(val) -> int | None:
    if not val:
        return None
    s = str(val).strip().upper()

    # M1-T6, M2-T10, M01-T3, M03-T2 → número después de T
    # Cubre formato SENASA con módulo incluido en turno
    match = re.search(r'M\d+[-\s]T\s*0*(\d+)', s)
    if match:
        tur_n = int(match.group(1))
        return tur_n if tur_n <= 20 else None

    # T08, T01, T10, T03 → número después de T
    match = re.search(r'\bT\s*0*(\d+)\b', s)
    if match:
        tur_n = int(match.group(1))
        return tur_n if tur_n <= 20 else None

    # Solo número: "6", "06" → número
    match = re.match(r'^0*(\d+)$', s)
    if match:
        n = int(match.group(1))
        return n if n <= 20 else None

    return None

def norm_lote(val) -> str | None:
    """Normalizar lote: '1.0' → '1', '115-B' → '115B'"""
    if not val:
        return None
    s = str(val).strip().upper()
    if not s or s in ('NAN', 'NONE', ''):
        return None
    match = re.match(r'^(\d+)\.0+$', s)
    if match:
        return match.group(1)
    s = s.replace('-', '')
    return s if s else None

def fundo_to_aq(fundo) -> str | None:
    """Mapear fundo a código AQ"""
    if not fundo:
        return None
    fundo_upper = str(fundo).upper().strip()
    mapping = {
        # AQ1
        'ARENA AZUL':   'AQ1',
        # AQ2 - Fundo 1: Quri Allpa / Vivadis (mismo lugar, dos nombres)
        'QURI ALLPA':   'AQ2',
        'VIVADIS':      'AQ2',
        # AQ2 - Fundo 2: Kawsay Allpa / Santa Teresa (mismo lugar, dos nombres)
        'KAWSAY ALLPA': 'AQ2',
        'SANTA TERESA': 'AQ2',
        # AQ2 - Fundo 3
        'AYLLU ALLPA':  'AQ2',
    }
    return mapping.get(fundo_upper)

def get_semaforo_category(val: float) -> int:
    v = float(val)
    if v <= 0:    return 0
    elif v < 1.5: return 1
    elif v < 2.5: return 2
    else:         return 3


# ============================================================
# CALCULAR LOTES CON CENTROIDE KMZ (OPCIÓN A)
# ============================================================
def calcular_lotes_con_centroide(valid: pd.DataFrame, kmz_polygons: list[dict]) -> list[dict]:
    # ── Indexar polígonos KMZ por clave normalizada ──
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
                poly_index[key] = {
                    "lat":  clat,
                    "lon":  clon,
                    "name": poly.get("name", ""),
                }

    lotes_markers = []
    con_kmz_count = 0
    sin_match     = 0

    for row in valid.to_dict("records"):
        fundo_aq = fundo_to_aq(str(row.get("fundo", "")))
        mod_n    = norm_mod(str(row.get("modulo", "")))
        tur_n    = norm_tur(str(row.get("turno",  "")))
        lote_n   = norm_lote(str(row.get("lote",  "")))

        if not fundo_aq or not mod_n or not tur_n:
            sin_match += 1
            continue

        key       = f"{fundo_aq}|{mod_n}|{tur_n}|{lote_n}"
        centroide = poly_index.get(key)

        if centroide:
            lat_final = centroide["lat"]
            lon_final = centroide["lon"]
            con_kmz   = True
            con_kmz_count += 1
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
                    and lat_val != -9999.0
                    and lon_val != -9999.0
                    and not pd.isna(lat_val)
                    and not pd.isna(lon_val)):
                lat_final = lat_val
                lon_final = lon_val
                con_kmz   = False
                sin_match += 1
            else:
                sin_match += 1
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
            "modulo_n": mod_n,   # ← normalizado
            "turno_n":  tur_n,   # ← normalizado
            "fundo_aq": fundo_aq, # ← normalizado
        })

    st.sidebar.caption(
        f"🔗 Con KMZ: {con_kmz_count} | "
        f"Fallback GPS: {sin_match} | "
        f"Total: {len(lotes_markers)}"
    )

    return lotes_markers
# ============================================================
# GENERAR CONTORNOS GAUSSIANOS (PYTHON → JS)
# ============================================================
def generar_contornos_gauss(
    lotes:        list[dict],
    polygons_kmz: list[dict],
    num_niveles:  int   = 10,
    grosor_lineas:int   = 3,
    opacidad_fill:float = 0.65,
) -> dict:
    import numpy as np
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

    # ── Máximo real de capturas (antes de cualquier suavizado) ──
    z_max_real = float(caps.max()) if len(caps) > 0 else 3.0

    # ── Grilla con bbox de polígonos KMZ ──
    GRID = 200

    all_poly_lats = []
    all_poly_lons = []
    for poly in polygons_kmz:
        for coord in poly.get("coords", []):
            all_poly_lats.append(coord[0])
            all_poly_lons.append(coord[1])

    if all_poly_lats:
        lat_min = min(all_poly_lats)
        lat_max = max(all_poly_lats)
        lon_min = min(all_poly_lons)
        lon_max = max(all_poly_lons)
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

    # ── Calcular metros por pixel ──
    lat_range         = lat_max - lat_min
    lon_range         = lon_max - lon_min
    metros_por_px_lat = (lat_range * 111000) / GRID
    metros_por_px_lon = (lon_range * 111000 * np.cos(np.radians(np.mean(lats)))) / GRID

    # ── Calcular sigma en base a distancia al vecino más cercano ──
    coords_pts  = np.column_stack([lons, lats])
    tree        = cKDTree(coords_pts)
    dists_nn, _ = tree.query(coords_pts, k=2)
    dist_vecino = float(np.median(dists_nn[:, 1]))
    sigma       = max(0.0005, min(dist_vecino * 0.8, 0.003))

    # ── PROMEDIO PONDERADO LOCAL por punto y vecinos cercanos ──
    Z_num = np.zeros((GRID, GRID))
    Z_den = np.zeros((GRID, GRID))

    for la, lo, ca in zip(lats, lons, caps):
        dx     = lon_g - lo
        dy     = lat_g - la
        dist2  = dx**2 + dy**2
        radio  = (3 * sigma) ** 2
        dentro = dist2 <= radio
        peso   = np.where(dentro, np.exp(-dist2 / (2 * sigma**2)), 0.0)
        Z_num += peso * ca
        Z_den += peso

    Z = np.where(Z_den > 1e-10, Z_num / Z_den, np.nan)

    # ── Suavizado mínimo ──
    metros_sigma = 50
    sigma_lat    = metros_sigma / metros_por_px_lat
    sigma_lon    = metros_sigma / metros_por_px_lon
    sigma_px     = max(0.3, min((sigma_lat + sigma_lon) / 2, 1.5))

    Z_temp     = np.where(np.isnan(Z), 0.0, Z)
    Z_temp     = gaussian_filter(Z_temp, sigma=sigma_px)
    Z_den_temp = np.where(np.isnan(Z), 0.0, 1.0)
    Z_den_temp = gaussian_filter(Z_den_temp, sigma=sigma_px)
    Z          = np.where(Z_den_temp > 0.01, Z_temp / Z_den_temp, np.nan)

    # ── Recortar con Shapely ──
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
                    shapely_coords = [(c[1], c[0]) for c in coords]
                    try:
                        shp = ShapelyPolygon(shapely_coords)
                        if not shp.is_valid:
                            shp = shp.buffer(0)
                        if shp.is_valid and not shp.is_empty:
                            shapes.append(shp)
                    except Exception:
                        pass

            if shapes:
                union          = unary_union(shapes)
                union_mask     = union.buffer(0.0001)
                prepared_union = prep(union_mask)
                flat_lons      = lon_g.ravel()
                flat_lats      = lat_g.ravel()
                inside = np.array([
                    prepared_union.contains(Point(flon, flat))
                    for flon, flat in zip(flat_lons, flat_lats)
                ])
                mask = inside.reshape(GRID, GRID)
            else:
                mask[:] = True

        except ImportError:
            mask[:] = True
    else:
        mask[:] = True

    # ── Aplicar máscara ──
    Z_masked = np.where(mask, Z, np.nan)

    z_valid = Z_masked[~np.isnan(Z_masked)]
    if len(z_valid) == 0:
        return {"fills": [], "lines": [], "opacidad": opacidad_fill}

    z_max_grilla = float(z_valid.max())

    # ── REESCALAR: el pico de la grilla debe igualar el máximo real de capturas ──
    # El gaussiano diluye los picos (ej: 3 capturas → grilla llega a 1.8)
    # Reescalamos proporcionalmente para que el pico vuelva al valor real
    if z_max_grilla > 0:
        Z_masked = Z_masked * (z_max_real / z_max_grilla)

    z_valid = Z_masked[~np.isnan(Z_masked)]
    z_min   = float(z_valid.min())

    # ── Semáforo fijo: verde=0, amarillo=1, naranja=2, rojo=3+ ──
    vmin = 0
    vmax = max(3.0, z_max_real)  # mínimo 3, sube si hay capturas muy altas

    colors_semaforo = [
        (0 / vmax, "#00FF00"),  # verde    → 0
        (1 / vmax, "#FFFF00"),  # amarillo → 1
        (2 / vmax, "#FFA500"),  # naranja  → 2
        (3 / vmax, "#FF0000"),  # rojo     → 3+
        (1.0,      "#FF0000"),  # rojo     → hasta el máximo
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "semaforo", colors_semaforo, N=256
    )

    # ── Líneas de contour en valores enteros del semáforo ──
    levels_lines_base = [l for l in [1, 2, 3] if z_min < l < z_max_real]

    if num_niveles > 3:
        extra = np.linspace(z_min, z_max_real, num_niveles + 1)[1:-1]
        levels_lines_extra = [
            float(l) for l in extra
            if l not in levels_lines_base
            and z_min < l < z_max_real
        ]
        levels_lines = sorted(set(levels_lines_base + levels_lines_extra))
    else:
        levels_lines = levels_lines_base

    fig, ax = plt.subplots()

    N_bands     = 256
    levels_cont = np.linspace(vmin, vmax, N_bands)

    cf = ax.contourf(
        lon_g, lat_g, Z_masked,
        levels=levels_cont,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax
    )

    cl = None
    if levels_lines:
        cl = ax.contour(
            lon_g, lat_g, Z_masked,
            levels=levels_lines,
            colors="black",
            linewidths=grosor_lineas
        )

    # ── Serializar rellenos ──
    fills = []
    for i in range(len(cf.allsegs)):
        level_val = levels_cont[i] if i < len(levels_cont) else 0
        rgba      = cmap((level_val - vmin) / max(vmax - vmin, 1))
        color_hex = mcolors.to_hex(rgba)
        segs      = cf.allsegs[i]
        for seg in segs:
            if len(seg) < 3:
                continue
            latlons = [[float(p[1]), float(p[0])] for p in seg]
            fills.append({"color": color_hex, "coords": latlons})

    # ── Serializar líneas ──
    lines = []
    if cl is not None:
        for i, level in enumerate(levels_lines):
            segs = cl.allsegs[i] if i < len(cl.allsegs) else []
            for seg in segs:
                if len(seg) < 2:
                    continue
                latlons = [[float(p[1]), float(p[0])] for p in seg]
                lines.append({
                    "level":  float(level),
                    "coords": latlons,
                    "grosor": grosor_lineas,
                })

    plt.close(fig)
    return {
        "fills":    fills,
        "lines":    lines,
        "opacidad": opacidad_fill,
    }
# ============================================================
# CARGAR KMZ LOCAL (MEJORADO - 4 NIVELES)
# ============================================================
@st.cache_data(show_spinner="Cargando polígonos KMZ…")
def load_kmz_local(kmz_path: str = "data/MODULOS_PRIZE_PAIJAN.kmz"):
    """
    Carga KMZ local y parsea polígonos con 4 niveles:
    FUNDO_AQ, MÓDULO, TURNO, LOTE
    Con DEBUG detallado para troubleshooting
    """
    import zipfile
    from pathlib import Path
    try:
        kmz_file = Path(kmz_path)
        if not kmz_file.exists():
            st.error(f"❌ KMZ no encontrado en:\n`{kmz_file.absolute()}`")
            st.info("💡 Verifica la ruta. Debes usar la ruta correcta del archivo KMZ.")
            return []

        with zipfile.ZipFile(kmz_file, 'r') as kmz:
            kml_files = [f for f in kmz.namelist() if f.lower().endswith('.kml')]
            if not kml_files:
                st.error("❌ No se encontró archivo .kml dentro del KMZ")
                st.info(f"📁 Archivos en el KMZ: {kmz.namelist()}")
                return []


            kml_content = kmz.read(kml_files[0])

            try:
                from lxml import etree
                parser = etree.XMLParser(recover=True, encoding='utf-8')
                root   = etree.fromstring(kml_content, parser=parser)

                nsmap = {
                    'kml': 'http://www.opengis.net/kml/2.2',
                    'gx':  'http://www.google.com/kml/ext/2.2'
                }

                polygons = []
                skipped  = {"sin_coords": 0, "sin_mod": 0, "sin_tur": 0, "total": 0}

                # ── BUSCAR CARPETAS DE MÓDULOS (IGNORAR POZOS) ──
                folders = root.xpath('.//kml:Folder', namespaces=nsmap)
                if not folders:
                    folders = root.xpath('.//*[local-name()="Folder"]')


                folder_names = []
                for folder in folders:
                    folder_name_xpath = folder.xpath('.//kml:name/text()', namespaces=nsmap)
                    if not folder_name_xpath:
                        folder_name_xpath = folder.xpath('.//*[local-name()="name"]/text()')
                    if folder_name_xpath:
                        folder_names.append(folder_name_xpath[0])


                target_folders = []
                for folder in folders:
                    folder_name_xpath = folder.xpath('.//kml:name/text()', namespaces=nsmap)
                    if not folder_name_xpath:
                        folder_name_xpath = folder.xpath('.//*[local-name()="name"]/text()')

                    if folder_name_xpath:
                        fname = folder_name_xpath[0].upper()
                        if ('AQ1' in fname or 'AQ2' in fname) and 'MODULO' in fname:
                            target_folders.append((folder, folder_name_xpath[0]))

                if not target_folders:
                    st.error("❌ No se encontraron carpetas 'AQ1 - MODULO' o 'AQ2 - MODULO'")
                    st.info("💡 Ignorando carpeta 'Pozos_Prize' (son pozos, no lotes)")
                    return []

                placemarks = []
                for target_folder, folder_name in target_folders:
                    folder_placemarks = target_folder.xpath('.//kml:Placemark', namespaces=nsmap)
                    if not folder_placemarks:
                        folder_placemarks = target_folder.xpath('.//*[local-name()="Placemark"]')
                    for pm in folder_placemarks:
                        placemarks.append((pm, folder_name))


                for idx, (pm, folder_name) in enumerate(placemarks[:3]):
                    name_el = pm.xpath('.//kml:name/text()', namespaces=nsmap)
                    if not name_el:
                        name_el = pm.xpath('.//*[local-name()="name"]/text()')
                    name_text = name_el[0] if name_el else "(sin nombre)"

                for idx, (placemark, folder_name) in enumerate(placemarks):
                    skipped["total"] += 1

                    # ── EXTRAER NOMBRE ──
                    name_xpath = placemark.xpath('.//kml:name/text()', namespaces=nsmap)
                    if not name_xpath:
                        name_xpath = placemark.xpath('.//*[local-name()="name"]/text()')
                    name = name_xpath[0].strip() if name_xpath else ""

                    # ── EXTRAER DESCRIPCIÓN ──
                    desc_xpath = placemark.xpath('.//kml:description/text()', namespaces=nsmap)
                    if not desc_xpath:
                        desc_xpath = placemark.xpath('.//*[local-name()="description"]/text()')
                    desc = desc_xpath[0].strip() if desc_xpath else ""

                    # ── EXTRAER FUNDO_AQ Y MOD_N DEL NOMBRE DE CARPETA ──
                    fundo_aq = None
                    mod_n    = None

                    fundo_match = re.search(r'(AQ\d+)', folder_name, re.IGNORECASE)
                    if fundo_match:
                        fundo_aq = fundo_match.group(1).upper()

                    mod_match = re.search(r'MODULO\s*0*(\d+)', folder_name, re.IGNORECASE)
                    if mod_match:
                        mod_n = int(mod_match.group(1))

                    if not fundo_aq or not mod_n:
                        skipped["sin_mod"] += 1
                        continue

                    if idx < 3 and desc:
                        desc_preview = desc[:500] if len(desc) > 500 else desc

                    # ── EXTRAER COORDENADAS ──
                    coords = []

                    coord_xpath = placemark.xpath('.//kml:Polygon//kml:coordinates/text()', namespaces=nsmap)
                    if not coord_xpath:
                        coord_xpath = placemark.xpath('.//*[local-name()="Polygon"]//*[local-name()="coordinates"]/text()')
                    if not coord_xpath:
                        coord_xpath = placemark.xpath('.//kml:LineString//kml:coordinates/text()', namespaces=nsmap)
                    if not coord_xpath:
                        coord_xpath = placemark.xpath('.//*[local-name()="LineString"]//*[local-name()="coordinates"]/text()')
                    if not coord_xpath:
                        coord_xpath = placemark.xpath('.//kml:Point//kml:coordinates/text()', namespaces=nsmap)
                    if not coord_xpath:
                        coord_xpath = placemark.xpath('.//*[local-name()="Point"]//*[local-name()="coordinates"]/text()')
                    if not coord_xpath:
                        coord_xpath = placemark.xpath('.//*[local-name()="coordinates"]/text()')

                    if coord_xpath:
                        coord_text = coord_xpath[0].strip()
                        for coord_tuple in coord_text.split():
                            if coord_tuple.strip():
                                parts = coord_tuple.split(',')
                                if len(parts) >= 2:
                                    try:
                                        lon, lat = float(parts[0]), float(parts[1])
                                        coords.append([lat, lon])
                                    except (ValueError, IndexError):
                                        pass

                    is_point = False
                    if len(coords) == 1:
                        is_point = True
                    elif len(coords) < 3:
                        skipped["sin_coords"] += 1
                        continue

                    # ── EXTRAER TURNO ──
                    tur_n = None

                    tur_desc_match = re.search(
                        r'<td[^>]*>\s*Turno\s*</td>\s*<td[^>]*>\s*(\d+)',
                        desc, re.IGNORECASE
                    )
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
                        skipped["sin_tur"] += 1
                        continue

                    # ── EXTRAER LOTE ──
                    lote = None

                    lote_desc_match = re.search(
                        r'<td[^>]*>\s*Lote\s*</td>\s*<td[^>]*>\s*(\d+)',
                        desc, re.IGNORECASE
                    )
                    if lote_desc_match:
                        lote = norm_lote(lote_desc_match.group(1))

                    if not lote:
                        lote_match = re.search(r'[Ll]ote[:\s]*(\d+)', desc)
                        if lote_match:
                            lote = norm_lote(lote_match.group(1))

                    if not lote:
                        lote_match = re.search(r'(?:LOTE|LOT)[:\s]*([^\s,;<]+)', name, re.IGNORECASE)
                        if lote_match:
                            lote = norm_lote(lote_match.group(1))

                    if not lote:
                        lote = f"PZ_{name.replace(' ', '_').strip()}"

                    # ── AGREGAR POLÍGONO VÁLIDO ──
                    polygons.append({
                        "name":      name or f"Polígono {len(polygons)+1}",
                        "coords":    coords,
                        "mod_n":     mod_n,
                        "tur_n":     tur_n,
                        "fundo_aq":  fundo_aq,
                        "lote":      lote,
                        "lote_name": lote,
                    })

                if polygons:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Polígonos", len(polygons))
                    with col2:
                        st.metric("Módulos", len(set(p['mod_n'] for p in polygons)))
                    with col3:
                        st.metric("Turnos",  len(set(p['tur_n'] for p in polygons)))
                    with col4:
                        st.metric("Lotes",   len(set(p['lote']  for p in polygons)))

                  
                    return polygons
                else:
                    st.error("❌ No se encontraron polígonos válidos")
                    st.error(
                        f"📊 Rechazo: Sin coords: {skipped['sin_coords']}, "
                        f"Sin mod: {skipped['sin_mod']}, Sin tur: {skipped['sin_tur']}"
                    )
                    return []

            except ImportError:
                st.error("❌ lxml no instalado. Instala con: `pip install lxml`")
                return []

    except Exception as e:
        st.error(f"❌ Error cargando KMZ: {e}")
        import traceback
        st.error(f"📋 Traceback:\n```\n{traceback.format_exc()}\n```")
        return []


# ============================================================
# CARGAR DATOS EXCEL
# ============================================================
@st.cache_data(show_spinner="Cargando datos de trampas…", ttl=3600)
def load_trampas_anexadas() -> pd.DataFrame:
    import requests, io

    URL_AQUAI  = st.secrets.get("ONEDRIVE_URL_AQUAI",  "")
    URL_AQUAII = st.secrets.get("ONEDRIVE_URL_AQUAII", "")

    ARCHIVOS = {
        "AQI":  (URL_AQUAI,  "Bdatos"),
        "AQII": (URL_AQUAII, "BDatos AQU II"),
    }

    COLS = ["LATITUD", "LONGITUD", "FECHA", "FUNDO", "MODULO",
            "TURNO", "TRAMPA", "CAPTURAS", "LOTE", "EMPRESA",
            "SEMANA", "AÑO", "TIPO DE TRAMPA"]

    DTYPE_MAP = {
        "FUNDO": str, "MODULO": str, "TURNO": str, "TRAMPA": str,
        "LOTE": str,  "EMPRESA": str, "TIPO DE TRAMPA": str,
        "CAPTURAS": str, "SEMANA": str, "AÑO": str,
    }

    def _descargar(url: str) -> bytes | None:
        if not url:
            return None
        try:
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept":     "application/octet-stream",
            }
            resp = requests.get(
                url, headers=headers,
                timeout=60, allow_redirects=True
            )
            if resp.status_code == 200:
                # Verificar que sea un Excel real, no HTML de login
                content_type = resp.headers.get("Content-Type", "")
                if "html" in content_type:
                    st.sidebar.warning("⚠️ Link expirado o requiere login")
                    return None
                return resp.content
            st.sidebar.warning(f"⚠️ HTTP {resp.status_code}")
            return None
        except Exception as e:
            st.sidebar.warning(f"⚠️ Error descarga: {e}")
            return None

    # ── Intentar SharePoint público ──
    dfs = []
    for key, (url, sheet) in ARCHIVOS.items():
        contenido = _descargar(url)
        if contenido:
            try:
                df_tmp = pd.read_excel(
                    io.BytesIO(contenido),
                    sheet_name=sheet,
                    engine="openpyxl",
                    usecols=lambda c: c in COLS,
                    dtype=DTYPE_MAP,
                )
                dfs.append(df_tmp)
                st.sidebar.caption(f"✅ {key}: {len(df_tmp)} filas")
            except Exception as e:
                st.sidebar.warning(f"⚠️ Error leyendo Excel {key}: {e}")
        else:
            st.sidebar.warning(f"⚠️ {key}: descarga fallida")

    # ── Fallback local ──
    if not dfs:
        st.sidebar.info("📂 SharePoint no disponible — archivos locales")
        path_aquai  = r"C:\Users\lperez.LPEREZPRUEBA\operaciones_control\OPERACIONES\PRODUCCION_MOSCA\data\BD_Mosca_Fruta_AQUAI.xlsx"
        path_aquaii = r"C:\Users\lperez.LPEREZPRUEBA\operaciones_control\OPERACIONES\PRODUCCION_MOSCA\data\BD_Mosca_Fruta_AQUAII.xlsx"
        try:
            dfs.append(pd.read_excel(path_aquai,  sheet_name="Bdatos",        engine="openpyxl", usecols=lambda c: c in COLS, dtype=DTYPE_MAP))
            dfs.append(pd.read_excel(path_aquaii, sheet_name="BDatos AQU II", engine="openpyxl", usecols=lambda c: c in COLS, dtype=DTYPE_MAP))
            st.sidebar.caption("📂 Archivos locales OK")
        except FileNotFoundError:
            st.error("❌ Sin datos disponibles")
            return pd.DataFrame()

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

    return df
# ============================================================
# SIDEBAR - FILTROS COMPLETOS
# ============================================================
st.sidebar.header("⚙️ Configuración")

if st.sidebar.button("🔄 Limpiar todos los filtros", use_container_width=True):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

st.sidebar.markdown("---")

# ============================================================
# CARGAR DATOS EXCEL Y KMZ
# ============================================================
df = load_trampas_anexadas()

# DESPUÉS:
@st.cache_data(show_spinner="Descargando KMZ desde GitHub…")
def download_kmz_from_github() -> bytes | None:
    import urllib.request, urllib.error
    token   = st.secrets.get("GITHUB_TOKEN_KMZ", "")  # ← token específico KMZ
    api_url = (
        "https://api.github.com/repos/"
        "controloperacionalprize-boss/CAMPO_RENDIMIENTO/"
        "contents/MODULOS_PRIZE_PAIJAN.kmz"
    )
    headers = {"Accept": "application/vnd.github.v3.raw"}
    if token:
        headers["Authorization"] = f"token {token}"

    st.sidebar.caption(f"🔑 KMZ Token: {'✅' if token else '❌ vacío'}")

    try:
        req  = urllib.request.Request(api_url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        st.sidebar.caption(f"✅ KMZ: {len(data):,} bytes")
        return data
    except urllib.error.HTTPError as e:
        st.sidebar.error(f"❌ KMZ HTTP {e.code}: {e.reason}")
        return None
    except Exception as ex:
        st.sidebar.error(f"❌ KMZ Error: {ex}")
        return None
# ── Cargar KMZ: primero GitHub, fallback local ──
_kmz_bytes = download_kmz_from_github()

if _kmz_bytes:
    import tempfile, os
    _tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".kmz")
    _tmp.write(_kmz_bytes)
    _tmp.close()
    kmz_polygons = load_kmz_local(_tmp.name)
    os.unlink(_tmp.name)
else:
    st.sidebar.error("❌ KMZ no disponible")
    kmz_polygons = []
# ── FILTROS ENCADENADOS ──
with st.sidebar.expander("🔍 Filtros de datos", expanded=True):

    df_f = df.copy()


    semanas_opts = sorted(df_f["semana"].dropna().unique().astype(int).tolist())
    sel_semana   = st.multiselect("Semana", options=semanas_opts, default=[])
    df_f = df_f[df_f["semana"].isin(sel_semana)].copy() if sel_semana else df_f

    fundos_opts = sorted(df_f["fundo"].dropna().unique().tolist())
    sel_fundo   = st.multiselect("Fundo", options=fundos_opts, default=[])
    df_f = df_f[df_f["fundo"].isin(sel_fundo)].copy() if sel_fundo else df_f

    mods_opts = sorted(df_f["modulo"].dropna().unique().tolist())
    sel_mod   = st.multiselect("Módulo", options=mods_opts, default=[])
    df_f = df_f[df_f["modulo"].isin(sel_mod)].copy() if sel_mod else df_f

    lotes_opts = sorted(df_f["lote"].dropna().unique().tolist()) if "lote" in df_f.columns else []
    sel_lote   = st.multiselect("Lote", options=lotes_opts, default=[])
    df_f = df_f[df_f["lote"].isin(sel_lote)].copy() if sel_lote else df_f

    turnos_opts = sorted(df_f["turno"].dropna().unique().tolist())
    sel_turno   = st.multiselect("Turno", options=turnos_opts, default=[])
    df_f = df_f[df_f["turno"].isin(sel_turno)].copy() if sel_turno else df_f

    trampas_opts = sorted(df_f["trampa"].dropna().unique().tolist())
    sel_trampa   = st.multiselect("Trampa", options=trampas_opts, default=[])
    df_f = df_f[df_f["trampa"].isin(sel_trampa)].copy() if sel_trampa else df_f

    # ── RANGO DE FECHAS ──
    if "fecha" in df_f.columns and not df_f.empty:
        min_f = df_f["fecha"].min()
        max_f = df_f["fecha"].max()
        sel_fecha = st.date_input(
            "Rango de fechas", value=(min_f, max_f),
            min_value=min_f, max_value=max_f
        )
        f_ini, f_fin = (
            (sel_fecha[0], sel_fecha[1])
            if isinstance(sel_fecha, tuple)
            else (sel_fecha, sel_fecha)
        )
        dff = df_f[(df_f["fecha"] >= f_ini) & (df_f["fecha"] <= f_fin)].copy()
    else:
        dff = df_f.copy()

# ── FILTRO SEMAFORIZACIÓN ──
st.sidebar.markdown("**Filtro por semaforización:**")
sel_sem_verde    = st.sidebar.checkbox("🟢 0 capturas",   value=True, key="sem_verde")
sel_sem_amarillo = st.sidebar.checkbox("🟡 1 captura",    value=True, key="sem_amarillo")
sel_sem_naranja  = st.sidebar.checkbox("🟠 2 capturas",   value=True, key="sem_naranja")
sel_sem_rojo_f   = st.sidebar.checkbox("🔴 > 2 capturas", value=True, key="sem_rojo")

cats_permitidas = set()
if sel_sem_verde:    cats_permitidas.add(0)
if sel_sem_amarillo: cats_permitidas.add(1)
if sel_sem_naranja:  cats_permitidas.add(2)
if sel_sem_rojo_f:   cats_permitidas.add(3)

if not dff.empty:
    dff["_cat"] = dff["capturas"].apply(get_semaforo_category)
    dff = dff[dff["_cat"].isin(cats_permitidas)].drop(columns=["_cat"])

st.sidebar.markdown("---")

# ── MÉTODO INTERPOLACIÓN ──
metodo_interp = st.sidebar.radio(
    "🗺️ Método interpolación",
    options=["GPS (si existe)", "Lotes KMZ", "Híbrido (GPS + KMZ)"],
    index=1
)

st.sidebar.markdown("---")

# ── MODO VISUALIZACIÓN ──
modo_color = st.sidebar.radio(
    "🎨 Modo visualización",
    options=["Normal", "Espectral", "Curvas de Nivel"],
    index=0
)

# ── OPCIONES CURVAS NIVEL ──
num_niveles = grosor_lineas = opacidad_relleno = None
mostrar_etiquetas = False
if modo_color == "Curvas de Nivel":
    with st.sidebar.expander("⚙️ Opciones curvas", expanded=True):
        num_niveles       = st.slider("Número de líneas",  2, 20, 10)
        grosor_lineas     = st.slider("Grosor líneas",     1,  6,  3)
        mostrar_etiquetas = st.checkbox("Mostrar etiquetas", value=False)
        opacidad_relleno  = st.slider("Opacidad (%)",      0, 100, 65)

buffer_val = st.sidebar.slider("📏 Buffer trampas (°)", 0.001, 0.05, 0.010, step=0.001)

st.sidebar.markdown("---")

# ── VECTORES PROPAGACIÓN ──
with st.sidebar.expander("🧭 Vectores Propagación", expanded=False):
    mostrar_vectores = st.checkbox("Mostrar flechas", value=False, key="show_vectors")
    if mostrar_vectores:
        n_arrows      = st.slider("Densidad",                5, 30, 15)
        escala_flecha = st.slider("Longitud (×10⁻⁴ °)",     1, 20,  6)
        head_size     = st.slider("Tamaño punta (×10⁻⁵ °)", 1, 20,  6)
        min_mag       = st.slider("Magnitud mínima",         1, 30,  5) / 100.0
        color_flechas = st.color_picker("Color saetas", value="#1a1aff")

st.sidebar.markdown("---")
st.sidebar.info(f"📊 **Registros:** {len(dff)}")

# ── RESUMEN FILTROS ACTIVOS ──
filtros_activos = []
if sel_semana: filtros_activos.append(f"**Semana:** {', '.join(map(str, sel_semana))}")
if sel_fundo:  filtros_activos.append(f"**Fundo:** {', '.join(sel_fundo)}")
if sel_mod:    filtros_activos.append(f"**Módulo:** {', '.join(sel_mod)}")
if sel_lote:   filtros_activos.append(f"**Lote:** {', '.join(sel_lote)}")
if sel_turno:  filtros_activos.append(f"**Turno:** {', '.join(sel_turno)}")
if sel_trampa: filtros_activos.append(f"**Trampa:** {', '.join(sel_trampa)}")

if filtros_activos:
    st.sidebar.success("✅ Filtros activos")
    with st.sidebar.expander("📋 Ver filtros"):
        for fa in filtros_activos:
            st.markdown(fa)

# ============================================================
# PREPARAR DATOS PARA JAVASCRIPT
# ============================================================
map_data           = []
lotes_para_contorno = []
lotes_markers      = []
polygons_con_datos = []
lotes_etiquetas    = []

if not dff.empty:
    from collections import defaultdict

    dff_agg = dff.copy()
    dff_agg["lote"]     = dff_agg["lote"].fillna("").astype(str).str.strip()
    dff_agg["lat_orig"] = dff_agg["lat"].copy()
    dff_agg["lon_orig"] = dff_agg["lon"].copy()
    dff_agg["lat"]      = dff_agg["lat"].fillna(-9999.0)
    dff_agg["lon"]      = dff_agg["lon"].fillna(-9999.0)

    dff_agg["modulo_n"] = dff_agg["modulo"].apply(lambda x: norm_mod(str(x)))
    dff_agg["turno_n"]  = dff_agg["turno"].apply(lambda x: norm_tur(str(x)))
    dff_agg["lote_n"]   = dff_agg["lote"].apply(lambda x: norm_lote(str(x)))

    dff_agg = (
        dff_agg
        .groupby(["fundo", "modulo_n", "turno_n", "lote_n"], as_index=False)
        .agg({
            "capturas":    "sum",
            "trampa":      "first",
            "tipo_trampa": "first",
            "lat":         "first",
            "lon":         "first",
            "modulo":      "first",
            "turno":       "first",
            "lote":        "first",
        })
    )

    map_data      = dff_agg.to_dict("records")
    valid         = dff_agg.copy()
    lotes_markers = calcular_lotes_con_centroide(valid, kmz_polygons)

    # ── Agrupar por turno para etiquetas ──
    turnos_agrupados = defaultdict(lambda: {
        "lats": [], "lons": [], "capturas": 0,
        "lotes": [], "fundo": "", "modulo": "", "turno": "",
        "con_kmz": False
    })

    for m in lotes_markers:
        key = f"{m['fundo_aq']}|{m['modulo_n']}|{m['turno_n']}"
        g = turnos_agrupados[key]
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

    for key, g in turnos_agrupados.items():
        if g["lats"]:
            lotes_etiquetas.append({
                "lat":      sum(g["lats"]) / len(g["lats"]),
                "lon":      sum(g["lons"]) / len(g["lons"]),
                "capturas": g["capturas"],
                "fundo":    g["fundo"],
                "modulo":   g["modulo"],
                "turno":    g["turno"],
                "lotes":    sorted(g["lotes"]),
                "n_lotes":  len(g["lotes"]),
                "con_kmz":  g["con_kmz"],
            })

    # ── Para el gaussiano: solo lotes con centroide KMZ ──
    lotes_para_contorno = [
        {"lat": m["lat"], "lon": m["lon"], "capturas": m["capturas"]}
        for m in lotes_markers
        if m.get("con_kmz")
    ]

    # ── Polígonos KMZ que SÍ tienen datos ──
    keys_con_datos     = set(m["key_kmz"] for m in lotes_markers if m.get("con_kmz"))
    polygons_con_datos = []
    for poly in kmz_polygons:
        fundo_aq = str(poly.get("fundo_aq", "")).upper().strip()
        mod_n    = poly.get("mod_n")
        tur_n    = poly.get("tur_n")
        lote_n   = norm_lote(str(poly.get("lote_name", "")))
        key      = f"{fundo_aq}|{mod_n}|{tur_n}|{lote_n}"
        if key in keys_con_datos:
            polygons_con_datos.append(poly)

# ── Generar contornos gaussianos solo si el modo lo requiere ──
contornos = {"fills": [], "lines": [], "opacidad": (opacidad_relleno or 65) / 100.0}
if modo_color in ["Curvas de Nivel", "Espectral"] and len(lotes_para_contorno) >= 3:
    with st.spinner("Generando contornos gaussianos…"):
        contornos = generar_contornos_gauss(
            lotes_para_contorno,
            polygons_con_datos,
            num_niveles   = num_niveles   or 10,
            grosor_lineas = grosor_lineas or 3,
            opacidad_fill = (opacidad_relleno or 65) / 100.0,
        )
# ── Configuración visualización ──
viz_config = {
    "modo_color":        modo_color,
    "metodo_interp":     metodo_interp,
    "num_niveles":       num_niveles       or 10,
    "grosor_lineas":     grosor_lineas     or 3,
    "mostrar_etiquetas": mostrar_etiquetas,
    "opacidad_relleno":  opacidad_relleno  or 65,
    "buffer_val":        float(buffer_val),
    "mostrar_vectores":  mostrar_vectores  if "mostrar_vectores" in locals() else False,
    "n_arrows":          n_arrows          if "n_arrows"          in locals() else 15,
    "escala_flecha":     escala_flecha     if "escala_flecha"     in locals() else 6,
    "head_size":         head_size         if "head_size"         in locals() else 6,
    "min_mag":           min_mag           if "min_mag"           in locals() else 0.05,
    "color_flechas":     color_flechas     if "color_flechas"     in locals() else "#1a1aff",
    "semaforización": {
        "verde":    sel_sem_verde,
        "amarillo": sel_sem_amarillo,
        "naranja":  sel_sem_naranja,
        "rojo":     sel_sem_rojo_f,
    }
}

# ── Serializar desde lotes_markers (ya tienen centroide KMZ) ──
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
        "con_kmz":  m.get("con_kmz", False),  # ← único cambio

    }
    for m in lotes_markers
] if lotes_markers else []

# ── JSON final (SIN token para no exponerlo en el HTML) ──
data_json = json.dumps({
    "data":        map_data_optimized,
    "config":      viz_config,
    "recordCount": len(dff),
    "polygons":    kmz_polygons,
    "githubToken": "",   # ← vacío, el token se usa solo en Python
    "contornos":   contornos,
    "lotes":       lotes_etiquetas,
}, separators=(",", ":"), ensure_ascii=False)

# ============================================================
# MAPA CON COMPONENTE JAVASCRIPT
# ============================================================
st.markdown("## 🗺️ Mapa Epidemiológico")

html_file = Path(__file__).parent / "mapa_streamlit_js.html"

if html_file.exists():
    with open(html_file, "r", encoding="utf-8") as f:
        html_content = f.read()
else:
    html_content = """
    <div style="display:flex;align-items:center;justify-content:center;height:950px;
                background:#f5f5f5;color:#666;">
        <div>⚠️ Archivo mapa_streamlit_js.html no encontrado.
             Colócalo en la misma carpeta que este script.</div>
    </div>
    """

html_with_data = f"""
<script>
    window.streamlitData = {data_json};
</script>
{html_content}
"""

st.components.v1.html(html_with_data, height=950, width=None)

# ============================================================
# ============================================================
# ============================================================
# EXPORTAR HTML → GITHUB PAGES
# ============================================================
GITHUB_TOKEN  = st.secrets.get("GITHUB_TOKEN",  "")
GITHUB_OWNER  = st.secrets.get("GITHUB_OWNER",  "controloperacionalprize-boss")
GITHUB_REPO   = st.secrets.get("GITHUB_REPO",   "mapa_html")
GITHUB_BRANCH = st.secrets.get("GITHUB_BRANCH", "main")
GITHUB_FILE   = "mapa_mosca.html"

def _push_file_github(api_url, contenido, branch, mensaje, headers, es_binario=False):
    import base64, urllib.request, urllib.error, json
    sha = None
    try:
        req  = urllib.request.Request(api_url + f"?ref={branch}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        sha  = data.get("sha")
        st.sidebar.caption(f"📌 SHA encontrado: {sha[:7] if sha else 'None'}")  # ← debug
    except urllib.error.HTTPError as e:
        if e.code == 404:
            st.sidebar.caption("📌 Archivo nuevo (no existe aún)")
        else:
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
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        sha_nuevo = result.get("content", {}).get("sha", "")
        st.sidebar.caption(f"✅ Subido OK, nuevo SHA: {sha_nuevo[:7] if sha_nuevo else '?'}")
        return True, ""
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        st.sidebar.caption(f"❌ PUT falló: {body}")
        return False, f"GitHub API error {e.code}: {body}"
    except Exception as ex:
        return False, f"Error: {ex}"

def _build_sufijo():
    import re as _re_fn
    _partes = ["A2026"]  # ← año fijo porque el filtro está hardcodeado en la carga
    if sel_semana:
        _partes.append("S" + "-".join(map(str, sorted(sel_semana))))
    sufijo = "_".join(_partes) if _partes else "SinFiltro"
    return _re_fn.sub(r'[^A-Za-z0-9_\-]', '', sufijo)[:60]

def _build_headers():
    return {
        "Authorization":        f"Bearer {GITHUB_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "Content-Type":         "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _subir_html_a_github(html_content):
    import datetime
    if not GITHUB_TOKEN:
        return False, "No se encontró GITHUB_TOKEN en secrets.toml"

    headers   = _build_headers()
    base_repo = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"
    sufijo    = _build_sufijo()
    ts        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    mensaje   = f"Mapa actualizado {ts} | {sufijo}"

    # ── Anti-caché: meta tags para que el browser no guarde versión vieja ──
    html_limpio = html_content.replace(
        "<head>",
        "<head>\n"
        '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\n'
        '<meta http-equiv="Pragma" content="no-cache">\n'
        '<meta http-equiv="Expires" content="0">\n'
    )

    # ── Archivo fijo (siempre el mismo nombre → sobreescribe) ──
    ok1, res1 = _push_file_github(
        f"{base_repo}/{GITHUB_FILE}",
        html_limpio, GITHUB_BRANCH, mensaje, headers
    )
    if not ok1:
        return False, f"Error subiendo archivo fijo: {res1}"

    # ── Histórico con semana en el nombre ──
    nombre_h = f"historico/mapa_{sufijo}.html"
    _push_file_github(
        f"{base_repo}/{nombre_h}",
        html_limpio, GITHUB_BRANCH, mensaje, headers
    )

    url_historico = f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/{nombre_h}"
    return True, url_historico


def _subir_png_a_github(png_bytes):
    import datetime
    if not GITHUB_TOKEN:
        return False, "No se encontró GITHUB_TOKEN en secrets.toml"

    headers   = _build_headers()
    base_repo = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"
    sufijo    = _build_sufijo()
    ts        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    mensaje   = f"PNG generado {ts} | {sufijo}"
    nombre    = f"historico_png/mapa_{sufijo}.png"

    ok, res = _push_file_github(
        f"{base_repo}/{nombre}",
        png_bytes, GITHUB_BRANCH, mensaje, headers, es_binario=True
    )
    if ok:
        return True, f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/{nombre}"
    return False, res


# ── Botones en sidebar ──
st.sidebar.markdown("---")
st.sidebar.markdown("### 🌐 Publicar")
col_pub, col_png = st.sidebar.columns([1, 1])

with col_pub:
    if st.button("🚀 Publicar HTML", use_container_width=True, key="btn_pub_html"):
        with st.spinner("Subiendo a GitHub Pages..."):
            ok, resultado = _subir_html_a_github(html_with_data)
        if ok:
            st.sidebar.success("✅ Publicado")
            st.sidebar.markdown(f"[🔗 Ver mapa]({resultado})", unsafe_allow_html=True)
        else:
            st.sidebar.error(resultado)
with col_png:
    if st.button("🖼️ PNG", use_container_width=True, key="btn_png"):
        with st.spinner("Capturando mapa..."):
            tmp_html = None
            try:
                from playwright.sync_api import sync_playwright
                from PIL import Image
                import io, tempfile, os

                # ── Instalar browsers si no están (primera vez en cloud) ──
                os.system("playwright install chromium")

                # ── Guardar HTML temporal ──
                tmp_html = tempfile.NamedTemporaryFile(
                    delete=False, suffix=".html", mode="w", encoding="utf-8"
                )
                tmp_html.write(html_with_data)
                tmp_html.close()

                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                            "--disable-web-security",
                            "--allow-file-access-from-files",
                        ]
                    )
                    page = browser.new_page(
                        viewport={"width": 1920, "height": 1080},
                        device_scale_factor=2,
                    )

                    # ── Cargar HTML ──
                    page.goto(f"file://{tmp_html.name}", wait_until="networkidle")
                    page.wait_for_timeout(3000)

                    # ── Activar modo PNG ──
                    try:
                        page.evaluate("activarModoPNGGeneral()")
                    except Exception:
                        pass

                    page.wait_for_timeout(2000)

                    # ── Fix específico para emojis 🪰 en Playwright headless ──
                    try:
                        page.evaluate("""
                            () => {
                                // 1. Detener animación flotar que bloquea render en headless
                                const style = document.createElement('style');
                                style.textContent = `
                                    * { 
                                        animation: none !important; 
                                        transition: none !important;
                                    }
                                    @keyframes flotar { 
                                        0%, 50%, 100% { transform: translateX(-50%) translateY(0px); }
                                    }
                                `;
                                document.head.appendChild(style);

                                // 2. Forzar tamaño visible de emojis mosca (en PNG mode son 7px — muy chico)
                                document.querySelectorAll('.leaflet-marker-icon div').forEach(el => {
                                    const txt = el.textContent || '';
                                    if (txt.includes('🪰')) {
                                        el.style.fontSize    = '20px';
                                        el.style.animation   = 'none';
                                        el.style.visibility  = 'visible';
                                        el.style.opacity     = '1';
                                        el.style.display     = 'block';
                                        el.style.transform   = 'none';
                                    }
                                });

                                // 3. Forzar visibilidad de todos los markers
                                document.querySelectorAll('.leaflet-marker-icon').forEach(el => {
                                    el.style.visibility = 'visible';
                                    el.style.opacity    = '1';
                                    el.style.display    = 'block';
                                });

                                // 4. Invalidar mapa para forzar redibujado
                                if (window.map) window.map.invalidateSize(true);
                            }
                        """)
                    except Exception:
                        pass

                    # ── Esperar tiles ──
                    try:
                        page.wait_for_selector(".leaflet-tile-loaded", timeout=15000)
                    except Exception:
                        pass

                    # ── Esperar markers ──
                    try:
                        page.wait_for_selector(".leaflet-marker-icon", timeout=10000)
                    except Exception:
                        pass

                    # ── Pausa final ──
                    page.wait_for_timeout(5000)

                    # ── Capturar ──
                    try:
                        map_el    = page.locator("#mapContainer")
                        png_bytes = map_el.screenshot()
                    except Exception:
                        png_bytes = page.screenshot(full_page=False)

                    browser.close()

                # ── Mostrar resolución ──
                img = Image.open(io.BytesIO(png_bytes))
                st.sidebar.caption(f"📐 {img.width}×{img.height}px")

                # ── Subir PNG a GitHub ──
                ok_png, res_png = _subir_png_a_github(png_bytes)
                if ok_png:
                    st.sidebar.success("✅ PNG guardado en GitHub")
                    st.sidebar.markdown(f"[🔗 Ver PNG]({res_png})", unsafe_allow_html=True)
                else:
                    st.sidebar.warning(f"PNG local OK, GitHub falló: {res_png}")

                # ── Descarga local ──
                st.sidebar.download_button(
                    label="⬇️ Descargar PNG",
                    data=png_bytes,
                    file_name=f"mapa_mosca_{_build_sufijo()}.png",
                    mime="image/png",
                    key="btn_dl_png"
                )

            except Exception as e:
                st.sidebar.error(f"Error generando PNG: {e}")
                import traceback
                st.sidebar.error(traceback.format_exc())
            finally:
                try:
                    if tmp_html:
                        os.unlink(tmp_html.name)
                except Exception:
                    pass