from collections import defaultdict
import io
import os
import json
import xml.etree.ElementTree as ET
import zipfile
import folium
import shutil  # 用於自動複製與備份各颱風/熱帶性低氣壓專屬 HTML
import requests
import datetime
import html
import re
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import dateutil.parser


# 從環境變數讀取 CWA API 金鑰，若未設定則提供預設值(僅供本地測試)
CWA_API_KEY = os.environ.get("CWA_API_KEY")
INDEX_FILE = "typhoons_index.json"


def sanitize_eng_name(eng_name, cwa_td_no="-", cwa_ty_no="-"):
    """
    清理英文名稱，只保留英數字與連字號，例如 'IN-FA'。
    若為尚未命名的熱帶性低氣壓 (無英文名或為 UNKNOWN)，
    自動以熱帶氣旋編號命名 (如 'TD-35')，避免不同熱帶性低氣壓互相覆蓋。
    """
    cleaned = ""
    if eng_name and str(eng_name).strip() not in ("未命名", "UNKNOWN", "-"):
        cleaned = re.sub(r'[^A-Za-z0-9\-]', '', str(eng_name).upper())

    if not cleaned or cleaned == "UNKNOWN":
        if cwa_ty_no and str(cwa_ty_no).strip() not in ("", "-"):
            safe_no = re.sub(r'[^A-Za-z0-9]', '', str(cwa_ty_no))
            return f"TY-{safe_no}"
        if cwa_td_no and str(cwa_td_no).strip() not in ("", "-"):
            safe_no = re.sub(r'[^A-Za-z0-9]', '', str(cwa_td_no))
            return f"TD-{safe_no}"
        return "UNKNOWN"
    return cleaned


def update_typhoon_registry(active_typhoons, current_time_str):
    """
    管理 typhoons_index.json 生命週期：
    1. 當熱帶性低氣壓生成時，自動建立索引與專屬分頁檔名 (如 typhoonvis_TD-35.html)。
    2. 若該熱帶性低氣壓後續增強為颱風並獲得國際命名，自動將舊分頁更名並無縫銜接紀錄。
    3. 活躍期間持續標記為 'active'；當颱風結束並從 API 消失後，自動改為 'historical' 停止更新。
    """
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = {}
    else:
        registry = {
            "CHAMPI": {
                "cwa_no": "29",
                "td_no": "",
                "zh_name": "薔琵",
                "eng_name": "CHAMPI",
                "filename": "typhoonvis_CHAMPI.html",
                "last_updated": "2026-10-23",
                "status": "historical"
            }
        }

    # 將既有紀錄中的舊版 "UNKNOWN" 自動遷移為具體的 TD 編號 (如 TD-35)
    if "UNKNOWN" in registry:
        old_unk = registry.pop("UNKNOWN")
        unk_no = str(old_unk.get("cwa_no", "")).strip()
        new_key = f"TD-{unk_no}" if (unk_no and unk_no != "-") else "UNKNOWN"
        new_fname = f"typhoonvis_{new_key}.html"
        old_fname = old_unk.get("filename", "typhoonvis_UNKNOWN.html")
        if os.path.exists(old_fname) and old_fname != new_fname and not os.path.exists(new_fname):
            try:
                shutil.move(old_fname, new_fname)
            except Exception as e:
                print(f"遷移舊檔案 {old_fname} 失敗: {e}")
        old_unk["eng_name"] = new_key
        old_unk["zh_name"] = old_unk.get("zh_name") or "熱帶性低氣壓"
        old_unk["filename"] = new_fname
        old_unk.setdefault("td_no", unk_no)
        if new_key not in registry:
            registry[new_key] = old_unk

    # 先將所有既有紀錄標記為歷史狀態 (historical)
    # 若氣旋已消散不在本次 active_typhoons 清單中，就會維持 historical 並停止覆寫 HTML
    for key in registry:
        registry[key]["status"] = "historical"

    # 更新或新增當前活躍的熱帶氣旋
    for ty in active_typhoons:
        clean_eng = ty["eng_name"]
        cwa_no = str(ty.get("cwa_no", "")).strip()
        td_no = str(ty.get("td_no", "")).strip()
        filename = f"typhoonvis_{clean_eng}.html"

        # 檢查是否為原本已存在的熱帶性低氣壓 (TD-XX) 升格並獲得正式英文命名
        old_td_key = None
        for k, v in list(registry.items()):
            if k != clean_eng and k.startswith("TD-"):
                if (td_no and td_no != "-" and (v.get("td_no") == td_no or v.get("cwa_no") == td_no)) or \
                   (cwa_no and cwa_no != "-" and v.get("cwa_no") == cwa_no):
                    old_td_key = k
                    break

        if old_td_key:
            old_info = registry.pop(old_td_key)
            old_file = old_info.get("filename", "")
            if old_file and os.path.exists(old_file) and old_file != filename:
                try:
                    shutil.move(old_file, filename)
                    print(f"🔄 熱帶性低氣壓升格為颱風，已將分頁 {old_file} 自動更名為 {filename}")
                except Exception as e:
                    print(f"更名分頁檔案失敗: {e}")

        registry[clean_eng] = {
            "cwa_no": cwa_no,
            "td_no": td_no,
            "zh_name": ty.get("zh_name", clean_eng),
            "eng_name": clean_eng,
            "filename": filename,
            "last_updated": ty.get("last_time", current_time_str[:16]),
            "status": "active"
        }

    # 排序：活躍中 (active) 優先排最上方，其次依最後更新時間由新到舊排序
    sorted_registry = dict(
        sorted(
            registry.items(),
            key=lambda item: (
                0 if item[1].get("status") == "active" else 1,
                item[1].get("last_updated", "")
            ),
            reverse=False
        )
    )
    # 針對同狀態內部按時間倒序排列
    active_items = sorted(
        [(k, v) for k, v in registry.items() if v.get("status") == "active"],
        key=lambda item: item[1].get("last_updated", ""),
        reverse=True
    )
    historical_items = sorted(
        [(k, v) for k, v in registry.items() if v.get("status") != "active"],
        key=lambda item: item[1].get("last_updated", ""),
        reverse=True
    )
    sorted_registry = dict(active_items + historical_items)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_registry, f, ensure_ascii=False, indent=2)

    return sorted_registry


# 取得現在的更新時間 (轉換為台灣時間 UTC+8)
current_time_str = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
# 初始化 README 內容
readme_content = "## 網站：https://typhoonvis.netlify.app/\n\n"
readme_content += f"### 🌀 最新熱帶氣旋動態 (更新時間：{current_time_str})\n\n"

# 1. 初始化 Folium 互動地圖
m = folium.Map(
    location=[20.0, 130.0],
    zoom_start=5,
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attr="Esri World Street Map",
)

# 2. 建立圖層 (FeatureGroups)
fg_county = folium.FeatureGroup(name="5. 縣市邊界與陸上警戒區", show=True)
fg_cwa_wsp = folium.FeatureGroup(name="4. CWA 暴風圈侵襲機率 (W-C0034-003)", show=False)
fg_cwa_route = folium.FeatureGroup(name="3. CWA 警報與暴風圈 (W-C0034-002)", show=True)
fg_cwa_005 = folium.FeatureGroup(name="2. CWA 熱帶氣旋分析預報 (W-C0034-005)", show=True)
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
        res = requests.get(url, timeout=15, verify=False)
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
                folium.PolyLine(locations=path, color=color, weight=4, opacity=0.9, tooltip=f"<b>NCDR: {name}</b>").add_to(fg_ncdr)

        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is not None and point_coords.text:
            parts = point_coords.text.strip().split(",")
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                folium.CircleMarker(location=[lat, lon], radius=5, color=color, fill=True,
                                    fill_color=color, fill_opacity=1.0,
                                    tooltip=f"NCDR: {name} (北緯 {lat:.1f}°, 東經 {lon:.1f}°)",
                                    popup=folium.Popup(f"<b>NCDR: {name}</b><br><b>位置：北緯 {lat:.1f}°, 東經 {lon:.1f}°</b>", max_width=300)).add_to(fg_ncdr)

# --- [圖層 2] CWA W-C0034-005 熱帶氣旋詳細定位與預報 ---
print("正在讀取 CWA 西北太平洋及南海熱帶氣旋資料 (W-C0034-005)...")
cwa_005_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005?Authorization={CWA_API_KEY}"
cyclone_panel_html = ""

def extract_lang_text(item, preferred_lang="zh-hant"):
    if not item: return "無"
    if isinstance(item, list):
        for entry in item:
            if entry.get("@lang") == preferred_lang:
                return entry.get("#text", "無")
        return item[0].get("#text", "無")
    elif isinstance(item, dict):
        return item.get("#text", "無")
    return str(item)

def format_radius(circle_obj):
    if not circle_obj: return "無"
    if isinstance(circle_obj, dict):
        return f"{circle_obj.get('Radius', '未知')} 公里"
    return f"{circle_obj} 公里"

active_typhoons = []  # 收集當前活躍颱風與熱帶性低氣壓
all_chart_data = {}   # 準備存儲所有活動中氣旋的圖表資料
try:
    res_005 = requests.get(cwa_005_url, timeout=15, verify=False)
    if res_005.status_code == 200:
        data_005 = res_005.json()
        
        if "records" in data_005:
            cyclones = data_005["records"].get("TropicalCyclones", {}).get("TropicalCyclone", [])
        else:
            cyclones = data_005.get("cwaopendata", {}).get("Dataset", {}).get("TropicalCyclones", {}).get("TropicalCyclone", [])
            
        if isinstance(cyclones, dict):
            cyclones = [cyclones]

        panel_blocks = []
        
        for cyc in cyclones:
            name_en = cyc.get("TyphoonName", "未命名")
            name_zh = cyc.get("CwaTyphoonName", "")
            cwa_td_no = str(cyc.get("CwaTdNo", "-")).strip()
            cwa_ty_no = str(cyc.get("CwaTyNo", "-")).strip()
            display_no = cwa_ty_no if (cwa_ty_no and cwa_ty_no != "-") else cwa_td_no
            clean_eng = sanitize_eng_name(name_en, cwa_td_no, cwa_ty_no)

            if name_zh:
                title_name = f"{name_zh} ({clean_eng})"
            elif clean_eng.startswith("TD-"):
                title_name = f"熱帶性低氣壓 ({clean_eng})"
            else:
                title_name = clean_eng

            analysis_fixes = cyc.get("AnalysisData", {}).get("Fix", [])
            if isinstance(analysis_fixes, dict): analysis_fixes = [analysis_fixes]
            
            history_pts = []
            latest_fix = None

            # 建立圖表專用陣列
            c_labels = []
            c_winds = []
            c_pressures = []

            for fix in analysis_fixes:
                lat = float(fix.get("CoordinateLatitude"))
                lon = float(fix.get("CoordinateLongitude"))
                history_pts.append([lat, lon])
                latest_fix = fix
                # 收集時間、風速、氣壓資料
                raw_dt = fix.get("DateTime", "").replace("T", " ")
                try:
                    dt_obj = dateutil.parser.isoparse(fix.get("DateTime", ""))
                    fmt_dt = dt_obj.strftime("%m-%d %H:%M") # 格式化為 月-日 時:分
                except:
                    fmt_dt = raw_dt[:16]
                
                c_labels.append(fmt_dt)
                try: c_winds.append(float(fix.get('MaxWindSpeed', 0)))
                except: c_winds.append(0)
                try: c_pressures.append(float(fix.get('Pressure', 0)))
                except: c_pressures.append(0)
                dt = fix.get("DateTime", "").replace("T", " ")
                r15 = format_radius(fix.get("Circle15ms"))
                r25 = format_radius(fix.get("Circle25ms"))
                mv_pred = extract_lang_text(fix.get("MovingPrediction"))
                
                popup_content = f"""
                <div style='font-size:12px; line-height:1.5;'>
                    <b style='color:#b52b27; font-size:13px;'>🌀 {title_name} - 過去定位點</b><br>
                    <b>定位時間：</b>{dt}<br>
                    <b>中心位置：</b>北緯 {lat}° / 東經 {lon}°<br>
                    <b>中心氣壓：</b>{fix.get('Pressure', '-')} hPa<br>
                    <b>最大風速：</b>{fix.get('MaxWindSpeed', '-')} m/s (陣風: {fix.get('MaxGustSpeed', '-')} m/s)<br>
                    <b>移速/移向：</b>{fix.get('MovingSpeed', '-')} km/h，朝 {fix.get('MovingDirection', '-')} 方向<br>
                    <b>七級風暴風半徑：</b>{r15}<br>
                    <b>十級風暴風半徑：</b>{r25}<br>
                    <b>動態預測：</b>{mv_pred}
                </div>
                """
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=4,
                    color="#e65100",
                    fill=True,
                    fill_color="#ff9800",
                    fill_opacity=0.85,
                    tooltip=f"{title_name} 定位: {dt}",
                    popup=folium.Popup(popup_content, max_width=320)
                ).add_to(fg_cwa_005)
                
            all_chart_data[title_name] = {
                "labels": c_labels,
                "winds": c_winds,
                "pressures": c_pressures
            }

            if len(history_pts) > 1:
                folium.PolyLine(
                    locations=history_pts,
                    color="#e65100",
                    weight=3,
                    dash_array="5, 5",
                    opacity=0.9,
                    tooltip=f"<b>{title_name} 過去分析軌跡</b>"
                ).add_to(fg_cwa_005)

            forecast_fixes = cyc.get("ForecastData", {}).get("Fix", [])
            if isinstance(forecast_fixes, dict): forecast_fixes = [forecast_fixes]

            forecast_pts = []
            if latest_fix:
                forecast_pts.append([float(latest_fix.get("CoordinateLatitude")), float(latest_fix.get("CoordinateLongitude"))])

            for f_fix in forecast_fixes:
                f_lat = float(f_fix.get("CoordinateLatitude"))
                f_lon = float(f_fix.get("CoordinateLongitude"))
                forecast_pts.append([f_lat, f_lon])
                
                f_hour = f_fix.get("ForecastHour", "-")
                init_time = f_fix.get("InitialTime", "").replace("T", " ")
                r15 = format_radius(f_fix.get("Circle15ms"))
                r25 = format_radius(f_fix.get("Circle25ms"))
                prob_r = f_fix.get("Radius70PercentProbability", "無")
                state_trans = extract_lang_text(f_fix.get("StateTransfer"))

                f_popup = f"""
                <div style='font-size:12px; line-height:1.5;'>
                    <b style='color:#c2185b; font-size:13px;'>🔮 {title_name} - {f_hour}小時未來預報</b><br>
                    <b>起報時間：</b>{init_time}<br>
                    <b>預測位置：</b>北緯 {f_lat}° / 東經 {f_lon}°<br>
                    <b>中心氣壓：</b>{f_fix.get('Pressure', '-')} hPa<br>
                    <b>預估最大風速：</b>{f_fix.get('MaxWindSpeed', '-')} m/s (陣風: {f_fix.get('MaxGustSpeed', '-')} m/s)<br>
                    <b>預測移速/移向：</b>{f_fix.get('MovingSpeed', '-')} km/h，朝 {f_fix.get('MovingDirection', '-')} 方向<br>
                    <b>70%機率半徑：</b>{prob_r} 公里<br>
                    <b>七級/十級半徑：</b>{r15} / {r25}<br>
                    <b>未來狀態轉變：</b>{state_trans}
                </div>
                """
                folium.CircleMarker(
                    location=[f_lat, f_lon],
                    radius=5,
                    color="#c2185b",
                    fill=True,
                    fill_color="#ff4081",
                    fill_opacity=0.9,
                    tooltip=f"{title_name} +{f_hour}H預報 (北緯{f_lat}°, 東經{f_lon}°)",
                    popup=folium.Popup(f_popup, max_width=320)
                ).add_to(fg_cwa_005)

            if len(forecast_pts) > 1:
                folium.PolyLine(
                    locations=forecast_pts,
                    color="#c2185b",
                    weight=3.5,
                    opacity=0.9,
                    tooltip=f"<b>{title_name} CWA 官方預測路徑</b>"
                ).add_to(fg_cwa_005)

            if latest_fix:
                dt_str = latest_fix.get("DateTime", "").replace("T", " ")

                # 將當前活躍氣旋加入清單，供 update_typhoon_registry 自動建立/更新分頁使用
                active_typhoons.append({
                    "cwa_no": display_no,
                    "td_no": cwa_td_no,
                    "zh_name": name_zh if name_zh else ("熱帶性低氣壓" if clean_eng.startswith("TD-") else clean_eng),
                    "eng_name": clean_eng,
                    "last_time": dt_str[:16]
                })

                panel_blocks.append(f"""
                <div style="background-color: #fff9f5; border: 1px solid #ffccbc; border-radius: 6px; padding: 10px; margin-bottom: 10px;">
                    <b style="font-size: 14px; color: #d84315;">🌀 {title_name}</b> 
                    <span style="font-size:11px; background:#d84315; color:#fff; padding:1px 5px; border-radius:3px; margin-left:4px;">編號: {display_no}</span>
                    <hr style="margin: 6px 0; border: none; border-top: 1px dashed #ffab91;">
                    <div style="font-size: 12px; line-height: 1.6;">
                        <b>最新定位時間：</b>{dt_str}<br>
                        <b>中心位置：</b>北緯 {latest_fix.get('CoordinateLatitude')}°, 東經 {latest_fix.get('CoordinateLongitude')}°<br>
                        <b>中心氣壓：</b>{latest_fix.get('Pressure')} hPa<br>
                        <b>中心最大風速：</b>{latest_fix.get('MaxWindSpeed')} m/s (陣風: {latest_fix.get('MaxGustSpeed')} m/s)<br>
                        <b>移動速度/方向：</b>{latest_fix.get('MovingSpeed')} km/h，向 {latest_fix.get('MovingDirection')}<br>
                        <b>七級風暴風半徑：</b>{format_radius(latest_fix.get('Circle15ms'))}<br>
                        <b>十級風暴風半徑：</b>{format_radius(latest_fix.get('Circle25ms'))}<br>
                        <b>預測動態：</b>{extract_lang_text(latest_fix.get('MovingPrediction'))}
                    </div>
                </div>
                """)
                # 將該颱風資訊轉為 Markdown 格式寫入 README
                readme_content += f"#### {title_name} (編號: {display_no})\n"
                readme_content += f"- **最新定位時間**：{dt_str}\n"
                readme_content += f"- **中心位置**：北緯 {latest_fix.get('CoordinateLatitude')}°, 東經 {latest_fix.get('CoordinateLongitude')}°\n"
                readme_content += f"- **中心氣壓**：{latest_fix.get('Pressure')} hPa\n"
                readme_content += f"- **中心最大風速**：{latest_fix.get('MaxWindSpeed')} m/s (陣風: {latest_fix.get('MaxGustSpeed')} m/s)\n"
                readme_content += f"- **移動速度/方向**：{latest_fix.get('MovingSpeed')} km/h，向 {latest_fix.get('MovingDirection')}\n"
                readme_content += f"- **七級風暴風半徑**：{format_radius(latest_fix.get('Circle15ms'))}\n"
                readme_content += f"- **十級風暴風半徑**：{format_radius(latest_fix.get('Circle25ms'))}\n"
                readme_content += f"- **預測動態**：{extract_lang_text(latest_fix.get('MovingPrediction'))}\n\n"

        if panel_blocks:
            cyclone_panel_html = "".join(panel_blocks)
        else:
            cyclone_panel_html = "<p style='color:gray; padding:10px;'>目前西北太平洋及南海無活動中的熱帶氣旋紀錄。</p>"
            # 無颱風時的 README 顯示
            readme_content += "> 目前西北太平洋及南海無活動中的熱帶氣旋紀錄。\n\n"
except Exception as e:
    print(f"讀取 CWA W-C0034-005 發生錯誤: {e}")
    cyclone_panel_html = f"<p style='color:red; padding:10px;'>無法解析熱帶氣旋資料: {e}</p>"

# 更新 typhoons_index.json 並動態產生「歷史紀錄」面板按鈕 HTML
typhoon_registry = update_typhoon_registry(active_typhoons, current_time_str)
history_buttons_list = []
for eng_key, info in typhoon_registry.items():
    is_active = info.get("status") == "active"
    status_badge = (
        '<span style="background:#d9534f; color:white; padding:1px 5px; border-radius:3px; font-size:10px; margin-left:6px;">活躍中</span>'
        if is_active else
        '<span style="background:#777; color:white; padding:1px 5px; border-radius:3px; font-size:10px; margin-left:6px;">歷史存檔</span>'
    )
    cwa_no_str = info.get("cwa_no", "")
    cwa_badge = f" (編號: {cwa_no_str})" if cwa_no_str and cwa_no_str != "-" else ""
    zh_label = info.get("zh_name", "")
    eng_label = info.get("eng_name", eng_key)
    display_title = f"{zh_label} ({eng_label})" if (zh_label and zh_label != eng_label) else eng_label

    history_buttons_list.append(f"""
            <button onclick="window.open('{info['filename']}', '_blank')" class="quick-btn" style="text-align: left; padding: 12px; font-size: 13px; border: 1px solid #ffccbc; background: #fff9f5; transition: 0.2s;">
                <b style="color: #d84315;">🌀 {display_title}{cwa_badge}</b>{status_badge}<br>
                <span style="font-size: 11px; color: #555; margin-top: 4px; display: inline-block;">最後更新: {info['last_updated']}</span>
            </button>""")

history_buttons_html = "\n".join(history_buttons_list) if history_buttons_list else "<p style='color:gray;'>尚無歷史颱風檔案</p>"

# --- [圖層 3 & 4] CWA 警報與侵襲機率圖資 ---
print("正在讀取 CWA 颱風警報與暴風圈 (W-C0034-002)...")
cwa_002_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-002?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
cwa_002_root = fetch_kml_from_kmz(cwa_002_url)
parse_and_add_cwa_002(cwa_002_root, fg_cwa_route)

print("正在讀取 CWA 暴風圈侵襲機率 (W-C0034-003)...")
cwa_003_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
parse_and_add_cwa_003(fetch_kml_from_kmz(cwa_003_url), fg_cwa_wsp)

# --- 輔助函式：取得 CWA Product XML (用於風雨預測) ---
def get_cwa_product_xml(api_url):
    try:
        res = requests.get(api_url, timeout=15, verify=False)
        res.raise_for_status()
        product_url = None
        try:
            data = res.json()
            product_url = data.get("cwaopendata", {}).get("Dataset", {}).get("Resource", {}).get("ProductURL")
        except Exception:
            root = ET.fromstring(res.content)
            for elem in root.iter():
                if elem.tag.endswith("ProductURL") and elem.text:
                    product_url = elem.text.strip()
                    break
        if product_url:
            xml_res = requests.get(product_url, timeout=15, verify=False)
            xml_res.raise_for_status()
            return xml_res.content
        else:
            return res.content
    except Exception as e:
        print(f"取得 CWA Product XML 失敗 ({api_url}): {e}")
        return None

# --- 輔助函式：美化表格的 CSS ---
TABLE_CSS = """
<style>
    .cwa-table { width: 100%; border-collapse: collapse; font-size: 12px; text-align: center; font-family: sans-serif; }
    .cwa-table th { background: #337ab7; color: white; padding: 6px; position: sticky; top: 0; z-index: 2; border: 1px solid #286090; }
    .cwa-table td { border: 1px solid #ddd; padding: 6px; vertical-align: middle; }
    .cwa-table tr:nth-child(even) { background: #f9f9f9; }
    .cwa-table tr:hover { background: #f1f7fd; }
    .danger-wind { color: #d9534f; font-weight: bold; }
    .table-container { max-height: 350px; overflow-y: auto; margin-bottom: 15px; border: 1px solid #ddd; }
</style>
"""

# --- 專門解析風力預測 (WindTable.xml 結構) ---
def parse_wind_xml(xml_content):
    if not xml_content:
        return "<p style='color:gray;'>暫無風力資料</p>"
    try:
        root = ET.fromstring(xml_content)
        # 使用 defaultdict 來整理同一個地區的不同時間段資料
        wind_data = defaultdict(list)
        
        # 尋找所有 AreaForecastData 節點
        for area_node in root.findall(".//AreaForecastData"):
            area_name = area_node.get("area", "未知區域")
            start_time_str = area_node.find(".//StartValidTime/Time").text if area_node.find(".//StartValidTime/Time") is not None else ""
            end_time_str = area_node.find(".//EndValidTime/Time").text if area_node.find(".//EndValidTime/Time") is not None else ""
            
            # 格式化時間 (只取 日/時)
            try:
                st = dateutil.parser.isoparse(start_time_str).strftime("%d日%H時")
                et = dateutil.parser.isoparse(end_time_str).strftime("%d日%H時")
                time_range = f"{st} - {et}"
            except:
                time_range = start_time_str
            
            # 取得風力 (平均風與陣風)
            wind_type = area_node.find(".//WindData").get("type", "")
            level = area_node.find(".//WindData/Level").text if area_node.find(".//WindData/Level") is not None else "-"
            
            # 整理結構：以 (地區, 時間) 為 Key
            wind_data[(area_name, time_range)].append((wind_type, level))

        if not wind_data:
            return "<p style='color:gray;'>目前非警報期間，無風力預測。</p>"

        # 組合 HTML 表格
        html = TABLE_CSS + "<div class='table-container'><table class='cwa-table'><tr><th>地區</th><th>時間區間</th><th>平均風</th><th>陣風</th></tr>"
        
        # 整理輸出
        for (area, time_range), values in wind_data.items():
            avg_wind = "-"
            gust_wind = "-"
            for v_type, v_level in values:
                if v_type == "average": avg_wind = v_level
                if v_type == "gust": gust_wind = v_level
            
            # 如果陣風超過 10 級，標記為紅色警告
            gust_class = "danger-wind" if "10" in gust_wind or "11" in gust_wind or "12" in gust_wind else ""
            
            html += f"<tr><td><b>{area}</b></td><td>{time_range}</td><td>{avg_wind}</td><td class='{gust_class}'>{gust_wind}</td></tr>"
        
        html += "</table></div>"
        return html
    except Exception as e:
        return f"<p style='color:red;'>解析風力預測失敗: {e}</p>"

# --- 專門解析雨量預測 (動態解析 S3 內的 Table 結構) ---
def parse_rain_xml(xml_content, title_prefix="雨量"):
    if not xml_content:
        return f"<p style='color:gray;'>暫無{title_prefix}資料</p>"
    try:
        root = ET.fromstring(xml_content)
        html = TABLE_CSS + f"<div class='table-container'><table class='cwa-table'><tr><th>地區</th><th>預測值 (毫米)</th></tr>"
        
        has_data = False
        # CWA 降雨量預測表格通常用 County / Area / Station 作為分層，包含 PredictValue 或 Rainfall
        # 這裡採用一種容錯率較高的爬梳法：找尋所有包含預測值的節點
        for item in root.iter():
            tag = item.tag.lower()
            if "county" in tag or "location" in tag or "area" in tag:
                name = ""
                value = ""
                for child in item:
                    ctag = child.tag.lower()
                    if "name" in ctag:
                        name = child.text
                    elif "predictvalue" in ctag or "rainfall" in ctag or "value" in ctag:
                        value = child.text
                
                if name and value:
                    html += f"<tr><td><b>{name}</b></td><td>{value}</td></tr>"
                    has_data = True
                    
        html += "</table></div>"
        
        if not has_data:
            return f"<p style='color:gray;'>目前無最新的{title_prefix}預測數據 (XML結構可能為空或無警戒)。</p>"
            
        return html
    except Exception as e:
        return f"<p style='color:red;'>解析{title_prefix}預測失敗: {e}</p>"

   
# --- [API 擴充] 風力與雨量預測 ---
print("正在讀取 CWA 風力預測 (F-C0034-005)...")
f_005_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-C0034-005?Authorization={CWA_API_KEY}&downloadType=WEB&format=JSON"
wind_xml_data = get_cwa_product_xml(f_005_url)
wind_html = parse_wind_xml(wind_xml_data)

print("正在讀取 CWA 24小時雨量預測 (F-C0034-006)...")
f_006_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-C0034-006?Authorization={CWA_API_KEY}&downloadType=WEB&format=JSON"
rain_24h_xml_data = get_cwa_product_xml(f_006_url)
rain_24h_html = parse_rain_xml(rain_24h_xml_data, "24小時雨量")

print("正在讀取 CWA 總雨量預測 (F-C0034-007)...")
f_007_url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-C0034-007?Authorization={CWA_API_KEY}&downloadType=WEB&format=JSON"
rain_total_xml_data = get_cwa_product_xml(f_007_url)
rain_total_html = parse_rain_xml(rain_total_xml_data, "總雨量")

# --- [API 擴充] 日本氣象廳 (JMA) 颱風 JSON API ---
print("正在讀取 JMA (日本氣象廳) 颱風資料...")
def fetch_jma_info():
    jma_url = "https://www.jma.go.jp/bosai/typhoon/data/TC.json"
    try:
        res = requests.get(jma_url, timeout=10, verify=False)
        if res.status_code == 404:
            return "<p style='color:green; padding: 10px; font-weight:bold;'>✅ 目前西太平洋無活躍颱風，日本氣象廳 (JMA) 暫無發布警報。</p>"
        res.raise_for_status()
        data = res.json()
        if not data:
            return "<p style='color:gray; padding: 10px;'>目前日本氣象廳 (JMA) 無發布活躍的颱風資訊。</p>"
        
        html_output = "<ul style='padding-left: 20px;'>"
        for tc in data:
            name_en = tc.get('name', {}).get('romaji', '未知名稱')
            name_kana = tc.get('name', {}).get('kana', '')
            time_dt = tc.get('dateTime', '未知時間')
            html_output += f"<li style='margin-bottom:8px;'><b>{name_en} ({name_kana})</b><br><small style='color:#666;'>JMA 更新時間: {time_dt}</small></li>"
        html_output += "</ul>"
        return html_output
    except Exception as e:
        return f"<p style='color:red; padding:10px;'>無法取得 JMA 資訊 (錯誤: {e})</p>"

jma_html = fetch_jma_info()

# --- CWA 警報文字解析 ---
cwa_001_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-001?Authorization={CWA_API_KEY}"
alert_html = ""
alert_areas_list = [] # 用來儲存警戒縣市名單，供地圖動態塗色使用

try:
    res = requests.get(cwa_001_url, timeout=15)
    if res.status_code == 200:
        data = res.json()
        info_list = data.get("records", {}).get("info", [])
        if info_list:
            latest = info_list[0]
            headline_text = latest.get('headline', '颱風警報')
            
            # 判斷若標題包含「解除」，則強制清空警戒區域清單
            if "解除" in headline_text:
                areas = []
                alert_areas_list = []
            else:
                areas = [a.get("areaDesc", "") for a in latest.get("area", [])]
                alert_areas_list = areas 
                
            area_str = "、".join(areas) if areas else "無"
            desc_text = ""
            for sec in latest.get("description", {}).get("section", []):
                title = sec.get('title', '')
                value = sec.get('value', '')
                if title and value:
                    desc_text += f"<b style='color:#337ab7;'>[{title}]</b> {value}<br><br>"
            # 判斷是否解除，給予不同顏色
            theme_color = "#5cb85c" if "解除" in headline_text else "#d9534f"
            alert_html = f"""
            <h4 style="margin:0 0 5px 0; color:{theme_color};"> {latest.get('headline', '颱風警報')}</h4>
            <small>CWA 發布時間：{latest.get('effective', '')}</small><hr style="margin:5px 0;">
            <div style="margin-bottom: 10px; font-size: 13px;"><b>【警戒區域】</b>：{area_str}</div>
            <div style="font-size: 12px; line-height: 1.6; background-color: #f9f9f9; padding: 10px; border-radius: 6px; border-left: 4px solid #d9534f; overflow-wrap: break-word;">{desc_text}</div>
            """
except Exception as e:
    print(f"解析 CWA 警報時發生錯誤：{e}")

if not alert_html:
    alert_html = """
    <h4 style="margin:0 0 5px 0; color:#0275d8; padding-top: 5px;">即時颱風消息</h4>
    <p style="color:gray;">目前臺灣氣象署尚未發布颱風警報。可點選「熱帶氣旋動態」查看西太平洋現存氣旋。</p>
    """

# --- 介面面板 (RWD 優化) ---
chart_data_json_str = json.dumps(all_chart_data) if 'all_chart_data' in locals() else "{}"

tab_panel_html = f"""
<!-- 引入 Chart.js -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
    .info-panel {{
        position: fixed; 
        bottom: 30px; 
        right: 30px; 
        width: 460px; 
        height: 530px;
        background-color: white; 
        border: 2px solid #337ab7; 
        z-index: 9999; 
        font-size: 13px;
        padding: 10px; 
        border-radius: 8px; 
        box-shadow: 0 0 10px rgba(0,0,0,0.3); 
        opacity: 0.97;
        display: flex; 
        flex-direction: column; 
        box-sizing: border-box;
    }}
    .legend-panel {{
        position: fixed; 
        bottom: 30px; 
        left: 30px; 
        width: 210px; 
        background-color: white; 
        border:2px solid gray; 
        z-index:9999; 
        font-size:12px;
        padding: 10px; 
        border-radius: 6px; 
        opacity: 0.95;
    }}
    .quick-btn {{
        padding: 4px 8px; 
        border: 1px solid #ccc; 
        border-radius: 12px; 
        background: #fff; 
        cursor: pointer; 
        font-size: 11px;
    }}
    .tab-btn {{
        flex: 1 1 auto; 
        padding: 6px 4px; 
        border: none; 
        background: #eee; 
        color: #333; 
        font-weight: bold; 
        cursor: pointer; 
        border-radius: 4px;
        font-size: 11px;
        text-align: center;
    }}
    @media (max-width: 768px) {{
        .info-panel {{
            right: 0; bottom: 0; width: 100%; height: 45vh; 
            border-radius: 12px 12px 0 0; border-bottom: none; border-left: none; border-right: none; opacity: 1;
        }}
        .legend-panel {{
            left: 10px; bottom: 48vh; width: auto; max-width: 200px; font-size: 10px; padding: 6px; max-height: 150px; overflow-y: auto;
        }}
        .tab-btn {{ font-size: 10px; padding: 4px 2px; }}
    }}
</style>

<div class="info-panel">
    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px;">
        <b style="font-size: 15px; color: #333;">颱風即時動態與預測整合</b>
    </div>
    <div style="font-size: 11px; color: #666; margin-bottom: 8px; border-bottom: 1px solid #ccc; padding-bottom: 5px;">
        <i class="fa fa-clock"></i> 系統資料更新時間：<b>{current_time_str}</b>
    </div>

    <!-- 頁籤按鈕 -->
    <div style="display: flex; border-bottom: 2px solid #337ab7; margin-bottom: 8px; flex-wrap: wrap; gap: 4px; padding-bottom: 4px;">
        <button onclick="switchTab('tab-chat')" id="btn-chat" class="tab-btn" style="background:#337ab7; color:white;">💬 查詢</button>
        <button onclick="switchTab('tab-cyclone')" id="btn-cyclone" class="tab-btn">熱帶氣旋</button>
        <button onclick="switchTab('tab-alert')" id="btn-alert" class="tab-btn">CWA 警報</button>
        <button onclick="switchTab('tab-jma')" id="btn-jma" class="tab-btn">JMA 動態</button>
        <button onclick="switchTab('tab-wind')" id="btn-wind" class="tab-btn">風力/雨量</button>
        <button onclick="switchTab('tab-history')" id="btn-history" class="tab-btn">歷史紀錄</button>
    </div>

    <!-- 聊天室與搜尋分頁 -->
    <div id="tab-chat" class="tab-content" style="display:flex; flex-direction:column; flex:1; overflow:hidden;">
        <div id="chat-history" style="flex:1; background:#f9f9f9; padding:10px; overflow-y:auto; border-radius:4px; margin-bottom:8px; border:1px solid #ddd;">
            <div style="margin-bottom:12px;">
                <b style="color:#337ab7;">🤖 系統助理：</b><br>
                <span style="background:#eef; padding:6px 10px; border-radius:8px; display:inline-block; margin-top:4px;">
                    您好！請點選下方按鈕查詢颱風資訊。
                </span>
            </div>
        </div>
        <div style="margin-bottom:8px; display:flex; gap:6px; flex-wrap:wrap;">
            <button onclick="sendQuery('最新警報')" class="quick-btn" style="color:#d9534f;">🚨 最新警報</button>
            <button onclick="sendQuery('颱風動態')" class="quick-btn" style="color:#e65100;">🌀 颱風動態</button>
            <button onclick="sendQuery('風力預測')" class="quick-btn" style="color:#5bc0de;">💨 風力預測</button>
            <button onclick="sendQuery('雨量預測')" class="quick-btn" style="color:#0275d8;">☔ 雨量預測</button>
        </div>
    </div>

    <!-- 原有資料隱藏容器 -->
    <div id="data-cyclone" style="display:none;">{cyclone_panel_html}</div>
    <div id="data-alert" style="display:none;">{alert_html}</div>
    <div id="data-jma" style="display:none;">{jma_html}</div>
    <div id="data-wind" style="display:none;">{wind_html}</div>
    <div id="data-rain" style="display:none;">{rain_24h_html}<br>{rain_total_html}</div>

    <!-- 實際內容面板 -->
    <div id="tab-cyclone" class="tab-content" style="display:none; overflow-y:auto; flex:1; line-height:1.6;">{cyclone_panel_html}</div>
    <div id="tab-alert" class="tab-content" style="display:none; overflow-y:auto; flex:1; line-height:1.6;">{alert_html}</div>
    <div id="tab-jma" class="tab-content" style="display:none; overflow-y:auto; flex:1; line-height:1.6;">{jma_html}</div>
    <div id="tab-wind" class="tab-content" style="display:none; flex-direction:column; flex:1; overflow:hidden;">
        <div style="display: flex; gap: 5px; margin-bottom: 10px;">
            <button onclick="document.getElementById('w-content').style.display='block'; document.getElementById('r24-content').style.display='none'; document.getElementById('rt-content').style.display='none';" style="flex:1; padding:5px; cursor:pointer;">💨 風力預測</button>
            <button onclick="document.getElementById('w-content').style.display='none'; document.getElementById('r24-content').style.display='block'; document.getElementById('rt-content').style.display='none';" style="flex:1; padding:5px; cursor:pointer;">☔ 24H雨量</button>
            <button onclick="document.getElementById('w-content').style.display='none'; document.getElementById('r24-content').style.display='none'; document.getElementById('rt-content').style.display='block';" style="flex:1; padding:5px; cursor:pointer;">🌧️ 總雨量</button>
        </div>
        <div id="w-content" style="display:block; flex:1; overflow-y:auto;">
            <b style="color:#e65100;">各地區風力預測</b><br>{wind_html}
        </div>
        <div id="r24-content" style="display:none; flex:1; overflow-y:auto;">
            <b style="color:#0275d8;">24小時雨量預測</b><br>{rain_24h_html}
        </div>
        <div id="rt-content" style="display:none; flex:1; overflow-y:auto;">
            <b style="color:#0275d8;">總雨量預測</b><br>{rain_total_html}
        </div>
    </div>
    
    <!-- 歷史紀錄面板 -->
    <div id="tab-history" class="tab-content" style="display:none; flex-direction:column; flex:1; overflow-y:auto;">
        
        <!-- 動態圖表區塊 -->
        <div id="dynamic-chart-container" style="display:none; background-color: #fff; padding: 10px; border-radius: 4px; border: 1px solid #ddd; margin-bottom: 8px;">
            <b id="chart-title" style="color: #337ab7; font-size: 14px;"><i class="fa fa-chart-line"></i> 強度趨勢圖</b>
            <div style="position: relative; height: 220px; width: 100%; margin-top: 10px;">
                <canvas id="typhoonTrendChart"></canvas>
            </div>
        </div>

        <!-- 歷史存檔按鈕 -->
        <div style="background-color: #f9f9f9; padding: 10px; border-radius: 4px; border: 1px solid #ddd; margin-bottom: 8px;">
            <b style="color: #337ab7; font-size: 14px;"><i class="fa fa-folder-open"></i> 颱風專屬頁面與歷史存檔</b>
        </div>
        <div id="history-buttons-container" style="display: flex; flex-direction: column; gap: 8px;">
            {history_buttons_html}
        </div>
    </div>
</div>
      
<script>
// 接收 Python 動態產生的所有颱風歷史資料 JSON
const allChartData = {chart_data_json_str};
let trendChart = null;

function renderChart() {{
    const typhoonNames = Object.keys(allChartData);
    if (typhoonNames.length === 0) return;
    
    // 顯示圖表區塊
    document.getElementById('dynamic-chart-container').style.display = 'block';
    
    // 預設抓取陣列中最新的一個颱風來畫圖
    const tName = typhoonNames[0];
    const tData = allChartData[tName];
    
    document.getElementById('chart-title').innerHTML = `<i class="fa fa-chart-line"></i> ${{tName}} 強度趨勢圖`;

    const ctx = document.getElementById('typhoonTrendChart');
    if (!ctx || trendChart) return; 

    trendChart = new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: tData.labels,
            datasets: [
                {{
                    label: '中心最大風速 (m/s)',
                    data: tData.winds, 
                    borderColor: '#d9534f', 
                    backgroundColor: 'rgba(217, 83, 79, 0.1)',
                    yAxisID: 'y',
                    tension: 0.3, 
                    fill: true
                }},
                {{
                    label: '中心氣壓 (hPa)',
                    data: tData.pressures, 
                    borderColor: '#337ab7', 
                    backgroundColor: 'rgba(51, 122, 183, 0.1)',
                    yAxisID: 'y1',
                    tension: 0.3
                }}
            ]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {{ mode: 'index', intersect: false }},
            plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }} }} }} }},
            scales: {{
                x: {{ ticks: {{ font: {{ size: 10 }} }} }},
                y: {{
                    type: 'linear', display: true, position: 'left',
                    title: {{ display: true, text: '風速 (m/s)', font: {{ size: 10 }} }}
                }},
                y1: {{
                    type: 'linear', display: true, position: 'right', reverse: true,
                    title: {{ display: true, text: '氣壓 (hPa)', font: {{ size: 10 }} }},
                    grid: {{ drawOnChartArea: false }} 
                }}
            }}
        }}
    }});
}}

// 自動從 typhoons_index.json 同步最新分頁清單 (讓已封存的歷史分頁也能顯示最新生成的颱風連結)
async function syncHistoryButtons() {{
    const container = document.getElementById('history-buttons-container');
    if (!container) return;
    try {{
        const res = await fetch('typhoons_index.json?t=' + Date.now());
        if (!res.ok) return;
        const registry = await res.json();
        const entries = Object.values(registry);
        if (entries.length === 0) return;

        container.innerHTML = entries.map(info => {{
            const isActive = info.status === 'active';
            const badge = isActive
                ? '<span style="background:#d9534f; color:white; padding:1px 5px; border-radius:3px; font-size:10px; margin-left:6px;">活躍中</span>'
                : '<span style="background:#777; color:white; padding:1px 5px; border-radius:3px; font-size:10px; margin-left:6px;">歷史存檔</span>';
            const cwaNo = (info.cwa_no && info.cwa_no !== '-') ? ` (編號: ${{info.cwa_no}})` : '';
            const zhName = info.zh_name || '';
            const engName = info.eng_name || '';
            const titleText = (zhName && zhName !== engName) ? `${{zhName}} (${{engName}})` : engName;
            return `
            <button onclick="window.open('${{info.filename}}', '_blank')" class="quick-btn" style="text-align: left; padding: 12px; font-size: 13px; border: 1px solid #ffccbc; background: #fff9f5; transition: 0.2s;">
                <b style="color: #d84315;">🌀 ${{titleText}}${{cwaNo}}</b>${{badge}}<br>
                <span style="font-size: 11px; color: #555; margin-top: 4px; display: inline-block;">最後更新: ${{info.last_updated || ''}}</span>
            </button>`;
        }}).join('');
    }} catch (err) {{
        // 離線或本地開啟時保留 Python 預渲染的靜態按鈕
    }}
}}

function switchTab(tabId) {{
    const tabIds = ['tab-chat', 'tab-cyclone', 'tab-alert', 'tab-jma', 'tab-wind', 'tab-history']; 
    tabIds.forEach(function(id) {{
        const panel = document.getElementById(id);
        const button = document.getElementById('btn-' + id.replace('tab-', ''));
        const active = id === tabId;
        if (panel) {{
            if (active) {{
                panel.style.display = (id === 'tab-chat' || id === 'tab-wind' || id === 'tab-history') ? 'flex' : 'block';
                
                // 切換到歷史頁籤時觸發圖表繪製與清單同步
                if (id === 'tab-history') {{
                    setTimeout(renderChart, 100);
                    syncHistoryButtons();
                }}
            }} else {{
                panel.style.display = 'none';
            }}
        }}
        if (button) {{
            button.style.background = active ? '#337ab7' : '#eee';
            button.style.color = active ? 'white' : '#333';
        }}
    }});
}}

function sendQuery(text) {{
    if(!text || !text.trim()) return;
    const history = document.getElementById('chat-history');
    
    history.innerHTML += `<div style="margin-bottom:12px; text-align:right;">
        <span style="background:#dcf8c6; padding:6px 10px; border-radius:8px; display:inline-block; max-width:85%; word-break:break-all;">${{text}}</span>
    </div>`;

    let response = "抱歉，找不到相關資訊。";
    if(text.includes('警報') || text.includes('颱風警報')) {{
        response = document.getElementById('data-alert').innerHTML;
    }} else if(text.includes('颱風') || text.includes('動態') || text.includes('氣旋')) {{
        response = document.getElementById('data-cyclone').innerHTML;
    }} else if(text.includes('風力') || text.includes('風速')) {{
        response = document.getElementById('data-wind').innerHTML;
    }} else if(text.includes('雨量') || text.includes('降雨')) {{
        response = document.getElementById('data-rain').innerHTML;
    }} else if(text.includes('日本') || text.includes('JMA')) {{
        response = document.getElementById('data-jma').innerHTML;
    }}

    history.innerHTML += `<div style="margin-bottom:12px; text-align:left;">
        <b style="color:#337ab7; font-size:12px;">🤖 系統助理：</b><br>
        <span style="background:#eef; padding:8px 10px; border-radius:8px; display:inline-block; max-width:90%; overflow-x:auto; line-height:1.5;">
            ${{response}}
        </span>
    </div>`;
    
    history.scrollTop = history.scrollHeight;
}}
</script>
"""


# 圖例 (加入 CSS Class 進行 RWD 控制)
legend_html = """
<div class="legend-panel">
  <b>圖層路徑圖例</b><br>
  <span style="color:#e65100; font-weight:bold;">- - -</span> CWA (005) 歷史分析軌跡<br>
  <span style="color:#c2185b; font-weight:bold;">———</span> CWA (005) 官方預測路徑<br>
  <hr style="margin:4px 0;">
  <i class="fa fa-minus" style="color:red"></i> 台灣 (CWA/NCDR)<br>
  <i class="fa fa-minus" style="color:blue"></i> 日本 (JMA)<br>
  <i class="fa fa-minus" style="color:green"></i> 美國 (JTWC)<br>
  <i class="fa fa-minus" style="color:purple"></i> 韓國 (KMA)<br>
  <i class="fa fa-minus" style="color:orange"></i> 中國 (CMA)<br>
  <i class="fa fa-square" style="color:#d9534f; opacity:0.6;"></i> CWA 警報暴風圈/潛勢區<br>
  <i class="fa fa-square" style="color:purple; opacity:0.5;"></i> CWA 侵襲機率區
</div>
"""

# --- [圖層 5] 縣市邊界與警戒區動態塗色 ---
county_geojson_path = "twcounty2010.5.json"

def county_style(feature):
    county_name = feature['properties'].get('county', '')
    
    # 判斷該縣市是否有在最新的警報名單中
    is_alert = False
    # 容錯處理：API可能回傳「台東縣」或「臺東縣」，做個簡單替換比對
    county_name_alt = county_name.replace("臺", "台") if "臺" in county_name else county_name.replace("台", "臺")
    
    for a in alert_areas_list:
        if county_name in a or county_name_alt in a or a in county_name:
            is_alert = True
            break
            
    if is_alert:
        return {
            'fillColor': '#d9534f', # 若在警戒區內：填滿警戒紅
            'color': '#b52b27',     # 邊線深紅
            'weight': 2,
            'fillOpacity': 0.45
        }
    else:
        return {
            'fillColor': '#cccccc', # 若無警報：透明/淺灰色
            'color': '#666666',     # 邊線灰色
            'weight': 1,
            'fillOpacity': 0.05
        }

if os.path.exists(county_geojson_path):
    try:
        with open(county_geojson_path, 'r', encoding='utf-8') as f:
            geo_data = json.load(f)
        
        folium.GeoJson(
            geo_data,
            name="台灣縣市邊界 (警戒區自動塗色)",
            style_function=county_style,
            tooltip=folium.GeoJsonTooltip(fields=['county'], aliases=['📍 縣市：'], style="font-size: 14px; font-weight: bold;")
        ).add_to(fg_county)
    except Exception as e:
        print(f"載入縣市邊界資料失敗: {e}")
else:
    print(f"找不到檔案 {county_geojson_path}，請確認檔案與程式碼放在同一資料夾內。")

# 右下角作者版權浮水印
watermark_html = """
<div style="position: fixed; bottom: 5px; right: 10px; z-index: 9999; font-size: 11px; color: #555; background: rgba(255,255,255,0.7); padding: 2px 6px; border-radius: 4px; pointer-events: none;">
    邊界線圖資提供：<a href="https://github.com/ronnywang" target="_blank" style="color: #337ab7; pointer-events: auto;">ronnywang (GitHub)</a>
</div>
"""

m.get_root().html.add_child(folium.Element(tab_panel_html))
m.get_root().html.add_child(folium.Element(legend_html))
m.get_root().html.add_child(folium.Element(watermark_html)) # 加入浮水印

# 圖層疊加順序 
fg_county.add_to(m)   # 加入縣市圖層
fg_cwa_wsp.add_to(m)
fg_cwa_route.add_to(m)
fg_cwa_005.add_to(m)
fg_ncdr.add_to(m)

# 顯示地圖右上角的圖層切換控制項
folium.LayerControl(position="topright", collapsed=False).add_to(m)

output_file = "index.html"
m.save(output_file)
print(f"地圖已成功更新，已加入 RWD 手機版響應式排版支援：{output_file}")

# --- [關鍵新增] 自動建立與同步更新當前「活躍中」的熱帶性低氣壓/颱風專屬分頁 ---
# 已結束 (status == "historical") 的颱風不會進入此複製迴圈，因此會自動停止更新並完整封存最後狀態
for eng_key, info in typhoon_registry.items():
    if info.get("status") == "active":
        target_filename = info.get("filename")
        if target_filename:
            shutil.copyfile(output_file, target_filename)
            print(f"✅ 已自動建立/同步更新活躍氣旋專屬分頁：{target_filename}")

# --- 自動產生 Netlify 重新導向設定檔 (_redirects) ---
redirect_lines = []
for eng_key, info in typhoon_registry.items():
    fname = info.get("filename", "")
    if fname:
        short_path = "/" + fname.replace(".html", "").lower()
        redirect_lines.append(f"{short_path}    /{fname}    200")
# 找不到特定分頁時自動導回首頁，避免出現 Netlify 404 錯誤頁
redirect_lines.append("/typhoonvis_*.html    /index.html    302")

with open("_redirects", "w", encoding="utf-8") as f:
    f.write("\n".join(redirect_lines) + "\n")
print("已成功產生 Netlify 導向設定檔：_redirects")

# 輸出 README.md 檔案
with open("README.md", "w", encoding="utf-8") as f:
    f.write(readme_content)
print("已成功自動更新 README.md 檔")
