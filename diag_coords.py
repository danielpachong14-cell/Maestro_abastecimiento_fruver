import pandas as pd

df = pd.read_excel('Input/DB_Tiendas.xlsx')
df.columns = df.columns.str.strip()

results = []
for _, row in df.iterrows():
    bod = str(row.get('BODEGA', '')).strip()
    if not bod or bod == 'nan':
        continue
    nombre = str(row.get('NOMBRE DE TIENDA', '')).strip()

    coord_raw = str(row.get('COORDENADAS', '')).strip()
    lat, lon, source = None, None, 'sin datos'
    if coord_raw and coord_raw != 'nan':
        try:
            parts = coord_raw.split(',')
            clat = float(parts[0].strip())
            clon = float(parts[1].strip())
            if 0 <= clat <= 15 and -82 <= clon <= -65:
                lat, lon, source = clat, clon, 'COORDENADAS'
        except Exception:
            pass

    if lat is None:
        try:
            llat = float(str(row['LATITUD']).split(',')[0])
            llon = float(str(row['LONGITUD']).split(',')[0])
            if llat < -10 and 0 < llon < 15:
                llat, llon = llon, llat
            if llon > 60:
                llon = -llon
            if 0 <= llat <= 15 and -82 <= llon <= -65:
                lat, lon, source = llat, llon, 'LATITUD/LONGITUD'
        except Exception:
            pass

    url_col = 'UBICACION URL ( MAPS)' if 'UBICACION URL ( MAPS)' in df.columns else None
    for c in df.columns:
        if 'URL' in c.upper() and 'MAPS' in c.upper():
            url_col = c
            break
    url = str(row.get(url_col, '') if url_col else '').strip()
    if url == 'nan':
        url = ''

    gmaps = f'https://maps.google.com/?q={lat},{lon}' if lat else 'sin coords'
    results.append((bod, nombre, lat, lon, source, url[:80], gmaps))

print(f'Total tiendas: {len(results)}')
print()
header = f"{'BODEGA':8}  {'LAT':9}  {'LON':10}  {'FUENTE':15}  {'NOMBRE':35}  GMAPS_SCRIPT"
print(header)
print('-' * len(header))
for r in results:
    bod, nombre, lat, lon, source, url, gmaps = r
    lat_s = f'{lat:.5f}' if lat else 'N/A'
    lon_s = f'{lon:.5f}' if lon else 'N/A'
    print(f'{bod:8}  {lat_s:9}  {lon_s:10}  {source:15}  {nombre:35}  {gmaps}')

# Guardar como CSV para revision
out = []
for r in results:
    bod, nombre, lat, lon, source, url_excel, gmaps = r
    out.append({
        'BODEGA': bod,
        'NOMBRE': nombre,
        'LAT': lat,
        'LON': lon,
        'FUENTE': source,
        'GMAPS_SCRIPT': gmaps,
        'URL_EXCEL': url_excel,
    })
pd.DataFrame(out).to_csv('Output/diagnostico_coordenadas.csv', index=False, encoding='utf-8-sig')
print()
print('CSV guardado: Output/diagnostico_coordenadas.csv')
