"""
Genera un mapa HTML de verificacion con todas las tiendas de DB_Tiendas.xlsx.
Solo marcadores, sin rutas. Para validar que las coordenadas del Excel son correctas.
"""
import pandas as pd
import folium

df = pd.read_excel('Input/DB_Tiendas.xlsx')
df.columns = df.columns.str.strip()

stores = []
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

    if lat is None:
        continue

    zona = str(row.get('Zona', '')).strip()
    stores.append({'bod': bod, 'nombre': nombre, 'lat': lat, 'lon': lon, 'zona': zona, 'source': source})

# Centro del mapa
lats = [s['lat'] for s in stores]
lons = [s['lon'] for s in stores]
center = [sum(lats) / len(lats), sum(lons) / len(lons)]

m = folium.Map(location=center, zoom_start=11, tiles='CartoDB positron')

# Color por zona
COLORS = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
    '#42d4f4', '#9a6324', '#469990', '#f032e6', '#808000', '#666666',
]
zonas = sorted({s['zona'] for s in stores})
zona_color = {z: COLORS[i % len(COLORS)] for i, z in enumerate(zonas)}

for s in stores:
    color = zona_color.get(s['zona'], '#888888')
    gmaps = f"https://maps.google.com/?q={s['lat']},{s['lon']}"
    popup_html = (
        f"<div style='font-family:sans-serif;font-size:13px;min-width:160px'>"
        f"<b>{s['nombre']}</b><br>"
        f"<span style='color:#555'>{s['bod']} | Zona {s['zona']}</span><br>"
        f"<span style='color:#999;font-size:11px'>Fuente: {s['source']}</span><br>"
        f"<span style='font-size:11px'>({s['lat']:.5f}, {s['lon']:.5f})</span><br>"
        f"<a href='{gmaps}' target='_blank' style='font-size:11px;color:#2980b9'>Ver en Google Maps</a>"
        f"</div>"
    )
    folium.CircleMarker(
        location=[s['lat'], s['lon']],
        radius=8,
        color='white',
        fill=True,
        fill_color=color,
        fill_opacity=0.9,
        weight=2,
        popup=folium.Popup(popup_html, max_width=220),
        tooltip=f"{s['bod']} — {s['nombre']}",
    ).add_to(m)

out = 'Output/Verificacion_Coordenadas.html'
m.save(out)
print(f'Mapa guardado: {out}')
print(f'Total tiendas: {len(stores)}')
print()
print('Abre el mapa y verifica que cada punto este en la ubicacion real de la tienda.')
print('Si un punto esta mal, el error esta en la columna COORDENADAS de DB_Tiendas.xlsx.')
