import io
import json
import xml.etree.ElementTree as ET
import zipfile
import folium
import requests
import datetime

# 中央氣象署 (CWA) API 授權碼
CWA_API_KEY = "CWA-77A723AE-21B7-4AD9-A9DE-661D15AE3823"

# 取得現在的更新時間 (轉換為台灣時間 UTC+8)
current_time_str = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

# 1. 初始化 Folium 互動地圖
m = folium.Map(
    location=[23.8, 121.0],
    zoom_start=6,
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Esri World Street Map",
)

# 2. 建立圖層 (FeatureGroups)
fg_cwa_wsp = folium.FeatureGroup(name="3. CWA 暴風圈侵襲機率 (W-C0034-003)", show=True)
fg_cwa_route = folium.FeatureGroup(name="2. CWA 警報與暴風圈 (W-C0034-002)", show=True)
fg_ncdr = folium.FeatureGroup(name="1. 各國預測路徑 (NCDR)", show=True)

ns = {"kml": "http://www.opengis.net/kml/2.2"}

# 輔助函式：解析經緯度字串
def parse_lat_lon(pos_str):
    if not pos_str:
        return None
    cleaned = pos_str.replace("N", "").replace("E", "").replace("°", "").strip()
    parts = [p.strip() for p in cleaned.replace(",", " ").split() if p.strip()]
    if len(parts) >= 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None
    return None

# 輔助函式：下載並解析 KMZ / KML
def fetch_kml_from_kmz(url):
    try:
        res = requests.get(url, timeout=15)
        res.raise_for_status()
        if url.lower().endswith("kmz") or res.content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                kml_files = [f for f in z.namelist() if f.lower().endswith(".kml")]
                if kml_files:
                    return ET.fromstring(z.read(kml_files[0]))
        else:
            return ET.fromstring(res.content)
    except Exception as e:
        print(f"下載/解析 KML 失敗 ({url}): {e}")
        return None

# 專門解析 CWA 警報、歷史路徑與暴風圈 (W-C0034-002)
def parse_and_add_cwa_002(root_elem, target_group):
    if root_elem is None: return
    for pm in root_elem.findall(".//kml:Placemark", ns):
        name_elem = pm.find("kml:name", ns)
        name = name_elem.text.strip() if (name_elem is not None and name_elem.text) else "圖資物件"
        desc_elem = pm.find("kml:description", ns)
        desc = desc_elem.text.strip() if (desc_elem is not None and desc_elem.text) else ""

        # 1. 面域 Polygon
        poly_coords = pm.find(".//kml:Polygon//kml:coordinates", ns)
        if poly_coords is not None and poly_coords.text:
            poly_path = [[float(c.split(",")[1]), float(c.split(",")[0])]
                         for c in poly_coords.text.strip().split() if len(c.split(",")) >= 2]
            if poly_path:
                if "暴風圈" in name:
                    folium.Polygon(locations=poly_path, color="#d9534f", weight=2, opacity=0.8,
                                   fill=True, fill_color="#d9534f", fill_opacity=0.25,
                                   tooltip=f"<b>{name}</b>",
                                   popup=folium.Popup(f"<b>{name}</b><br>{desc}", max_width=300)).add_to(target_group)
                else:
                    folium.Polygon(locations=poly_path, color="#d9534f", weight=1.5, opacity=0.6,
                                   fill=True, fill_color="#f0ad4e", fill_opacity=0.12,
                                   tooltip=f"<b>{name}</b>",
                                   popup=folium.Popup(f"<b>{name}</b><br>{desc}", max_width=300)).add_to(target_group)

        # 2. 線條 LineString
        line_coords = pm.find(".//kml:LineString/kml:coordinates", ns)
        if line_coords is not None and line_coords.text:
            path = [[float(c.split(",")[1]), float(c.split(",")[0])]
                    for c in line_coords.text.strip().split() if len(c.split(",")) >= 2]
            if path:
                if "過去" in name or "歷史" in name:
                    folium.PolyLine(locations=path, color="#d9534f", weight=3.5, opacity=0.9, dash_array="6, 6",
                                    tooltip="<b>🌀 颱風歷史路徑 (過去移動軌跡)</b>",
                                    popup=folium.Popup(f"<b>{name}</b><br>{desc}", max_width=300)).add_to(target_group)
                else:
                    folium.PolyLine(locations=path, color="#d9534f", weight=3, opacity=0.85,
                                    tooltip=f"<b>{name}</b>",
                                    popup=folium.Popup(f"<b>{name}</b><br>{desc}", max_width=300)).add_to(target_group)

        # 3. 點位 Point (圓點 + 文字標籤)
        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is not None and point_coords.text:
            parts = point_coords.text.strip().split(",")
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                pos_str = f"中心位置：北緯 {lat:.1f}°, 東經 {lon:.1f}°"
                
                folium.CircleMarker(location=[lat, lon], radius=4, color="#d9534f", fill=True,
                                    fill_color="#d9534f", fill_opacity=0.9, tooltip=f"{name} ({pos_str})",
                                    popup=folium.Popup(f"<b>{name}</b><br><b>{pos_str}</b><br>{desc}", max_width=300)).add_to(target_group)
                
                if "日" in name or "時" in name or "颱風" in name:
                    folium.map.Marker(
                        [lat, lon],
                        icon=folium.DivIcon(
                            icon_size=(0,0),
                            icon_anchor=(0,0),
                            html=f'<div style="background-color: white; border: 1px solid #555; border-radius: 4px; padding: 2px 4px; font-size: 11px; font-weight: bold; color: #333; white-space: nowrap; transform: translate(8px, -18px); box-shadow: 1px 1px 3px rgba(0,0,0,0.3);">{name}</div>'
                        )
                    ).add_to(target_group)

# 專門解析 CWA 暴風圈侵襲機率 (W-C0034-003)
def parse_and_add_cwa_003(root_elem, target_group):
    if root_elem is None: return
    for pm in root_elem.findall(".//kml:Placemark", ns):
        name_elem = pm.find("kml:name", ns)
        name = name_elem.text.strip() if (name_elem is not None and name_elem.text) else "機率範圍"
        desc_elem = pm.find("kml:description", ns)
        desc = desc_elem.text.strip() if (desc_elem is not None and desc_elem.text) else ""
        
        fill_op = 0.04
        if "100%" in name or "80%" in name: fill_op = 0.10
        elif "60%" in name or "40%" in name: fill_op = 0.06
        elif "20%" in name: fill_op = 0.03

        poly_coords = pm.find(".//kml:Polygon//kml:coordinates", ns)
        if poly_coords is not None and poly_coords.text:
            poly_path = [[float(c.split(",")[1]), float(c.split(",")[0])]
                         for c in poly_coords.text.strip().split() if len(c.split(",")) >= 2]
            if poly_path:
                folium.Polygon(locations=poly_path, color="purple", weight=1, opacity=0.35,
                               fill=True, fill_color="purple", fill_opacity=fill_op,
                               tooltip=f"<b>暴風圈侵襲機率：{name}</b>",
                               popup=folium.Popup(f"<b>侵襲機率：{name}</b><br>{desc}", max_width=300)).add_to(target_group)

# --- [圖層 1] NCDR 各國預測路徑 ---
print("正在讀取 NCDR 各國預測資料...")
ncdr_url = "https://satis.ncdr.nat.gov.tw/kml/multityroute.aspx"
ncdr_root = fetch_kml_from_kmz(ncdr_url)

def get_agency_style(name):
    if not name: return "cadetblue"
    n = str(name).upper()
    if "台灣" in name or "CWA" in n or "CWB" in n: return "red"
    if "日本" in name or "JMA" in n: return "blue"
    if "美國" in name or "JTWC" in n: return "green"
    if "韓國" in name or "KMA" in n: return "purple"
    if "中國" in name or "CMA" in n: return "orange"
    return "cadetblue"

if ncdr_root is not None:
    for pm in ncdr_root.findall(".//kml:Placemark", ns):
        name_elem = pm.find("kml:name", ns)
        name = name_elem.text if (name_elem is not None and name_elem.text) else "颱風路徑"
        color = get_agency_style(name)

        line_coords = pm.find(".//kml:LineString/kml:coordinates", ns)
        if line_coords is not None and line_coords.text:
            path = [[float(c.split(",")[1]), float(c.split(",")[0])]
                    for c in line_coords.text.strip().split() if len(c.split(",")) >= 2]
            if path:
                folium.PolyLine(locations=path, color=color, weight=4, opacity=1.0, tooltip=f"<b>NCDR: {name}</b>").add_to(fg_ncdr)

        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is not None and point_coords.text:
            parts = point_coords.text.strip().split(",")
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                folium.CircleMarker(location=[lat, lon], radius=5, color=color, fill=True,
                                    fill_color=color, fill_opacity=1.0,
                                    tooltip=f"NCDR: {name} (北緯 {lat:.1f}°, 東經 {lon:.1f}°)",
                                    popup=folium.Popup(f"<b>NCDR: {name}</b><br><b>位置：北緯 {lat:.1f}°, 東經 {lon:.1f}°</b>", max_width=300)).add_to(fg_ncdr)

# --- [圖層 2 & 3] CWA KMZ 圖資 ---
print("正在讀取 CWA 颱風警報與暴風圈 (W-C0034-002)...")
cwa_002_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-002?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
cwa_002_root = fetch_kml_from_kmz(cwa_002_url)
parse_and_add_cwa_002(cwa_002_root, fg_cwa_route)

print("正在讀取 CWA 暴風圈侵襲機率 (W-C0034-003)...")
cwa_003_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
parse_and_add_cwa_003(fetch_kml_from_kmz(cwa_003_url), fg_cwa_wsp)


# --- [API 擴充] 日本氣象廳 (JMA) 颱風 JSON API 抓取 ---
print("正在讀取 JMA (日本氣象廳) 颱風資料...")
def fetch_jma_info():
    jma_url = "https://www.jma.go.jp/bosai/typhoon/data/TC.json"
    try:
        res = requests.get(jma_url, timeout=10)
        if res.status_code == 404:
            return "<p style='color:green; padding: 10px; font-weight:bold;'>✅ 目前西太平洋無活躍颱風，日本氣象廳 (JMA) 暫無發布警報。</p>"
        res.raise_for_status()
        data = res.json()
        if not data:
            return "<p style='color:gray;'>目前日本氣象廳 (JMA) 無發布活躍的颱風資訊。</p>"
        
        html_output = "<ul style='padding-left: 20px;'>"
        for tc in data:
            name_en = tc.get('name', {}).get('romaji', '未知名稱')
            name_kana = tc.get('name', {}).get('kana', '')
            time_dt = tc.get('dateTime', '未知時間')
            html_output += f"<li style='margin-bottom:8px;'><b>{name_en} ({name_kana})</b><br><small style='color:#666;'>JMA 更新時間: {time_dt}</small></li>"
        html_output += "</ul><div style='font-size:11px; color:gray; margin-top:10px;'>* 日本氣象廳颱風預測路徑已整合顯示於地圖上(藍色線條)。上方為 JMA 官方 API 即時回傳之活躍颱風列表。</div>"
        return html_output
    except Exception as e:
        return f"<p style='color:red;'>無法取得 JMA 資訊 (錯誤: {e})</p>"

jma_html = fetch_jma_info()


# --- CWA 警報文字解析 ---
cwa_001_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-001?Authorization={CWA_API_KEY}"
alert_html = ""
try:
    res = requests.get(cwa_001_url, timeout=15)
    if res.status_code == 200:
        data = res.json()
        info_list = data.get("records", {}).get("info", [])
        if info_list:
            latest = info_list[0]
            
            areas = [a.get("areaDesc", "") for a in latest.get("area", [])]
            area_str = "、".join(areas) if areas else "無"
            
            desc_text = ""
            for sec in latest.get("description", {}).get("section", []):
                title = sec.get('title', '')
                value = sec.get('value', '')
                if title and value:
                    desc_text += f"<b style='color:#337ab7;'>[{title}]</b> {value}<br><br>"
            
            alert_html = f"""
            <h4 style="margin:0 0 5px 0; color:#d9534f;">{latest.get('headline', '颱風警報')}</h4>
            <small>CWA 發布時間：{latest.get('effective', '')}</small><hr style="margin:5px 0;">
            <div style="margin-bottom: 10px; font-size: 13px;">
                <b>【警戒區域】</b>：{area_str}
            </div>
            <div style="font-size: 12px; line-height: 1.6; background-color: #f9f9f9; padding: 10px; border-radius: 6px; border-left: 4px solid #d9534f; overflow-wrap: break-word;">
                {desc_text}
            </div>
            """
except Exception as e:
    print(f"解析 CWA 警報時發生錯誤：{e}")

if not alert_html:
    alert_html = """
    <h4 style="margin:0 0 5px 0; color:#0275d8;">即時颱風消息</h4>
    <p style="color:gray;">目前臺灣尚未發布颱風警報。請參考地圖上之各國預測路徑及切換 JMA 分頁查看日本最新動態。</p>
    """

# --- 介面面板 ---
tab_panel_html = f"""
<div style="position: fixed; bottom: 30px; right: 30px; width: 440px; height: 500px;
     background-color: white; border: 2px solid #337ab7; z-index: 9999; font-size: 13px;
     padding: 10px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.3); opacity: 0.96;
     display: flex; flex-direction: column; box-sizing: border-box;">

    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px;">
        <b style="font-size: 15px; color: #333;">颱風即時動態與各國預測整合</b>
    </div>
    <div style="font-size: 11px; color: #666; margin-bottom: 8px; border-bottom: 1px solid #ccc; padding-bottom: 5px;">
        <i class="fa fa-clock"></i> 系統資料更新時間：<b>{current_time_str}</b>
    </div>

    <div style="display: flex; border-bottom: 2px solid #337ab7; margin-bottom: 8px; font-size: 12px; flex-wrap: wrap; gap: 4px; padding-bottom: 4px;">
        <button onclick="switchTab('tab-alert')" id="btn-alert"
                style="flex: 1 1 auto; padding: 6px; border: none; background: #337ab7; color: white; border-radius: 4px; cursor: pointer;">
            臺灣氣象署警報
        </button>
        <button onclick="switchTab('tab-jma')" id="btn-jma"
                style="flex: 1 1 auto; padding: 6px; border: none; background: #eee; color: #333; border-radius: 4px; cursor: pointer;">
            日本氣象廳 JMA
        </button>
    </div>

    <div id="tab-alert" style="display: block; flex: 1; min-height: 0; overflow-y: auto; line-height: 1.6;">
        {alert_html}
    </div>
    <div id="tab-jma" style="display: none; flex: 1; min-height: 0; overflow-y: auto; line-height: 1.6;">
        {jma_html}
    </div>
</div>

<script>
function switchTab(tabId) {{
    const tabIds = ['tab-alert', 'tab-jma'];
    tabIds.forEach(function(id) {{
        const panel = document.getElementById(id);
        const button = document.getElementById(id === 'tab-alert' ? 'btn-alert' : 'btn-jma');
        const active = id === tabId;
        if (panel) panel.style.display = active ? 'block' : 'none';
        if (button) {{
            button.style.background = active ? '#337ab7' : '#eee';
            button.style.color = active ? 'white' : '#333';
        }}
    }});
}}
</script>
"""

# 將資訊面板加入 Folium 地圖，並顯示圖層切換控制。
m.get_root().html.add_child(folium.Element(tab_panel_html))
folium.LayerControl(collapsed=False).add_to(m)

output_file = "typhoon_vis.html"
m.save(output_file)
print(f"地圖已輸出：{output_file}")
