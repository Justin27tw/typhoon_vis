import xml.etree.ElementTree as ET
import folium
import requests

kml_url = "https://satis.ncdr.nat.gov.tw/kml/multityroute.aspx"
print("正在下載最新颱風路徑資料...")

try:
    response = requests.get(kml_url, timeout=15)
    response.raise_for_status()
    kml_content = response.content
except Exception as e:
    print(f"下載失敗: {e}")
    exit()

root = ET.fromstring(kml_content)
ns = {"kml": "http://www.opengis.net/kml/2.2"}


def get_agency_style(name):
    if not name:
        return "cadetblue", "其他機構"
    name_upper = str(name).upper()
    if "台灣" in name or "CWA" in name_upper or "CWB" in name_upper:
        return "red", "台灣 (CWA)"
    elif "日本" in name or "JMA" in name_upper:
        return "blue", "日本 (JMA)"
    elif "美國" in name or "JTWC" in name_upper:
        return "green", "美國 (JTWC)"
    elif "韓國" in name or "KMA" in name_upper:
        return "purple", "韓國 (KMA)"
    elif "中國" in name or "CMA" in name_upper:
        return "orange", "中國 (CMA)"
    elif "歐洲" in name or "ECMWF" in name_upper:
        return "darkblue", "歐洲 (ECMWF)"
    else:
        return "cadetblue", "其他機構"


m = folium.Map(
    location=[23.8, 121.0],
    zoom_start=6,
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Esri World Street Map",
)

placemarks = root.findall(".//kml:Placemark", ns)

for pm in placemarks:
    name_elem = pm.find("kml:name", ns)
    name = (
        name_elem.text
        if (name_elem is not None and name_elem.text)
        else "颱風路徑"
    )
    color, agency_label = get_agency_style(name)

    line_coords = pm.find(".//kml:LineString/kml:coordinates", ns)
    if line_coords is not None and line_coords.text:
        coords_raw = line_coords.text.strip().split()
        path = [
            [float(c.split(",")[1]), float(c.split(",")[0])]
            for c in coords_raw
            if len(c.split(",")) >= 2
        ]
        if path:
            folium.PolyLine(
                locations=path,
                color=color,
                weight=4,
                opacity=0.85,
                tooltip=f"<b>{name}</b>",
                popup=folium.Popup(f"<b>預測機構：</b>{name}", max_width=300),
            ).add_to(m)

    point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
    if point_coords is not None and point_coords.text:
        parts = point_coords.text.strip().split(",")
        if len(parts) >= 2:
            lon, lat = float(parts[0]), float(parts[1])
            desc_elem = pm.find("kml:description", ns)
            desc = (
                desc_elem.text
                if (desc_elem is not None and desc_elem.text)
                else "無詳細說明"
            )
            folium.CircleMarker(
                location=[lat, lon],
                radius=4,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                tooltip=name,
                popup=folium.Popup(
                    f"<b>{name}</b><br>{desc}", max_width=300
                ),
            ).add_to(m)

legend_html = """
<div style="position: fixed; bottom: 30px; left: 30px; width: 160px; height: 180px; 
     background-color: white; border:2px solid gray; z-index:9999; font-size:14px;
     padding: 10px; border-radius: 5px; opacity: 0.9;">
  <b>各國颱風預測圖例</b><br>
  <i class="fa fa-minus" style="color:red"></i> 台灣 (CWA)<br>
  <i class="fa fa-minus" style="color:blue"></i> 日本 (JMA)<br>
  <i class="fa fa-minus" style="color:green"></i> 美國 (JTWC)<br>
  <i class="fa fa-minus" style="color:purple"></i> 韓國 (KMA)<br>
  <i class="fa fa-minus" style="color:orange"></i> 中國 (CMA)<br>
  <i class="fa fa-minus" style="color:cadetblue"></i> 其他/歷史位置
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

# 輸出檔名改為 index.html
output_file = "index.html"
m.save(output_file)
print(f"地圖已更新為：{output_file}")