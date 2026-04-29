import codecs
import pydeck as pdk
import background_code
import streamlit as st
import pandas as pd
import geopandas as gpd

from shapely import wkb
from datetime import timedelta, datetime
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

st.set_page_config(layout="wide")

title_col, logo_col = st.columns([4, 2])
with title_col:
    st.title("Modelleeromgeving voor alle middenspanningsstations in Nederland")
with logo_col:
    st.image("assets/slic_logo_citynetzero_donker.svg", width=400)
st.write("Selecteer het MSR dat je wilt analyseren.")
st.markdown('<a href="mailto:m.j.f.jenks@hva.nl">Vragen of opmerkingen</a>', unsafe_allow_html=True)

if st.button("🔄 Data Verversen"):
    st.cache_resource.clear()
    st.session_state.clear()
    st.rerun()

bg = background_code.BackgroundCode()

# --- Load data into session state ---
if "workbook" not in st.session_state:
    st.session_state.workbook = bg.load_Gsheets()

workbook = st.session_state.workbook

if "MSRs" not in st.session_state:
    st.session_state.MSRs = bg.get_sheet_dataframe("MSRs short", workbook)

if "vbo_objects" not in st.session_state:
    st.session_state.vbo_objects = bg.get_sheet_dataframe("Objects", workbook)

if "profielen" not in st.session_state:
    st.session_state.profielen = bg.get_sheet_dataframe("Profielen", workbook)

msr_gdf = bg.build_msr_gdf(st.session_state.MSRs)
profielen_df = st.session_state.profielen
gebruik_df = bg.build_gebruik_df(st.session_state.vbo_objects)

# --- Session state defaults ---
if "selected_id" not in st.session_state:
    st.session_state.selected_id = None
if "map_center" not in st.session_state:
    st.session_state.map_center = None
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 7
if "original_peak_power" not in st.session_state:
    st.session_state.original_peak_power = None
if "last_msr_id" not in st.session_state:
    st.session_state.last_msr_id = None
    st.session_state.cached_df = None
if "min_max" not in st.session_state:
    st.session_state.min_max = "-"
if "awaiting_confirmation" not in st.session_state:
    st.session_state.awaiting_confirmation = False
if "df_plot_data" not in st.session_state:
    st.session_state.df_plot_data = None


@st.cache_data(ttl=86400, show_spinner=False)
def get_address_from_coords(lat, lon):
    """Reverse geocode MSR coordinates to a human-readable Dutch address."""
    import math
    if lat is None or lon is None or math.isnan(float(lat)) or math.isnan(float(lon)):
        return None
    try:
        geolocator = Nominatim(user_agent="SlimLaden-MSR-App/1.0 (m.j.f.jenks@hva.nl)")
        location = geolocator.reverse(f"{lat}, {lon}", timeout=15, language='nl')
        if not location or not location.address:
            return None
        addr = location.raw.get('address', {})
        street = addr.get('road', '')
        house_number = addr.get('house_number', '')
        # MSRs are often in industrial areas — fall back through all place levels
        city = (
            addr.get('city')
            or addr.get('town')
            or addr.get('village')
            or addr.get('municipality')
            or addr.get('county')
            or addr.get('state_district')
            or ''
        )
        if street and city:
            return f"{street} {house_number}, {city}".replace(" ,", ",").strip()
        elif street:
            return street
        elif city:
            return city
        else:
            return location.address.split(',')[0]
    except GeocoderTimedOut:
        return None
    except Exception:
        return None


@st.cache_data
def geocode_address(address):
    try:
        geolocator = Nominatim(user_agent="msr_app")
        location = geolocator.geocode(address + ", Nederland", timeout=10)
        if location:
            return (location.latitude, location.longitude)
        return None
    except:
        return None


def normalize_msr_id(val):
    """Normalize owner_msr to a consistent string.
    Converts numeric floats ('12345.0' → '12345'), leaves non-numeric ('NO_MSR') as-is.
    """
    s = str(val).strip()
    try:
        return str(int(float(s)))
    except (ValueError, TypeError):
        return s


def parse_wkb(val):
    if val is None:
        return None
    try:
        if isinstance(val, str):
            if val.startswith("\\x"):
                val = val[2:]
            return wkb.loads(bytes.fromhex(val))
        return wkb.loads(val)
    except Exception:
        return None


@st.cache_data
def get_msr_points(_msr_gdf):
    """Convert MSR GeoDataFrame to a plain DataFrame with lon/lat for PyDeck."""
    gdf_wgs = _msr_gdf.to_crs(epsg=4326)
    return pd.DataFrame({
        "lon": gdf_wgs.geometry.x.tolist(),
        "lat": gdf_wgs.geometry.y.tolist(),
        "owner_msr": gdf_wgs["owner_msr"].astype(str).tolist(),
    })


def build_buildings_layer(cached_df, selected_id):
    """Parse WKB building geometries and return a red PyDeck ScatterplotLayer.

    Returns None when no valid points are found.
    """
    if cached_df is None or selected_id is None:
        return None
    try:
        df = cached_df.copy()
        df["geometry"] = df["vbo_points"].apply(parse_wkb)
        houses_gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:28992")

        selected_houses = houses_gdf[
            houses_gdf["owner_msr"].apply(normalize_msr_id) == normalize_msr_id(selected_id)
        ].to_crs(epsg=4326)

        points = []
        for geom in selected_houses.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "Point":
                points.append({"lon": geom.x, "lat": geom.y})
            else:
                for pt in geom.geoms:
                    points.append({"lon": pt.x, "lat": pt.y})

        if not points:
            return None

        return pdk.Layer(
            "ScatterplotLayer",
            id="buildings",
            data=pd.DataFrame(points),
            get_position=["lon", "lat"],
            get_fill_color=[220, 30, 30, 210],
            get_line_color=[140, 0, 0, 255],
            line_width_min_pixels=1,
            radius_min_pixels=3,
            radius_max_pixels=10,
            get_radius=8,
            pickable=False,
        )
    except Exception:
        return None


@st.fragment
def render_map_panel():
    """Kolom 1: MSR-kaart met gebouwen van het geselecteerde MSR als rode laag."""

    col_search, col_reset = st.columns([3, 1])
    with col_search:
        address_input = st.text_input(
            "Zoek een adres in Nederland...",
            key="address_search",
            placeholder="Bijv. Damrak 1, Amsterdam",
        )
    with col_reset:
        st.write("")
        if st.button("🔄 Reset kaart"):
            st.session_state.map_center = None
            st.session_state.map_zoom = 7
            st.rerun(scope="app")

    if address_input:
        coords = geocode_address(address_input)
        if coords:
            st.session_state.map_center = list(coords)
            st.session_state.map_zoom = 13
        else:
            st.warning("Adres niet gevonden. Probeer een specifiekere zoekterm.")

    st.markdown("---")

    msr_data = get_msr_points(msr_gdf)

    if st.session_state.map_center:
        view_lat, view_lon = st.session_state.map_center
        zoom = st.session_state.map_zoom
    else:
        view_lat = msr_data["lat"].mean()
        view_lon = msr_data["lon"].mean()
        zoom = 7

    # Gebouwen-laag (rood) onder de MSR-markers zodat blauw er bovenop ligt
    buildings_layer = build_buildings_layer(
        st.session_state.cached_df, st.session_state.selected_id
    )

    msr_layer = pdk.Layer(
        "ScatterplotLayer",
        id="msr-markers",
        data=msr_data,
        get_position=["lon", "lat"],
        get_fill_color=[31, 119, 180, 200],
        get_line_color=[255, 255, 255, 220],
        line_width_min_pixels=1,
        radius_min_pixels=4,
        radius_max_pixels=14,
        get_radius=250,
        pickable=True,
        auto_highlight=True,
        highlight_color=[255, 165, 0, 255],
    )

    # Layer-volgorde: gebouwen → MSR-markers → zoeklocatie (bovenste laag)
    layers = []
    if buildings_layer:
        layers.append(buildings_layer)
    layers.append(msr_layer)
    if st.session_state.map_center:
        search_lat, search_lon = st.session_state.map_center
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            id="search-location",
            data=pd.DataFrame({"lon": [search_lon], "lat": [search_lat]}),
            get_position=["lon", "lat"],
            get_fill_color=[255, 140, 0, 210],
            get_line_color=[180, 80, 0, 255],
            line_width_min_pixels=2,
            radius_min_pixels=8,
            radius_max_pixels=18,
            get_radius=400,
            pickable=False,
        ))

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(
            latitude=view_lat,
            longitude=view_lon,
            zoom=zoom,
            pitch=0,
        ),
        map_provider="carto",
        map_style="light",
        tooltip={"text": "MSR: {owner_msr}"},
    )

    chart = st.pydeck_chart(
        deck,
        on_select="rerun",
        selection_mode="single-object",
        height=600,
        use_container_width=True,
    )

    # Klik op MSR: laad gebouwen en herlaad volledige app
    selected_objects = chart.selection.objects.get("msr-markers", []) if chart.selection.objects else []
    if selected_objects:
        new_id = str(selected_objects[0]["owner_msr"])
        if new_id != str(st.session_state.last_msr_id):
            st.session_state.map_center = [selected_objects[0]["lat"], selected_objects[0]["lon"]]
            st.session_state.map_zoom = 14
            st.session_state.selected_id = new_id
            st.session_state.cached_df = bg.load_room_objects2(new_id, "datamichael13april26")
            st.session_state.last_msr_id = new_id
            st.session_state.original_peak_power = None
            st.session_state.df_plot_data = None
            st.rerun(scope="app")

    HvA_logo_url = "https://amsterdamgreencampus.nl/wp-content/uploads/2016/01/AmsUniOfAppSci.png"
    img = bg.image_converter(HvA_logo_url, 255, 255, 255, 255, 200)
    if img is not None:
        st.image(img)


@st.fragment
def render_analysis_panel():
    """Kolom 2 (inputs) + kolom 3 (grafiek & KPIs) als één fragment.

    Eén fragment zodat wijzigingen in de inputs direct de grafiek updaten
    zonder de kaart opnieuw te renderen.
    """
    col_inputs, col_chart = st.columns([1, 1.4], gap="medium")

    if not st.session_state.selected_id:
        with col_inputs:
            with st.container(border=True):
                st.info("👈 Klik op een MSR punt op de kaart om hier details te zien.")
        return

    msr_row = st.session_state.cached_df
    EV_jvb_per_auto = 3500

    # ------------------------------------------------------------------ #
    #  Kolom 2 – MSR-naam + alle invoer                                   #
    # ------------------------------------------------------------------ #
    with col_inputs:
        with st.container(border=True):

            # MSR header
            try:
                norm_id = normalize_msr_id(st.session_state.selected_id)
                selected_msr = msr_gdf[msr_gdf['owner_msr'].apply(normalize_msr_id) == norm_id]
                if len(selected_msr) > 0:
                    msr_wgs84 = selected_msr.to_crs(epsg=4326)
                    geom = msr_wgs84.geometry.iloc[0]
                    if geom is not None and not geom.is_empty:
                        msr_lat, msr_lon = geom.y, geom.x
                        msr_address = get_address_from_coords(msr_lat, msr_lon)
                        if msr_address:
                            st.subheader(f"MSR: {msr_address}")
                            st.caption(f"ID: {norm_id} | {msr_lat:.4f}, {msr_lon:.4f}")
                        else:
                            st.subheader(f"MSR: {norm_id}")
                            st.caption(f"Coördinaten: {msr_lat:.4f}, {msr_lon:.4f}")
                    else:
                        st.subheader(f"MSR: {norm_id}")
                else:
                    st.subheader(f"MSR: {st.session_state.selected_id}")
            except Exception as e:
                st.subheader(f"MSR: {st.session_state.selected_id}")
                st.caption(f"(Adres kon niet worden opgehaald: {e})")

            if len(msr_row) == 0:
                st.warning("Geen data beschikbaar voor dit MSR.")
                # Zet lege waarden zodat kolom 3 veilig kan renderen
                charge_strat = "Regulier on-demand laden"
                EV_adoption_perc = 0
                df_output = None
                start_date = end_date = None
            else:
                st.markdown("---")

                charge_strat = st.selectbox(
                    "Laadstrategie",
                    ("Regulier on-demand laden", "Netbewust slim laden", "Capaciteitspooling", "V2G"),
                    key="charge_strategy",
                )
                charge_strat_en = {
                    "Regulier on-demand laden": "Regular on-demand charging",
                    "Netbewust slim laden": "Grid-aware smart charging",
                    "Capaciteitspooling": "Capacity pooling",
                    "V2G": "V2G",
                }[charge_strat]

                try:
                    if "aantal_evs_m_msr" in msr_row.columns and "aantal_personenautos_msr" in msr_row.columns:
                        num_evs = msr_row["aantal_evs_m_msr"].iloc[0]
                        num_cars = msr_row["aantal_personenautos_msr"].iloc[0]
                        EV_perc_current = int(num_evs * 100 / num_cars) if num_cars > 0 else 0
                    else:
                        EV_perc_current = 25
                        st.info("⚠️ EV data niet beschikbaar, standaard 25% wordt gebruikt")
                except Exception as e:
                    EV_perc_current = 25
                    st.warning(f"Fout bij EV percentage: {e}. Standaard 25% wordt gebruikt.")

                EV_adoption_perc = st.slider(
                    f"EV-adoptie percentage (huidig: {EV_perc_current}%)",
                    EV_perc_current, 100, EV_perc_current,
                )

                df_output = bg.profile_creator(profielen_df, msr_row, EV_adoption_perc, EV_jvb_per_auto)
                df_output = bg.update_charge_strat(
                    df_output, charge_strat_en, profielen_df, msr_row, EV_adoption_perc, EV_jvb_per_auto
                )

                if (st.session_state.original_peak_power is None
                        or st.session_state.get("last_loaded_msr") != st.session_state.selected_id):
                    st.session_state.original_peak_power = df_output["MSR totaal_base profile [kW]"].max()
                    st.session_state.last_loaded_msr = st.session_state.selected_id

                st.markdown("---")
                st.markdown("**Dagselectie**")

                if st.button("📈 Dag met hoogste piekvermogen"):
                    st.session_state.date_max_power = df_output.loc[
                        df_output["MSR totaal [kW]"].idxmax(), "DATUM_TIJDSTIP_2024"
                    ]
                    st.session_state.min_max = "max"

                if st.button("📉 Dag met laagste piekvermogen"):
                    st.session_state.date_min_power = df_output.loc[
                        df_output["MSR totaal [kW]"].idxmin(), "DATUM_TIJDSTIP_2024"
                    ]
                    st.session_state.min_max = "min"

                min_date = df_output["DATUM_TIJDSTIP_2024"].min().date()
                max_date = df_output["DATUM_TIJDSTIP_2024"].max().date()
                default_start = min_date

                if st.session_state.min_max == "max" and "date_max_power" in st.session_state:
                    default_start = st.session_state.date_max_power
                elif st.session_state.min_max == "min" and "date_min_power" in st.session_state:
                    default_start = st.session_state.date_min_power

                if isinstance(default_start, pd.Timestamp):
                    default_start = default_start.date()
                default_start = min(max(default_start, min_date), max_date)

                start_date = st.date_input("Startdatum", default_start, min_value=min_date, max_value=max_date)
                end_date = st.date_input(
                    "Einddatum",
                    start_date + timedelta(days=1),
                    min_value=start_date + timedelta(days=1),
                    max_value=max_date,
                )

                date_range = (end_date - start_date).days
                if date_range <= 10:
                    bg.prepare_plot_df(start_date, end_date, df_output)
                else:
                    if not st.session_state.awaiting_confirmation:
                        st.warning(f"Geselecteerde periode: {date_range} dagen — dit kan traag zijn.")
                        if st.button("Ja, doorgaan"):
                            st.session_state.awaiting_confirmation = True
                        else:
                            st.stop()
                    if st.session_state.awaiting_confirmation:
                        bg.prepare_plot_df(start_date, end_date, df_output)
                        st.session_state.awaiting_confirmation = False

    # ------------------------------------------------------------------ #
    #  Kolom 3 – Grafiek + KPIs                                           #
    # ------------------------------------------------------------------ #
    with col_chart:
        with st.container(border=True):
            if df_output is None:
                return

            original_peak = st.session_state.original_peak_power

            st.subheader("Vermogensprofiel")
            if st.session_state.df_plot_data is not None:
                bg.plot_df_with_dashed_lines(
                    st.session_state.df_plot_data,
                    st.empty(),
                    max_base_profile=original_peak,
                )
            else:
                st.info("Selecteer een datumbereik om de grafiek te tonen.")

            st.markdown("---")
            st.subheader("KPI's")

            num_autos = int(msr_row["aantal_personenautos_msr"].iloc[0])
            st.markdown(f"""
            <div style='background-color:#f0f2f6;padding:12px;border-radius:8px;margin-bottom:12px;'>
                <p style='color:#666;font-size:13px;margin:0;'>Aantal auto's (waarvan {EV_adoption_perc}% EV)</p>
                <p style='color:#1f77b4;font-size:26px;font-weight:bold;margin:4px 0;'>{num_autos:,}</p>
            </div>
            """, unsafe_allow_html=True)

            if charge_strat != "Regulier on-demand laden":
                peak_selected = df_output["MSR totaal [kW]"].max()
                PAR_base = (df_output["MSR totaal_base profile [kW]"].max()
                            / df_output["MSR totaal_base profile [kW]"].mean())
                PAR_selected = df_output["MSR totaal [kW]"].max() / df_output["MSR totaal [kW]"].mean()
                peak_reduction = original_peak - peak_selected

                st.markdown("**Piekvermogen**")
                k1, k2, k3 = st.columns(3)
                k1.markdown(f"""<div style='background:#fff3cd;padding:12px;border-radius:8px;border-left:4px solid #ffc107;'>
                    <p style='color:#666;font-size:11px;margin:0;'>On-demand (origineel)</p>
                    <p style='color:#333;font-size:20px;font-weight:bold;margin:4px 0;'>{int(original_peak):,} kW</p></div>""",
                    unsafe_allow_html=True)
                k2.markdown(f"""<div style='background:#d1ecf1;padding:12px;border-radius:8px;border-left:4px solid #17a2b8;'>
                    <p style='color:#666;font-size:11px;margin:0;'>Geselecteerd profiel</p>
                    <p style='color:#333;font-size:20px;font-weight:bold;margin:4px 0;'>{int(peak_selected):,} kW</p></div>""",
                    unsafe_allow_html=True)
                k3.markdown(f"""<div style='background:#d4edda;padding:12px;border-radius:8px;border-left:4px solid #28a745;'>
                    <p style='color:#666;font-size:11px;margin:0;'>Piekreductie</p>
                    <p style='color:#28a745;font-size:20px;font-weight:bold;margin:4px 0;'>{int(peak_reduction):,} kW</p></div>""",
                    unsafe_allow_html=True)

                st.markdown("**Peak-to-Average Ratio**")
                k1, k2, k3 = st.columns(3)
                k1.markdown(f"""<div style='background:#fff3cd;padding:12px;border-radius:8px;border-left:4px solid #ffc107;'>
                    <p style='color:#666;font-size:11px;margin:0;'>On-demand laden</p>
                    <p style='color:#333;font-size:20px;font-weight:bold;margin:4px 0;'>{round(PAR_base, 2)}</p></div>""",
                    unsafe_allow_html=True)
                k2.markdown(f"""<div style='background:#d1ecf1;padding:12px;border-radius:8px;border-left:4px solid #17a2b8;'>
                    <p style='color:#666;font-size:11px;margin:0;'>Geselecteerd profiel</p>
                    <p style='color:#333;font-size:20px;font-weight:bold;margin:4px 0;'>{round(PAR_selected, 2)}</p></div>""",
                    unsafe_allow_html=True)
                k3.markdown(f"""<div style='background:#d4edda;padding:12px;border-radius:8px;border-left:4px solid #28a745;'>
                    <p style='color:#666;font-size:11px;margin:0;'>Verschil</p>
                    <p style='color:#28a745;font-size:20px;font-weight:bold;margin:4px 0;'>{round(PAR_base - PAR_selected, 2)}</p></div>""",
                    unsafe_allow_html=True)
            else:
                PAR_base = (df_output["MSR totaal_base profile [kW]"].max()
                            / df_output["MSR totaal_base profile [kW]"].mean())
                k1, k2 = st.columns(2)
                k1.markdown(f"""<div style='background:#fff3cd;padding:14px;border-radius:8px;border-left:4px solid #ffc107;'>
                    <p style='color:#666;font-size:13px;margin:0;'>Piekvermogen (on-demand)</p>
                    <p style='color:#333;font-size:26px;font-weight:bold;margin:4px 0;'>{int(original_peak):,} kW</p></div>""",
                    unsafe_allow_html=True)
                k2.markdown(f"""<div style='background:#d1ecf1;padding:14px;border-radius:8px;border-left:4px solid #17a2b8;'>
                    <p style='color:#666;font-size:13px;margin:0;'>Peak-to-Average Ratio</p>
                    <p style='color:#333;font-size:26px;font-weight:bold;margin:4px 0;'>{round(PAR_base, 2)}</p></div>""",
                    unsafe_allow_html=True)


# --- Hoofd-layout: drie visuele kolommen ---
col_map, col_right = st.columns([1, 2.4], gap="medium")

with col_map:
    with st.container(border=True):
        render_map_panel()

with col_right:
    render_analysis_panel()
