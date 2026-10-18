import io
import json
import xml.etree.ElementTree as ET
import zipfile
import folium
import requests

# CWA API 授權碼
CWA_API_KEY = "CWA-77A723AE-21B7-4AD9-A9DE-661D15AE3823"

# 1. 初始化 Folium 地圖
m = folium.Map(
    location=[23.8, 121.0],
    zoom_start=6,
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Esri World Street Map",
)

# 2. 建立圖層 (FeatureGroups) 供使用者自由開關
fg_ncdr = folium.FeatureGroup(name="1. 各國預測路徑 (NCDR)", show=True)
fg_cwa_route = folium.FeatureGroup(
    name="2. CWA 警報與暴風圈 (W-C0034-002)", show=True
)
fg_cwa_wsp = folium.FeatureGroup(
    name="3. CWA 暴風圈侵襲機率 (W-C0034-003)", show=True
)

ns = {"kml": "http://www.opengis.net/kml/2.2"}


# 輔助函式：解壓並解析 KMZ/KML 資料
def fetch_kml_from_kmz(url):
    try:
        res = requests.get(url, timeout=15)
        res.raise_for_status()
        # 判斷是否為 KMZ (ZIP 格式)
        if url.lower().endswith("kmz") or res.content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                kml_files = [
                    f for f in z.namelist() if f.lower().endswith(".kml")
                ]
                if kml_files:
                    return ET.fromstring(z.read(kml_files[0]))
        else:
            return ET.fromstring(res.content)
    except Exception as e:
        print(f"下載/解析失敗 ({url}): {e}")
        return None


# 輔助函式：解析 KML 幾何圖型 (LineString, Point, Polygon) 增強地圖呈現
def parse_and_add_kml_to_group(root_elem, target_group, default_color="red"):
    if root_elem is None:
        return

    placemarks = root_elem.findall(".//kml:Placemark", ns)
    for pm in placemarks:
        name_elem = pm.find("kml:name", ns)
        name = (
            name_elem.text
            if (name_elem is not None and name_elem.text)
            else "圖資物件"
        )

        desc_elem = pm.find("kml:description", ns)
        desc = (
            desc_elem.text
            if (desc_elem is not None and desc_elem.text)
            else ""
        )

        # (A) 線條 (LineString)
        line_coords = pm.find(".//kml:LineString/kml:coordinates", ns)
        if line_coords is not None and line_coords.text:
            raw = line_coords.text.strip().split()
            path = [
                [float(c.split(",")[1]), float(c.split(",")[0])]
                for c in raw
                if len(c.split(",")) >= 2
            ]
            if path:
                folium.PolyLine(
                    locations=path,
                    color=default_color,
                    weight=3,
                    opacity=0.8,
                    tooltip=name,
                    popup=folium.Popup(f"<b>{name}</b><br>{desc}", max_width=300),
                ).add_to(target_group)

        # (B) 點位 (Point)
        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is not None and point_coords.text:
            parts = point_coords.text.strip().split(",")
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=4,
                    color=default_color,
                    fill=True,
                    fill_color=default_color,
                    fill_opacity=0.8,
                    tooltip=name,
                    popup=folium.Popup(
                        f"<b>{name}</b><br>{desc}", max_width=300
                    ),
                ).add_to(target_group)

        # (C) 面域/暴風圈/機率區塊 (Polygon)
        poly_coords = pm.find(".//kml:Polygon//kml:coordinates", ns)
        if poly_coords is not None and poly_coords.text:
            raw = poly_coords.text.strip().split()
            poly_path = [
                [float(c.split(",")[1]), float(c.split(",")[0])]
                for c in raw
                if len(c.split(",")) >= 2
            ]
            if poly_path:
                folium.Polygon(
                    locations=poly_path,
                    color=default_color,
                    fill=True,
                    fill_color=default_color,
                    fill_opacity=0.25,
                    tooltip=name,
                    popup=folium.Popup(
                        f"<b>{name}</b><br>{desc}", max_width=300
                    ),
                ).add_to(target_group)


# --- [圖層 1] NCDR 各國颱風預測路徑 ---
print("正在讀取 NCDR 各國預測資料...")
ncdr_url = "https://satis.ncdr.nat.gov.tw/kml/multityroute.aspx"
ncdr_root = fetch_kml_from_kmz(ncdr_url)


def get_agency_style(name):
    if not name:
        return "cadetblue"
    n = str(name).upper()
    if "台灣" in name or "CWA" in n or "CWB" in n:
        return "red"
    if "日本" in name or "JMA" in n:
        return "blue"
    if "美國" in name or "JTWC" in n:
        return "green"
    if "韓國" in name or "KMA" in n:
        return "purple"
    if "中國" in name or "CMA" in n:
        return "orange"
    return "cadetblue"


if ncdr_root is not None:
    for pm in ncdr_root.findall(".//kml:Placemark", ns):
        name_elem = pm.find("kml:name", ns)
        name = (
            name_elem.text
            if (name_elem is not None and name_elem.text)
            else "颱風路徑"
        )
        color = get_agency_style(name)

        line_coords = pm.find(".//kml:LineString/kml:coordinates", ns)
        if line_coords is not None and line_coords.text:
            path = [
                [float(c.split(",")[1]), float(c.split(",")[0])]
                for c in line_coords.text.strip().split()
                if len(c.split(",")) >= 2
            ]
            if path:
                folium.PolyLine(
                    locations=path,
                    color=color,
                    weight=4,
                    opacity=0.85,
                    tooltip=f"<b>NCDR: {name}</b>",
                ).add_to(fg_ncdr)

        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is not None and point_coords.text:
            parts = point_coords.text.strip().split(",")
            if len(parts) >= 2:
                folium.CircleMarker(
                    location=[float(parts[1]), float(parts[0])],
                    radius=4,
                    color=color,
                    fill=True,
                    fill_opacity=0.8,
                    tooltip=f"NCDR: {name}",
                ).add_to(fg_ncdr)


# --- [圖層 2] CWA 颱風警報與暴風圈 (W-C0034-002) ---
print("正在讀取 CWA 颱風警報與暴風圈圖資 (W-C0034-002)...")
cwa_002_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-002?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
cwa_002_root = fetch_kml_from_kmz(cwa_002_url)
parse_and_add_kml_to_group(
    cwa_002_root, fg_cwa_route, default_color="darkred"
)


# --- [圖層 3] CWA 暴風圈侵襲機率 (W-C0034-003) ---
print("正在讀取 CWA 暴風圈侵襲機率圖資 (W-C0034-003)...")
cwa_003_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
cwa_003_root = fetch_kml_from_kmz(cwa_003_url)
parse_and_add_kml_to_group(cwa_003_root, fg_cwa_wsp, default_color="purple")


# --- [資訊卡] CWA 颱風警報 JSON 內文與警戒區域 (W-C0034-001) ---
print("正在讀取 CWA 警報即時文字與警戒縣市 (W-C0034-001)...")
cwa_001_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-001?Authorization={CWA_API_KEY}"
info_html = "<b>無發布即時颱風警報</b>"

try:
    res = requests.get(cwa_001_url, timeout=15)
    if res.status_code == 200:
        data = res.json()
        info_list = data.get("records", {}).get("info", [])
        if info_list:
            latest_info = info_list[0]
            headline = latest_info.get("headline", "颱風警報")
            eff_time = latest_info.get("effective", "")

            # 解析警戒區域
            areas = [
                a.get("areaDesc", "") for a in latest_info.get("area", [])
            ]
            area_str = "、".join(areas[:8]) + (
                "..." if len(areas) > 8 else ""
            )

            # 解析描述段落
            sections = (
                latest_info.get("description", {}).get("section", [])
            )
            desc_text = ""
            for sec in sections:
                desc_text += f"<b>[{sec.get('title')}]</b> {sec.get('value')}<br>"

            info_html = f"""
            <div style="max-height:220px; overflow-y:auto;">
                <h4 style="margin:0 0 5px 0; color:#d9534f;">{headline}</h4>
                <small>發布時間：{eff_time}</small><hr style="margin:5px 0;">
                <b>【警戒區域】</b>：{area_str if area_str else "無"}<br><br>
                {desc_text}
            </div>
            """
except Exception as e:
    print(f"CWA JSON 警報解析失敗: {e}")

# 將 CWA 警報資訊卡加入地圖右下角
cwa_card = f"""
<div style="position: fixed; bottom: 30px; right: 30px; width: 320px; 
     background-color: white; border:2px solid #d9534f; z-index:9999; font-size:13px;
     padding: 12px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.3); opacity: 0.95;">
    <b style="font-size:15px; color:#333;">中央氣象署 (CWA) 最新警報</b>
    <hr style="margin:5px 0 8px 0;">
    {info_html}
</div>
"""
m.get_root().html.add_child(folium.Element(cwa_card))

# 左下角圖例 (Legend)
legend_html = """
<div style="position: fixed; bottom: 30px; left: 30px; width: 170px; 
     background-color: white; border:2px solid gray; z-index:9999; font-size:13px;
     padding: 10px; border-radius: 6px; opacity: 0.95;">
  <b>各國與 CWA 圖例</b><br>
  <i class="fa fa-minus" style="color:red"></i> 台灣 (CWA/NCDR)<br>
  <i class="fa fa-minus" style="color:blue"></i> 日本 (JMA)<br>
  <i class="fa fa-minus" style="color:green"></i> 美國 (JTWC)<br>
  <i class="fa fa-minus" style="color:purple"></i> 韓國 (KMA)<br>
  <i class="fa fa-minus" style="color:orange"></i> 中國 (CMA)<br>
  <i class="fa fa-square" style="color:darkred; opacity:0.5;"></i> CWA 暴風圈/警戒區<br>
  <i class="fa fa-square" style="color:purple; opacity:0.5;"></i> CWA 侵襲機率區
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

# 將所有 FeatureGroup 加入地圖
fg_ncdr.add_to(m)
fg_cwa_route.add_to(m)
fg_cwa_wsp.add_to(m)

# 啟用右上角圖層切換選單 (LayerControl)
folium.LayerControl(position="topright", collapsed=False).add_to(m)

# 儲存為 index.html
output_file = "index.html"
m.save(output_file)
print(f"地圖已成功更新並整合 CWA Data，儲存為：{output_file}")