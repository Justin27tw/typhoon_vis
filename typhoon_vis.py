import io
import json
import xml.etree.ElementTree as ET
import zipfile
import folium
import requests

# 中央氣象署 (CWA) API 授權碼
CWA_API_KEY = "CWA-77A723AE-21B7-4AD9-A9DE-661D15AE3823"

# 1. 初始化 Folium 互動地圖
m = folium.Map(
    location=[23.8, 121.0],
    zoom_start=6,
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Esri World Street Map",
)

# 2. 建立圖層 (FeatureGroups)
fg_ncdr = folium.FeatureGroup(name="1. 各國預測路徑 (NCDR)", show=True)
fg_cwa_route = folium.FeatureGroup(
    name="2. CWA 警報與暴風圈 (W-C0034-002)", show=True
)
fg_cwa_wsp = folium.FeatureGroup(
    name="3. CWA 暴風圈侵襲機率 (W-C0034-003)", show=True
)

ns = {"kml": "http://www.opengis.net/kml/2.2"}


# 輔助函式：下載並解析 KMZ / KML
def fetch_kml_from_kmz(url):
    try:
        res = requests.get(url, timeout=15)
        res.raise_for_status()
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
        print(f"下載/解析 KML 失敗 ({url}): {e}")
        return None


# 輔助函式：將 KML 幾何加入圖層
def parse_and_add_kml_to_group(root_elem, target_group, default_color="red"):
    if root_elem is None:
        return
    for pm in root_elem.findall(".//kml:Placemark", ns):
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

        # 線條
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
                    color=default_color,
                    weight=3,
                    opacity=0.8,
                    tooltip=name,
                    popup=folium.Popup(f"<b>{name}</b><br>{desc}", max_width=300),
                ).add_to(target_group)

        # 點位
        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is not None and point_coords.text:
            parts = point_coords.text.strip().split(",")
            if len(parts) >= 2:
                folium.CircleMarker(
                    location=[float(parts[1]), float(parts[0])],
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

        # 區域 (Polygon)
        poly_coords = pm.find(".//kml:Polygon//kml:coordinates", ns)
        if poly_coords is not None and poly_coords.text:
            poly_path = [
                [float(c.split(",")[1]), float(c.split(",")[0])]
                for c in poly_coords.text.strip().split()
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


# 輔助函式：取得 CWA OpenData 的 ProductURL 並獲取實體資料內容
def get_cwa_product_xml(api_url):
    try:
        res = requests.get(api_url, timeout=15)
        res.raise_for_status()
        product_url = None

        # 嘗試從 JSON 解析 ProductURL
        try:
            data = res.json()
            product_url = (
                data.get("cwaopendata", {})
                .get("Dataset", {})
                .get("Resource", {})
                .get("ProductURL")
            )
        except Exception:
            # 嘗試從 XML 解析 ProductURL
            root = ET.fromstring(res.content)
            for elem in root.iter():
                if elem.tag.endswith("ProductURL") and elem.text:
                    product_url = elem.text.strip()
                    break

        if product_url:
            xml_res = requests.get(product_url, timeout=15)
            xml_res.raise_for_status()
            return xml_res.content
        else:
            return res.content
    except Exception as e:
        print(f"取得 CWA Product XML 失敗 ({api_url}): {e}")
        return None


# 輔助函式：格式化風力與雨量預測 XML 為 HTML 表格
def parse_cwa_forecast_xml(xml_content, forecast_type="風力"):
    if not xml_content:
        return f"<p style='color:gray;'>目前無發布最新{forecast_type}預測資料</p>"
    try:
        root = ET.fromstring(xml_content)
        rows = []

        # 搜尋包含縣市名稱的節點
        for loc in root.iter():
            if loc.tag.endswith("location") or loc.tag.endswith("Location"):
                loc_name = ""
                infos = []
                for child in loc:
                    tag_name = child.tag.split("}")[-1]
                    txt = (child.text or "").strip()
                    if (
                        "location" in tag_name.lower()
                        or "name" in tag_name.lower()
                        or "county" in tag_name.lower()
                    ):
                        loc_name = txt
                    elif txt:
                        infos.append(f"<b>{tag_name}</b>: {txt}")

                if loc_name and infos:
                    rows.append(
                        f"<tr><td style='padding:4px; border-bottom:1px solid #eee; font-weight:bold; white-space:nowrap;'>{loc_name}</td>"
                        f"<td style='padding:4px; border-bottom:1px solid #eee;'>{' | '.join(infos)}</td></tr>"
                    )

        if rows:
            return f"<table style='width:100%; border-collapse:collapse; font-size:12px;'>{''.join(rows)}</table>"

        # 若無層級結構，改取關鍵文字輸出
        texts = [
            elem.text.strip()
            for elem in root.iter()
            if elem.text
            and len(elem.text.strip()) > 1
            and not elem.text.strip().startswith("http")
        ]
        if texts:
            return "<br>".join(texts[:20])

        return f"<p style='color:gray;'>非警報發布期間，暫無最新{forecast_type}資料。</p>"
    except Exception as e:
        return f"<p style='color:red;'>解析{forecast_type}預測失敗: {e}</p>"


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


# --- [圖層 2 & 3] CWA KMZ 圖資 ---
print("正在讀取 CWA 颱風警報與暴風圈 (W-C0034-002)...")
cwa_002_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-002?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
parse_and_add_kml_to_group(
    fetch_kml_from_kmz(cwa_002_url), fg_cwa_route, default_color="darkred"
)

print("正在讀取 CWA 暴風圈侵襲機率 (W-C0034-003)...")
cwa_003_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
parse_and_add_kml_to_group(
    fetch_kml_from_kmz(cwa_003_url), fg_cwa_wsp, default_color="purple"
)


# --- [分頁 1] CWA 颱風警報 (W-C0034-001) ---
print("正在讀取 CWA 警報即時文字 (W-C0034-001)...")
cwa_001_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-001?Authorization={CWA_API_KEY}"
alert_html = "<p style='color:gray;'>目前無發布即時颱風警報</p>"

try:
    res = requests.get(cwa_001_url, timeout=15)
    if res.status_code == 200:
        data = res.json()
        info_list = data.get("records", {}).get("info", [])
        if info_list:
            latest = info_list[0]
            headline = latest.get("headline", "颱風警報")
            eff_time = latest.get("effective", "")
            areas = [a.get("areaDesc", "") for a in latest.get("area", [])]
            area_str = "、".join(areas[:8]) + (
                "..." if len(areas) > 8 else ""
            )

            desc_text = ""
            for sec in latest.get("description", {}).get("section", []):
                desc_text += f"<b>[{sec.get('title')}]</b> {sec.get('value')}<br>"

            alert_html = f"""
            <h4 style="margin:0 0 5px 0; color:#d9534f;">{headline}</h4>
            <small>發布時間：{eff_time}</small><hr style="margin:5px 0;">
            <b>【警戒區域】</b>：{area_str if area_str else "無"}<br><br>
            {desc_text}
            """
except Exception as e:
    print(f"CWA JSON 警報解析失敗: {e}")


# --- [分頁 2] 各地區風力預測 (F-C0034-005) ---
print("正在讀取 CWA 風力預測 (F-C0034-005)...")
f_005_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-C0034-005?Authorization={CWA_API_KEY}&downloadType=WEB&format=JSON"
wind_xml_data = get_cwa_product_xml(f_005_url)
wind_html = parse_cwa_forecast_xml(wind_xml_data, forecast_type="風力")


# --- [分頁 3] 各地區總雨量預測 (F-C0034-007) ---
print("正在讀取 CWA 總雨量預測 (F-C0034-007)...")
f_007_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-C0034-007?Authorization={CWA_API_KEY}&downloadType=WEB&format=JSON"
rain_xml_data = get_cwa_product_xml(f_007_url)
rain_html = parse_cwa_forecast_xml(rain_xml_data, forecast_type="總雨量")


# --- 組裝右下角多分頁面板 (Tabs UI) ---
tab_panel_html = f"""
<div style="position: fixed; bottom: 30px; right: 30px; width: 360px; height: 320px; 
     background-color: white; border:2px solid #337ab7; z-index:9999; font-size:13px;
     padding: 10px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.3); opacity: 0.95;
     display: flex; flex-direction: column;">
    
    <b style="font-size:15px; color:#333; margin-bottom:6px;">中央氣象署 (CWA) 颱風預報總覽</b>
    
    <!-- 分頁按鈕 -->
    <div style="display: flex; border-bottom: 2px solid #337ab7; margin-bottom: 8px;">
        <button onclick="switchTab('tab-alert')" id="btn-alert" style="flex:1; padding:6px; border:none; background:#337ab7; color:white; font-weight:bold; cursor:pointer; border-radius:4px 4px 0 0;">颱風警報</button>
        <button onclick="switchTab('tab-wind')" id="btn-wind" style="flex:1; padding:6px; border:none; background:#eee; color:#333; font-weight:bold; cursor:pointer; border-radius:4px 4px 0 0;">風力預測</button>
        <button onclick="switchTab('tab-rain')" id="btn-rain" style="flex:1; padding:6px; border:none; background:#eee; color:#333; font-weight:bold; cursor:pointer; border-radius:4px 4px 0 0;">雨量預測</button>
    </div>

    <!-- 分頁內容區塊 -->
    <div id="tab-alert" class="tab-content" style="overflow-y:auto; flex:1;">{alert_html}</div>
    <div id="tab-wind" class="tab-content" style="display:none; overflow-y:auto; flex:1;">{wind_html}</div>
    <div id="tab-rain" class="tab-content" style="display:none; overflow-y:auto; flex:1;">{rain_html}</div>
</div>

<script>
function switchTab(tabId) {{
    document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
    document.getElementById(tabId).style.display = 'block';
    
    document.getElementById('btn-alert').style.background = (tabId === 'tab-alert') ? '#337ab7' : '#eee';
    document.getElementById('btn-alert').style.color = (tabId === 'tab-alert') ? 'white' : '#333';
    
    document.getElementById('btn-wind').style.background = (tabId === 'tab-wind') ? '#337ab7' : '#eee';
    document.getElementById('btn-wind').style.color = (tabId === 'tab-wind') ? 'white' : '#333';
    
    document.getElementById('btn-rain').style.background = (tabId === 'tab-rain') ? '#337ab7' : '#eee';
    document.getElementById('btn-rain').style.color = (tabId === 'tab-rain') ? 'white' : '#333';
}}
</script>
"""
m.get_root().html.add_child(folium.Element(tab_panel_html))

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

# 將 FeatureGroup 加入地圖
fg_ncdr.add_to(m)
fg_cwa_route.add_to(m)
fg_cwa_wsp.add_to(m)

# 啟用右上角圖層切換選單
folium.LayerControl(position="topright", collapsed=False).add_to(m)

# 儲存為 index.html
output_file = "index.html"
m.save(output_file)
print(f"視覺化地圖已成功更新，並包含最新風力與雨量預測！儲存為：{output_file}")