# Written by: Michael Jenks
# Last update: 24/11/2025

import gspread
import requests
import folium

import altair as alt
import streamlit as st
import pandas as pd
import geopandas as gpd
import numpy as np
#import matplotlib.pyplot as plt


from google.oauth2.service_account import Credentials
from shapely import wkt, wkb
#from datetime import timedelta
from PIL import Image
from io import BytesIO
from folium.plugins import FastMarkerCluster, Geocoder

class BackgroundCode:

    def __init__(self):
        self.locations = {
            "Sporenburg": (52.373815, 4.945598),
            "Roelantstraat": (52.376836, 4.856632),
            "Vincent van Goghstraat": (52.349022, 4.888944),
        }
    
    def load_Gsheets(
            self, 
            Gsheet_ID="1p2HqiGGOKvuZfjxSTOIi_NBotnnCxq0_0UG8hZhbM0g"
            ):
        # Load service account info securely from Streamlit secrets
        
        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        creds = Credentials.from_service_account_info(st.secrets["google_service_account"], scopes=SCOPES)
        gc = gspread.authorize(creds)

        spreadsheet = gc.open_by_key(Gsheet_ID)

        return spreadsheet

    def get_sheet_dataframe(self, sheet_name, sheet):
        """Read a worksheet into a DataFrame."""
        try:
            worksheet = sheet.worksheet(sheet_name)
            data = worksheet.get_all_records()
            return pd.DataFrame(data)
        except gspread.WorksheetNotFound:
            st.warning(f"Worksheet '{sheet_name}' not found.")
            return pd.DataFrame()

    @st.cache_data(ttl=2_592_000, show_spinner="Sheet laden...")
    def get_sheet_dataframe(_self, sheet_name, _sheet):
        """Read a worksheet into a DataFrame.

        Cached for 30 days. _self and _sheet are prefixed with an underscore
        so Streamlit skips them when computing the cache key — the cache is
        keyed only on sheet_name. Clear via st.cache_data.clear() or the
        'Data Verversen' button.
        """
        try:
            worksheet = _sheet.worksheet(sheet_name)
            data = worksheet.get_all_records()
            return pd.DataFrame(data)
        except gspread.WorksheetNotFound:
            st.warning(f"Worksheet '{sheet_name}' not found.")
            return pd.DataFrame()
        
    # --- Build GeoDataFrames ---
    @staticmethod
    @st.cache_resource
    def build_gebruik_df(_df):
        col_list = [
            "owner_msr",
            "jvb_industrie",
            "jvb_logies",
            "jvb_onderwijs",
            "jvb_winkel",
            "jvb_woon",
            "jvb_kantoor_gezondheid",
            "jvb_sport_bijeenkomst_overig",
            "percentage_evs_msr",
            "aantal_personenautos_msr"
        ]
        output_df = _df[col_list].copy()
        return output_df

    

    @staticmethod
    @st.cache_resource
    def build_msr_gdf(_df: pd.DataFrame) -> gpd.GeoDataFrame:

        def to_geometry(val):
            if pd.isna(val) or val == "":
                return None
            # Try WKT first
            if isinstance(val, str):
                try:
                    return wkt.loads(val)
                except Exception:
                    pass
                # Try WKB hex string
                try:
                    return wkb.loads(val, hex=True)
                except Exception:
                    print(f"Invalid geometry skipped: {val}")
                    return None
            # Already a Shapely geometry?
            from shapely.geometry.base import BaseGeometry
            if isinstance(val, BaseGeometry):
                return val
            return None

        _df["msr_coordinates"] = _df["msr_coordinates"].apply(to_geometry)

        # Filter out rows that couldn't be converted, optional
        #_df = _df[_df["msr_coordinates"].notna()]

        return gpd.GeoDataFrame(_df, geometry="msr_coordinates", crs="EPSG:28992")

    @staticmethod
    @st.cache_resource
    
    def build_vbo_gdf(_df: pd.DataFrame, col_name: str) -> gpd.GeoDataFrame:
        
        def to_geometry(val):
            if pd.isna(val) or val.strip() == "":
                return None
            # Try WKT
            if isinstance(val, str):
                try:
                    return wkt.loads(val)
                except Exception:
                    # Try WKB hex
                    try:
                        return wkb.loads(val, hex=True)
                    except Exception:
                        print(f"Invalid geometry skipped: {val}")
                        return None
            # Already a Shapely geometry?
            from shapely.geometry.base import BaseGeometry
            if isinstance(val, BaseGeometry):
                return val
            return None

        _df[col_name] = _df[col_name].apply(to_geometry)
        
        # Optionally remove rows that couldn't be converted
        _df = _df[_df[col_name].notna()]

        return gpd.GeoDataFrame(_df, geometry=col_name, crs="EPSG:28992")
    
    # --- Build map fresh each run (not cached) ---
    def build_base_map(self, _gdf):
        gdf_wgs = _gdf.to_crs(epsg=4326)
        m = folium.Map(location=[gdf_wgs.geometry.y.mean(), gdf_wgs.geometry.x.mean()], zoom_start=7)
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
        return m
    
    def profile_creator(self, df_profiles, msr_row, EV_adoption_perc, EV_jvb_per_auto):
        #import inspect
        #st.write("Function called from:")
        #st.write(inspect.stack()[1])

        df_MSR_profile = pd.DataFrame()
        #msr_row = df_MSRs[df_MSRs['owner_msr'] == MSR_ID]
        if len(msr_row.index) is not 1:
            st.write("Error in MSR matches")

        df_MSR_profile["DATUM_TIJDSTIP_2024"] = df_profiles["DATUM_TIJDSTIP_2024"].copy()

        df_MSR_profile["Woningen totaal [kW]"] = df_profiles["jvb_woon"].copy()*msr_row["jvb_woon"].iloc[0]*4
        df_MSR_profile["Winkel [kW]"] = df_profiles["jvb_winkel"].copy()*msr_row["jvb_winkel"].iloc[0]*4
        df_MSR_profile["Onderwijs [kW]"] = df_profiles["jvb_onderwijs"].copy()*msr_row["jvb_onderwijs"].iloc[0]*4
        df_MSR_profile["Logies [kW]"] = df_profiles["jvb_logies"].copy()*msr_row["jvb_logies"].iloc[0]*4
        df_MSR_profile["Industrie [kW]"] = df_profiles["jvb_industrie"].copy()*msr_row["jvb_industrie"].iloc[0]*4
        df_MSR_profile["Kantoor_Gezondsheid [kW]"] = df_profiles["jvb_kantoor_gezondheid"].copy()*msr_row["jvb_kantoor_gezondheid"].iloc[0]*4
        df_MSR_profile["Sport_Bijeenkomst_Overig [kW]"] = df_profiles["jvb_sport_bijeenkomst_overig"].copy()*msr_row["jvb_sport_bijeenkomst_overig"].iloc[0]*4

        # EV and solar
        df_MSR_profile["EV oplaad [kW]"] = df_profiles["Elaad_normal_norm. [kWh/kWh]"].copy()*msr_row["aantal_personenautos_msr"].iloc[0]*EV_adoption_perc/100*EV_jvb_per_auto*4 # (KWh per EV per year)
        msr_row["jaaropwek_pv"] = np.where(msr_row["jaaropwek_pv"].isnan(), msr_row["n_objecten"]*0.904*900, msr_row["jaaropwek_pv"]) ##### Tijdelijke oplossing missende waardes Utrecht
        df_MSR_profile["Zonnepanelen [kW]"] = -df_profiles["ZP normalised energy [kWh/kWh]"].copy()*msr_row["jaaropwek_pv"].iloc[0]*4
        
        df_MSR_profile["Utiliteit totaal [kW]"] = df_MSR_profile["Winkel [kW]"] + df_MSR_profile["Onderwijs [kW]"] + df_MSR_profile["Kantoor_Gezondsheid [kW]"] + df_MSR_profile["Industrie [kW]"] + df_MSR_profile["Sport_Bijeenkomst_Overig [kW]"] + df_MSR_profile["Logies [kW]"]
        
        df_MSR_profile["MSR totaal [kW]"] = df_MSR_profile["Woningen totaal [kW]"] + df_MSR_profile["Utiliteit totaal [kW]"] + df_MSR_profile["EV oplaad [kW]"] + df_MSR_profile["Zonnepanelen [kW]"] #+ df_MSR_profile["Oplaad punten [kW]"]
        df_MSR_profile["MSR totaal_base profile [kW]"] = df_MSR_profile["MSR totaal [kW]"]
        df_MSR_profile["DATUM_TIJDSTIP_2024"] = pd.to_datetime(df_MSR_profile["DATUM_TIJDSTIP_2024"], dayfirst=True)

        return df_MSR_profile
    
    def update_charge_strat(self, df, charge_strat, df_profiles, msr_row, EV_adoption_perc, EV_jvb_per_auto):
        charge_profile_name = self.charge_profile_lookup(charge_strat)

        #msr_row = df_MSRs[df_MSRs['owner_msr'] == MSR_ID]

        # this data still to be added to gsheets
        #df["Oplaad punten [kW]"] = df_profiles[charge_profile_name].copy()*msr_row["jvb_EV"]*4
        df["EV oplaad [kW]"] = df_profiles[charge_profile_name].copy()*msr_row["aantal_personenautos_msr"].iloc[0]*EV_adoption_perc/100*EV_jvb_per_auto*4
        df["MSR totaal [kW]"] = df["Woningen totaal [kW]"] + df["Utiliteit totaal [kW]"] + df["EV oplaad [kW]"] + df["Zonnepanelen [kW]"] 
   

        return df

    def charge_profile_lookup(self, charge_strat):
        
        if charge_strat == "Regular on-demand charging":
            #prof_name = "Charge point energy_normalised [kWh/kWh]"
            prof_name = "Elaad_normal_norm. [kWh/kWh]"
        
        if charge_strat == "Grid-aware smart charging":
            prof_name = "Elaad_net_bewust_norm. [kWh/kWh]"

        if charge_strat == "Capacity pooling":
            prof_name = "Elaad_cap_pooling_norm. [kWh/kWh]"

        if charge_strat == "V2G":
            prof_name = "Elaad_V2G_norm. [kWh/kWh]"

        return prof_name
    
    def prepare_plot_df(self, start_date, end_date, df):
        mask = (df["DATUM_TIJDSTIP_2024"] >= pd.to_datetime(start_date)) & (df["DATUM_TIJDSTIP_2024"] <= pd.to_datetime(end_date))
        
        df_slice = df.loc[mask]

        # --- add to cols to plot ---
        cols_to_plot = [
            "Woningen totaal [kW]",
            "Utiliteit totaal [kW]",
            "Zonnepanelen [kW]",
            "EV oplaad [kW]",
            "MSR totaal [kW]"
        ]
        
        # --- store into session_state
        st.session_state["df_plot_data"] = df_slice.set_index("DATUM_TIJDSTIP_2024")[cols_to_plot]

    def plot_df_with_dashed_lines(
            self,
            df,
            placeholder,
            dashed_series = [
                "EV oplaad [kW]",
                "Utiliteit totaal [kW]",
                "Woningen totaal [kW]",
                "Zonnepanelen [kW]"
            ],
            max_base_profile=None
        ):
        if df is None or df.empty:
            placeholder.write("No data to plot.")
            return

        legend_order = [
            "MSR totaal [kW]",
            "Woningen totaal [kW]",
            "Utiliteit totaal [kW]",
            "EV oplaad [kW]",
            "Zonnepanelen [kW]"
        ]
        
        # Reset index safely
        df_reset = df.reset_index()

        # Identify the index column (the column added by reset_index)
        index_col = df_reset.columns[0]

        # Ensure datetime index is treated correctly
        df_reset[index_col] = pd.to_datetime(df_reset[index_col])

        # Convert to long format
        df_long = df_reset.melt(
            id_vars=index_col,
            var_name="series",
            value_name="value"
        )

        # Build main line chart
        chart = (
            alt.Chart(df_long)
            .mark_line()
            .encode(
                x=alt.X(index_col + ":T", title="Date"),
                y=alt.Y("value:Q", title="Power [kW]"),
                color=alt.Color(
                    "series:N",
                    title="",
                    scale=alt.Scale(domain=legend_order),
                    sort=legend_order
                ),
                strokeDash=alt.condition(
                    alt.FieldOneOfPredicate(field="series", oneOf=dashed_series),
                    alt.value([4, 4]),       # dashed style
                    alt.value([1, 0])        # solid style
                ),
                strokeWidth=alt.condition(
                    alt.FieldOneOfPredicate(field="series", oneOf=dashed_series),
                    alt.value(1),            # thinner dashed lines
                    alt.value(2.5)           # thicker solid lines
                )
            )
        )
        
        # Add horizontal red line for max base profile if provided
        if max_base_profile is not None:
            rule = alt.Chart(pd.DataFrame({'y': [max_base_profile]})).mark_rule(
                color='red',
                strokeDash=[5, 5],
                strokeWidth=2
            ).encode(
                y='y:Q'
            )
            
            # Add text annotation for the line
            text = alt.Chart(pd.DataFrame({
                'y': [max_base_profile],
                'label': [f'Max standaard: {int(max_base_profile)} kW']
            })).mark_text(
                align='right',
                dx=-5,
                dy=-5,
                color='red',
                fontSize=11,
                fontWeight='bold'
            ).encode(
                x=alt.value(0),  # Position at the left
                y='y:Q',
                text='label:N'
            )
            
            # Combine all layers
            chart = (chart + rule + text).properties(
                padding={"bottom": 40}
            )
        else:
            chart = chart.properties(
                padding={"bottom": 40}
            )

        # Render chart
        placeholder.altair_chart(chart, use_container_width=True)

    @staticmethod
    @st.cache_resource
    def image_converter(URL, R, G, B, A, width=None):
        response = requests.get(URL)
        
        try:
            image = Image.open(BytesIO(response.content)).convert("RGBA")
            background = Image.new("RGBA", image.size, (R, G, B, A))
            background.paste(image, (0,0), image)
            final_image = background.convert("RGB")

            if width:
                w, h = final_image.size
                ratio = width / w
                new_height = int(h * ratio)
                final_image = final_image.resize((width, new_height), Image.LANCZOS)

            return final_image
        
        except:
            return None
        
    def load_room_objects(self, room_id):
        """Load objects associated with a specific voltage room"""
        try:
            conn = st.connection("postgresql", type="sql")
    
            # Perform query.
            objects_df = conn.query('SELECT * FROM Objectsmichael;', ttl="10m")

            # Handle the unnamed index column if it exists
            if '' in objects_df.columns or 'Unnamed: 0' in objects_df.columns:
                objects_df = objects_df.drop(columns=[col for col in objects_df.columns if col == '' or col.startswith('Unnamed')])
            return objects_df
        except Exception as e:
            st.warning(f"Could not load objects for room {room_id}: {e}")
            return None

    def load_room_objects2(self, selected_msr, table_name):
        """Load objects associated with a specific voltage room"""
        
        conn = st.connection("postgresql", type="sql")

        objects_df = conn.query(
            f"""
            SELECT *
            FROM {table_name}
            WHERE owner_msr = :msr
            """,
            params={"msr": selected_msr},
            ttl="10m"
        )

        # Perform query.
        #objects_df = conn.query('SELECT * FROM "ObjectsMichael";', ttl="10m")

        # Handle the unnamed index column if it exists
        if '' in objects_df.columns or 'Unnamed: 0' in objects_df.columns:
            objects_df = objects_df.drop(columns=[col for col in objects_df.columns if col == '' or col.startswith('Unnamed')])
        return objects_df
    
    def test_connection(self):

        conn = st.connection("postgresql", type="sql")

        test_output = conn.query(
            """
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE tablename ILIKE '%michael%';
            """
        )

        return test_output
if __name__ == "__main__":
    loaded = load_Gsheets()
