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


@st.cache_data
def load_price_data():
    """Load Prijzen2024Geheel.csv and return deduplicated hourly price DataFrame.

    The CSV has tz-aware timestamps (e.g. +01:00). We strip the timezone by
    formatting each timestamp as its local-time string and re-parsing as tz-naive,
    so the result matches the tz-naive MSR profile timestamps without needing pytz.
    """
    try:
        df = pd.read_csv("Prijzen2024Geheel.csv", index_col=0)
        ts = pd.to_datetime(df["DATUM_TIJDSTIP_2024"])
        # strftime on tz-aware Series formats in the timezone of each timestamp
        # (local Amsterdam time) → re-parse as tz-naive to match MSR profile
        df["DATUM_TIJDSTIP_2024"] = pd.to_datetime(ts.dt.strftime("%Y-%m-%d %H:%M:%S"))
        # CSV has 4 identical rows per hour; keep one per unique timestamp
        df = df.drop_duplicates(subset="DATUM_TIJDSTIP_2024", keep="first").reset_index(drop=True)
        return df
    except Exception as e:
        st.session_state["_price_load_error"] = str(e)
        return None


def prepare_price_for_merge(price_df, msr_df):
    """Align hourly price data to the 15-min MSR timestamps via hour-floor join."""
    msr_ts = pd.to_datetime(msr_df["DATUM_TIJDSTIP_2024"])
    lookup = pd.DataFrame({
        "DATUM_TIJDSTIP_2024": msr_df["DATUM_TIJDSTIP_2024"].values,
        "hour_ts": msr_ts.dt.floor("h").values,
    })
    price_copy = price_df.rename(columns={"DATUM_TIJDSTIP_2024": "hour_ts"})
    aligned = lookup.merge(price_copy[["hour_ts", "price_eur_per_kwh"]], on="hour_ts", how="left")
    aligned["price_eur_per_kwh"] = aligned["price_eur_per_kwh"].fillna(
        aligned["price_eur_per_kwh"].mean()
    )
    return aligned[["DATUM_TIJDSTIP_2024", "price_eur_per_kwh"]]


def _store_plot_data(start_date, end_date, df_output, df_bat=None):
    """Build and store the complete plot DataFrame in one step.

    Uses df_output for the five base series and optionally joins battery columns
    from df_bat on the shared DatetimeIndex.  Using join() instead of concat()
    avoids the alignment issues that can occur with a two-step approach.
    """
    _BASE_COLS = [
        "Woningen totaal [kW]",
        "Utiliteit totaal [kW]",
        "Zonnepanelen [kW]",
        "EV oplaad [kW]",
        "MSR totaal [kW]",
    ]
    mask = (
        (df_output["DATUM_TIJDSTIP_2024"] >= pd.to_datetime(start_date))
        & (df_output["DATUM_TIJDSTIP_2024"] <= pd.to_datetime(end_date))
    )
    result = df_output.loc[mask].set_index("DATUM_TIJDSTIP_2024")[_BASE_COLS].copy()

    if df_bat is not None:
        bat_mask = (
            (df_bat["DATUM_TIJDSTIP_2024"] >= pd.to_datetime(start_date))
            & (df_bat["DATUM_TIJDSTIP_2024"] <= pd.to_datetime(end_date))
        )
        bat_slice = df_bat.loc[bat_mask].set_index("DATUM_TIJDSTIP_2024")[
            ["MSR totaal met batterij [kW]", "Batterij vermogen [kW]"]
        ]
        result = result.join(bat_slice, how="left")

    st.session_state["df_plot_data"] = result


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
            st.session_state.cached_df = bg.load_room_objects2(new_id, "datamichael06mei26")
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
    """Kolom 2: MSR-naam + invoer. Kolom 3: grafiek + KPIs."""
    EV_jvb_per_auto = 3500
    df_output = None
    df_with_battery = None
    charge_strat = "Regulier on-demand laden"
    EV_adoption_perc = 0
    battery_enabled = False
    battery_kwh_val = 200
    battery_strategy = "peak_reduction"

    col_inputs, col_chart = st.columns([1, 1.5], gap="medium")

    # ------------------------------------------------------------------ #
    #  Kolom 2 – MSR-naam en invoerwidgets                                #
    # ------------------------------------------------------------------ #
    with col_inputs:
        with st.container(border=True):
            if not st.session_state.selected_id:
                st.info("👈 Klik op een MSR op de kaart.")
            else:
                msr_row = st.session_state.cached_df

                # MSR-naam / adres
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

                st.markdown("---")

                if len(msr_row) == 0:
                    st.warning("Geen data beschikbaar voor dit MSR.")
                else:
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
                            st.info("⚠️ EV data niet beschikbaar, standaard 25%")
                    except Exception as e:
                        EV_perc_current = 25
                        st.warning(f"Fout bij EV%: {e}")

                    EV_adoption_perc = st.slider(
                        f"EV-adoptie (huidig: {EV_perc_current}%)",
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

                    if st.button("📈 Hoogste piekvermogen"):
                        st.session_state.date_max_power = df_output.loc[
                            df_output["MSR totaal [kW]"].idxmax(), "DATUM_TIJDSTIP_2024"
                        ]
                        st.session_state.min_max = "max"

                    if st.button("📉 Laagste piekvermogen"):
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

                    st.markdown("---")
                    st.markdown("**🔋 Batterijopslag**")

                    battery_enabled = st.checkbox("Batterij inschakelen", key="battery_enabled")
                    if battery_enabled:
                        _STRATEGY_LABELS = {
                            "self_consumption": "Zelf benutting zonnestroom",
                            "price_optimization": "Prijsoptimalisatie",
                            "peak_reduction": "Piekvermindering",
                        }
                        battery_strategy = st.selectbox(
                            "Batterijstrategie",
                            list(_STRATEGY_LABELS.keys()),
                            format_func=lambda x: _STRATEGY_LABELS[x],
                            key="battery_strategy",
                        )
                        battery_kwh_val = st.slider(
                            "Batterijcapaciteit (kWh)", 50, 1000, 200, step=50, key="battery_kwh"
                        )

                        price_for_bat = None
                        if battery_strategy == "price_optimization":
                            raw_prices = load_price_data()
                            if raw_prices is None:
                                err = st.session_state.get("_price_load_error", "onbekende fout")
                                st.warning(f"⚠️ Prijsdata kon niet worden geladen: {err}")
                                battery_enabled = False
                            else:
                                price_for_bat = prepare_price_for_merge(raw_prices, df_output)

                        if battery_enabled:
                            with st.spinner("Batterijsimulatie berekenen..."):
                                try:
                                    df_with_battery = bg.battery_optimizer(
                                        df_output, battery_strategy, battery_kwh_val, price_for_bat
                                    )
                                except Exception as e:
                                    st.warning(f"⚠️ Batterijberekening mislukt: {e}")
                                    battery_enabled = False

                    _bat_for_plot = df_with_battery if battery_enabled else None
                    date_range = (end_date - start_date).days
                    if date_range <= 10:
                        _store_plot_data(start_date, end_date, df_output, _bat_for_plot)
                    elif st.session_state.awaiting_confirmation:
                        _store_plot_data(start_date, end_date, df_output, _bat_for_plot)
                        st.session_state.awaiting_confirmation = False
                    else:
                        st.warning(f"Periode: {date_range} dagen — kan traag zijn.")
                        if st.button("Ja, doorgaan"):
                            st.session_state.awaiting_confirmation = True

    # ------------------------------------------------------------------ #
    #  Kolom 3 – grafiek en KPIs                                          #
    # ------------------------------------------------------------------ #
    with col_chart:
        with st.container(border=True):
            if not st.session_state.selected_id or df_output is None:
                st.info("Selecteer een MSR en datumbereik om de grafiek te tonen.")
            else:
                msr_row = st.session_state.cached_df
                original_peak = st.session_state.original_peak_power

                if st.session_state.df_plot_data is not None:
                    # Batterij vermogen als gestippelde lijn zodat het visueel onderscheiden is
                    dashed = [
                        "EV oplaad [kW]",
                        "Utiliteit totaal [kW]",
                        "Woningen totaal [kW]",
                        "Zonnepanelen [kW]",
                        "Batterij vermogen [kW]",
                    ]
                    bg.plot_df_with_dashed_lines(
                        st.session_state.df_plot_data,
                        st.empty(),
                        dashed_series=dashed,
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

                # ---- Batterij KPI's (alleen zichtbaar als batterij aan staat) ----
                if battery_enabled and df_with_battery is not None:
                    st.markdown("---")
                    st.markdown("**🔋 Batterij KPI's**")

                    bat_peak = df_with_battery["MSR totaal met batterij [kW]"].max()
                    bat_peak_reduction = original_peak - bat_peak

                    # Jaarlijkse cycli: totale ontladen energie / capaciteit
                    discharged_kwh = (
                        df_with_battery.loc[
                            df_with_battery["Batterij vermogen [kW]"] < 0,
                            "Batterij vermogen [kW]",
                        ].abs().sum() * 0.25  # × DT (15 min → uur)
                    )
                    annual_cycles = discharged_kwh / battery_kwh_val

                    # Benutting: gemiddeld absoluut batterijvermogen vs. max vermogen
                    max_bat_power = 0.25 * battery_kwh_val
                    utilisation_pct = (
                        df_with_battery["Batterij vermogen [kW]"].abs().mean() / max_bat_power * 100
                        if max_bat_power > 0 else 0
                    )

                    kb1, kb2, kb3 = st.columns(3)
                    kb1.markdown(
                        f"""<div style='background:#d4edda;padding:12px;border-radius:8px;border-left:4px solid #28a745;'>
                        <p style='color:#666;font-size:11px;margin:0;'>Nieuwe piekbelasting</p>
                        <p style='color:#28a745;font-size:20px;font-weight:bold;margin:4px 0;'>{int(bat_peak):,} kW</p></div>""",
                        unsafe_allow_html=True,
                    )
                    kb2.markdown(
                        f"""<div style='background:#d1ecf1;padding:12px;border-radius:8px;border-left:4px solid #17a2b8;'>
                        <p style='color:#666;font-size:11px;margin:0;'>Piekvermindering batterij</p>
                        <p style='color:#17a2b8;font-size:20px;font-weight:bold;margin:4px 0;'>{int(bat_peak_reduction):,} kW</p></div>""",
                        unsafe_allow_html=True,
                    )
                    kb3.markdown(
                        f"""<div style='background:#f0f2f6;padding:12px;border-radius:8px;border-left:4px solid #6c757d;'>
                        <p style='color:#666;font-size:11px;margin:0;'>Cycli/jaar · Benutting</p>
                        <p style='color:#333;font-size:20px;font-weight:bold;margin:4px 0;'>{annual_cycles:.1f} · {utilisation_pct:.0f}%</p></div>""",
                        unsafe_allow_html=True,
                    )


# --- Hoofd-layout: kaart links, analyse rechts (intern gesplitst in 2 kolommen) ---
col_map, col_right = st.columns([1, 2.5], gap="medium")

with col_map:
    with st.container(border=True):
        render_map_panel()

with col_right:
    render_analysis_panel()
