import folium
import codecs
import background_code
import streamlit as st
import pandas as pd
import geopandas as gpd

from folium.plugins import FastMarkerCluster, Geocoder
from shapely import wkb
from datetime import timedelta, datetime
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

st.set_page_config(layout="wide")

st.title("Modelleeromgeving voor alle middenspanningsstations in Nederland")
st.write("Selecteer het MSR dat je wilt analyseren.")
st.write("Voor vragen of opmerkingen, neem contact op met m.j.f.jenks@hva.nl")

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

# --- Session state ---
if "selected_id" not in st.session_state:
    st.session_state.selected_id = None

if "map_center" not in st.session_state:
    st.session_state.map_center = None

if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 7

# Store original peak power (before EV adoption changes)
if "original_peak_power" not in st.session_state:
    st.session_state.original_peak_power = None

# Function to get address from coordinates
@st.cache_data
def get_address_from_coords(lat, lon):
    """Reverse geocode coordinates to get address"""
    try:
        geolocator = Nominatim(user_agent="msr_app")
        location = geolocator.reverse(f"{lat}, {lon}", timeout=10, language='nl')
        if location and location.address:
            # Extract street and city from address
            addr_parts = location.raw.get('address', {})
            street = addr_parts.get('road', '')
            house_number = addr_parts.get('house_number', '')
            city = addr_parts.get('city') or addr_parts.get('town') or addr_parts.get('village', '')
            
            # Build clean address
            if street and city:
                if house_number:
                    return f"{street} {house_number}, {city}"
                else:
                    return f"{street}, {city}"
            elif city:
                return city
            else:
                return location.address.split(',')[0]  # First part of full address
        return None
    except:
        return None

# Build map
if st.session_state.map_center:
    m = folium.Map(
        location=st.session_state.map_center, 
        zoom_start=st.session_state.map_zoom
    )
    
    # Add orange marker for searched location
    folium.CircleMarker(
        location=st.session_state.map_center,
        radius=10,
        color='orange',
        fill=True,
        fill_color='orange',
        fill_opacity=0.7,
        popup="Gezocht adres",
        tooltip="Gezocht adres"
    ).add_to(m)
    
    # Add MSR markers
    gdf_wgs = msr_gdf.to_crs(epsg=4326)
    callback = """
    function (row) {
        var marker = L.marker(new L.LatLng(row[0], row[1]));
        marker.bindPopup(String(row[2]));
        marker.bindTooltip(String(row[2]));
        return marker;
    }
    """
    coords = list(zip(gdf_wgs.geometry.y, gdf_wgs.geometry.x, gdf_wgs["owner_msr"]))
    FastMarkerCluster(coords, callback=callback).add_to(m)
else:
    m = bg.build_base_map(msr_gdf)

# Add Geocoder plugin to the map
Geocoder(
    collapsed=False,
    position='topleft',
    placeholder='Zoek een adres in Nederland...',
).add_to(m)

# --- Create grid layout ---
left_col, right_col = st.columns([1, 1])

with left_col:
    # Reset button (optional - if you want to reset the map view)
    if st.button("🔄 Reset kaartweergave"):
        st.session_state.map_center = None
        st.session_state.map_zoom = 7
        st.rerun()
    
    st.markdown("---")
    
    map_data = st_folium(
        m,
        width="100%",
        height=600,
        key="main_map",
    )

    if map_data.get("last_object_clicked_tooltip"):
        st.session_state.selected_id = map_data["last_object_clicked_tooltip"]
    
    if "last_msr_id" not in st.session_state:
        st.session_state.last_msr_id = None
        st.session_state.cached_df = None

    current_id = st.session_state.get("selected_id")

    if current_id != st.session_state.last_msr_id:
        st.session_state.cached_df = bg.load_room_objects2(
            current_id,
            "datamichael13april26"
        )
        st.session_state.last_msr_id = current_id

    #st.dataframe(st.session_state.cached_df)
    def parse_wkb(val):
        if isinstance(val, str):
            if val.startswith("\\x"):
                val = val[2:]
            return wkb.loads(bytes.fromhex(val))
        return wkb.loads(val)

    if st.session_state.selected_id:
        df = st.session_state.cached_df.copy()
        df["geometry"] = df["vbo_points"].apply(parse_wkb)
        houses_gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:28992")

        selected_houses = houses_gdf[
            houses_gdf["owner_msr"].astype(int).astype(str) == str(st.session_state.selected_id)
        ].to_crs(epsg=4326)

        if len(selected_houses) > 0:
            house_map = folium.Map(
                location=[
                    selected_houses.geometry.centroid.y.mean(),
                    selected_houses.geometry.centroid.x.mean()
                ],
                zoom_start=17
            )
            for geom in selected_houses.geometry:
                if geom.geom_type == "Point":
                    points = [geom]
                else:
                    points = geom.geoms

                for point in points:
                    folium.CircleMarker(
                        location=[point.y, point.x],
                        radius=5,
                        color="red",
                        fill=True,
                        fill_opacity=0.8,
                    ).add_to(house_map)

            st.subheader(f"Gebouwen aangesloten op MSR: {st.session_state.selected_id}")
            st_folium(house_map, width="100%", height=400, key="house_map")
        else:
            st.warning("Geen gebouwen gevonden voor dit MSR.")
        
    HvA_logo_url = "https://amsterdamgreencampus.nl/wp-content/uploads/2016/01/AmsUniOfAppSci.png"
    img = bg.image_converter(HvA_logo_url, 255, 255, 255, 255, 200)

    if img is not None:
        st.image(img)

with right_col:
    if st.session_state.selected_id:
        # Try to get MSR location from the geodataframe
        try:
            # Filter msr_gdf to find the selected MSR
            selected_msr = msr_gdf[msr_gdf['owner_msr'].astype(str) == str(st.session_state.selected_id)]
            
            if len(selected_msr) > 0:
                # Convert to WGS84 for lat/lon
                msr_wgs84 = selected_msr.to_crs(epsg=4326)
                msr_lat = msr_wgs84.iloc[0].geometry.y
                msr_lon = msr_wgs84.iloc[0].geometry.x
                
                # Get address from coordinates
                msr_address = get_address_from_coords(msr_lat, msr_lon)
                
                if msr_address:
                    st.subheader(f"MSR: {msr_address}")
                    st.caption(f"ID: {st.session_state.selected_id} | Coördinaten: {msr_lat:.4f}, {msr_lon:.4f}")
                else:
                    st.subheader(f"MSR: {st.session_state.selected_id}")
                    st.caption(f"Coördinaten: {msr_lat:.4f}, {msr_lon:.4f}")
            else:
                st.subheader(f"MSR: {st.session_state.selected_id}")
        except Exception as e:
            # Fallback if geocoding fails
            st.subheader(f"MSR: {st.session_state.selected_id}")
            st.caption(f"(Adres kon niet worden opgehaald)")

        msr_row = st.session_state.cached_df
        EV_jvb_per_auto = 3500

        if len(msr_row) > 0:
            charge_strat = st.selectbox(
                "Welke laadstrategie wil je toepassen?",
                ("Regulier on-demand laden", "Netbewust slim laden", "Capaciteitspooling", "V2G"),
                key="charge_strategy"
            )
            
            # Map Dutch to English for internal use
            charge_strat_map = {
                "Regulier on-demand laden": "Regular on-demand charging",
                "Netbewust slim laden": "Grid-aware smart charging",
                "Capaciteitspooling": "Capacity pooling",
                "V2G": "V2G"
            }
            charge_strat_en = charge_strat_map[charge_strat]

            try:
                if "aantal_evs_m_msr" in msr_row.columns and "aantal_personenautos_msr" in msr_row.columns:
                    num_evs = msr_row["aantal_evs_m_msr"].iloc[0]
                    num_cars = msr_row["aantal_personenautos_msr"].iloc[0]
                    if num_cars > 0:
                        EV_perc_current = int(num_evs * 100 / num_cars)
                    else:
                        EV_perc_current = 0
                else:
                    EV_perc_current = 25
                    st.info("⚠️ EV data niet beschikbaar, standaard waarde van 25% wordt gebruikt")
            except Exception as e:
                EV_perc_current = 25
                st.warning(f"Fout bij berekenen EV percentage: {e}. Standaard 25% wordt gebruikt.")
            
            EV_adoption_perc = st.slider("Welk percentage EV-adoptie wil je modelleren?", EV_perc_current, 100, EV_perc_current)

            df_output = bg.profile_creator(profielen_df, msr_row, EV_adoption_perc, EV_jvb_per_auto)
            df_output = bg.update_charge_strat(df_output, charge_strat_en, profielen_df, msr_row, EV_adoption_perc, EV_jvb_per_auto)
            
            # Store original peak power when first loading this MSR (at current EV percentage)
            if st.session_state.original_peak_power is None or st.session_state.get('last_loaded_msr') != st.session_state.selected_id:
                st.session_state.original_peak_power = df_output["MSR totaal_base profile [kW]"].max()
                st.session_state.last_loaded_msr = st.session_state.selected_id

            if "min_max" not in st.session_state:
                st.session_state.min_max = "-"

            if st.button("Verander naar dag met hoogste piekvermogen"):
                date_max_power = df_output.loc[df_output["MSR totaal [kW]"].idxmax(), ("DATUM_TIJDSTIP_2024")]
                st.session_state.date_max_power = date_max_power
                st.session_state.min_max = "max"

            if st.button("Verander naar dag met laagste (of meest negatieve) piekvermogen"):
                date_min_power = df_output.loc[df_output["MSR totaal [kW]"].idxmin(), ("DATUM_TIJDSTIP_2024")]
                st.session_state.date_min_power = date_min_power
                st.session_state.min_max = "min"

            min_date = df_output["DATUM_TIJDSTIP_2024"].min().date()
            max_date = df_output["DATUM_TIJDSTIP_2024"].max().date()
            default_start = min_date

            if "min_max" in st.session_state:
                if st.session_state.min_max == "max" and "date_max_power" in st.session_state:
                    default_start = st.session_state.date_max_power
                elif st.session_state.min_max == "min" and "date_min_power" in st.session_state:
                    default_start = st.session_state.date_min_power

            if isinstance(default_start, pd.Timestamp):
                default_start = default_start.date()

            default_start = min(max(default_start, min_date), max_date)

            start_date = st.date_input("Startdatum", default_start, min_value=min_date, max_value=max_date)
            end_date = st.date_input("Einddatum", start_date + timedelta(days=1), min_value=start_date + timedelta(days=1), max_value=max_date)

            date_range = (end_date - start_date).days

            if "awaiting_confirmation" not in st.session_state:
                st.session_state.awaiting_confirmation = False

            if date_range <= 10:
                bg.prepare_plot_df(start_date, end_date, df_output)
            else:
                if not st.session_state.awaiting_confirmation:
                    st.warning(f"Je hebt een lange periode geselecteerd: {date_range} dagen.")
                    st.info("Dit kan traag zijn. Wil je doorgaan?")
                    if st.button("Ja, doorgaan"):
                        st.session_state.awaiting_confirmation = True
                    else:
                        st.stop()
                if st.session_state.awaiting_confirmation:
                    bg.prepare_plot_df(start_date, end_date, df_output)
                    st.session_state.awaiting_confirmation = False

            plot_placeholder = st.empty()

            if "df_plot_data" not in st.session_state:
                st.session_state["df_plot_data"] = None

            # Use original peak power for the red line
            original_peak = st.session_state.original_peak_power
            
            if st.session_state["df_plot_data"] is not None:
                bg.plot_df_with_dashed_lines(
                    st.session_state["df_plot_data"], 
                    plot_placeholder,
                    max_base_profile=original_peak
                )
            else:
                st.write("Nog geen grafiek gegenereerd.")

            st.subheader("KPI's:")
            
            num_autos = int(msr_row["aantal_personenautos_msr"].iloc[0])
            
            # Only show number of cars KPI
            st.markdown(f"""
            <div style='background-color: #f0f2f6; padding: 15px; border-radius: 10px; margin-bottom: 10px;'>
                <p style='color: #666; font-size: 14px; margin: 0;'>Aantal auto's (waarvan {EV_perc_current}% EV)</p>
                <p style='color: #1f77b4; font-size: 28px; font-weight: bold; margin: 5px 0;'>{num_autos:,}</p>
            </div>
            """, unsafe_allow_html=True)
            
            peak_on_demand = df_output["MSR totaal_base profile [kW]"].max()
            
            if charge_strat != "Regulier on-demand laden":
                peak_selected_profile = df_output["MSR totaal [kW]"].max()
                PAR_on_demand = df_output["MSR totaal_base profile [kW]"].max()/df_output["MSR totaal_base profile [kW]"].mean()
                PAR_selected_profile = df_output["MSR totaal [kW]"].max()/df_output["MSR totaal [kW]"].mean()
                peak_reduction = original_peak - peak_selected_profile
                par_difference = PAR_on_demand - PAR_selected_profile

                st.markdown("**Piekvermogen**")
                kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
                
                with kpi_col1:
                    st.markdown(f"""
                    <div style='background-color: #fff3cd; padding: 15px; border-radius: 10px; border-left: 4px solid #ffc107;'>
                        <p style='color: #666; font-size: 12px; margin: 0;'>On-demand laden (origineel)</p>
                        <p style='color: #333; font-size: 24px; font-weight: bold; margin: 5px 0;'>{int(original_peak):,} kW</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col2:
                    st.markdown(f"""
                    <div style='background-color: #d1ecf1; padding: 15px; border-radius: 10px; border-left: 4px solid #17a2b8;'>
                        <p style='color: #666; font-size: 12px; margin: 0;'>Geselecteerd profiel</p>
                        <p style='color: #333; font-size: 24px; font-weight: bold; margin: 5px 0;'>{int(peak_selected_profile):,} kW</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col3:
                    st.markdown(f"""
                    <div style='background-color: #d4edda; padding: 15px; border-radius: 10px; border-left: 4px solid #28a745;'>
                        <p style='color: #666; font-size: 12px; margin: 0;'>Piekreductie</p>
                        <p style='color: #28a745; font-size: 24px; font-weight: bold; margin: 5px 0;'>{int(peak_reduction):,} kW</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.markdown("")
                
                st.markdown("**Peak-to-Average Ratio**")
                kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
                
                with kpi_col1:
                    st.markdown(f"""
                    <div style='background-color: #fff3cd; padding: 15px; border-radius: 10px; border-left: 4px solid #ffc107;'>
                        <p style='color: #666; font-size: 12px; margin: 0;'>On-demand laden</p>
                        <p style='color: #333; font-size: 24px; font-weight: bold; margin: 5px 0;'>{round(PAR_on_demand, 2)}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col2:
                    st.markdown(f"""
                    <div style='background-color: #d1ecf1; padding: 15px; border-radius: 10px; border-left: 4px solid #17a2b8;'>
                        <p style='color: #666; font-size: 12px; margin: 0;'>Geselecteerd profiel</p>
                        <p style='color: #333; font-size: 24px; font-weight: bold; margin: 5px 0;'>{round(PAR_selected_profile, 2)}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col3:
                    st.markdown(f"""
                    <div style='background-color: #d4edda; padding: 15px; border-radius: 10px; border-left: 4px solid #28a745;'>
                        <p style='color: #666; font-size: 12px; margin: 0;'>Verschil</p>
                        <p style='color: #28a745; font-size: 24px; font-weight: bold; margin: 5px 0;'>{round(par_difference, 2)}</p>
                    </div>
                    """, unsafe_allow_html=True)

            else:
                PAR_on_demand = df_output["MSR totaal_base profile [kW]"].max()/df_output["MSR totaal_base profile [kW]"].mean()

                kpi_col1, kpi_col2 = st.columns(2)
                
                with kpi_col1:
                    st.markdown(f"""
                    <div style='background-color: #fff3cd; padding: 20px; border-radius: 10px; border-left: 4px solid #ffc107;'>
                        <p style='color: #666; font-size: 14px; margin: 0;'>Piekvermogen (on-demand)</p>
                        <p style='color: #333; font-size: 32px; font-weight: bold; margin: 5px 0;'>{int(original_peak):,} kW</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with kpi_col2:
                    st.markdown(f"""
                    <div style='background-color: #d1ecf1; padding: 20px; border-radius: 10px; border-left: 4px solid #17a2b8;'>
                        <p style='color: #666; font-size: 14px; margin: 0;'>Peak-to-Average Ratio</p>
                        <p style='color: #333; font-size: 32px; font-weight: bold; margin: 5px 0;'>{round(PAR_on_demand, 2)}</p>
                    </div>
                    """, unsafe_allow_html=True)

    else:
        st.info("👈 Klik op een MSR punt op de kaart om hier details te zien.")
