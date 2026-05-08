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
        msr_row["jaaropwek_pv"] = np.where(msr_row["jaaropwek_pv"].isna(), msr_row["n_objecten"]*0.904*900, msr_row["jaaropwek_pv"]) ##### Tijdelijke oplossing missende waardes Utrecht
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
            max_base_profile=None,
            nbl_limit=None
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
            chart = chart + rule + text

        if nbl_limit is not None:
            nbl_rule = alt.Chart(pd.DataFrame({'y': [nbl_limit]})).mark_rule(
                color='orange',
                strokeWidth=2.5
            ).encode(y='y:Q')

            nbl_text = alt.Chart(pd.DataFrame({
                'y': [nbl_limit],
                'label': [f'Fysieke grens (NBL): {int(nbl_limit)} kW']
            })).mark_text(
                align='right',
                dx=-5,
                dy=-5,
                color='orange',
                fontSize=11,
                fontWeight='bold'
            ).encode(
                x=alt.value(0),
                y='y:Q',
                text='label:N'
            )
            chart = chart + nbl_rule + nbl_text

        chart = chart.properties(height=280, padding={"top": 10, "bottom": 0, "left": 5, "right": 5})

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

    def battery_optimizer(
        self,
        df: pd.DataFrame,
        strategy: str,
        battery_kwh: float = 0.0,
        price_df: pd.DataFrame = None,
        peak_percentile: float = 85.0,
        soc_init_fraction: float = 0.5,
    ) -> pd.DataFrame:
        """Simulate a battery on the MSR energy profile with one of three strategies.

        Parameters
        ----------
        df : DataFrame
            Output of profile_creator / update_charge_strat.
            Must contain 'DATUM_TIJDSTIP_2024' and 'MSR totaal [kW]'.
            Solar generation is negative in 'Zonnepanelen [kW]'.
        strategy : str
            'self_consumption'   – absorb excess solar, discharge to cover imports.
            'price_optimization' – charge cheap intervals, discharge expensive ones.
            'peak_reduction'     – shave peaks above peak_percentile, charge in valleys.
        battery_kwh : float
            Usable battery capacity in kWh (50–1000). C-rate fixed at 0.25.
        price_df : DataFrame, optional
            Required for 'price_optimization'. Columns:
              'DATUM_TIJDSTIP_2024'  – matching timestamps (15-min)
              'price_eur_per_kwh'    – electricity price per interval.
        peak_percentile : float
            Load percentile used as the peak shaving target (default 85).
        soc_init_fraction : float
            Starting SoC as fraction of battery_kwh (default 0.5).

        Returns
        -------
        DataFrame (copy of df) with added columns:
            'Batterij vermogen [kW]'       positive = charging, negative = discharging
            'Batterij SoC [kWh]'           state of charge over time
            'MSR totaal met batterij [kW]' net grid load after battery interaction
        """
        # --- Constants ---
        C_RATE = 0.25
        ETA_CHARGE = 0.96       # charging efficiency
        ETA_DISCHARGE = 0.96    # discharging efficiency
        DT = 0.25               # 15-minute interval in hours

        battery_kwh = float(np.clip(battery_kwh, 50, 1000))
        max_power = C_RATE * battery_kwh          # kW  (e.g. 200 kWh → 50 kW)
        soc_min = battery_kwh * 0.10
        soc_max = battery_kwh * 0.90

        df = df.copy().sort_values("DATUM_TIJDSTIP_2024").reset_index(drop=True)
        load = df["MSR totaal [kW]"].values.astype(float)

        # --- Pre-process price signal ---
        prices = np.zeros(len(df))
        daily_mean_prices = np.zeros(len(df))
        if strategy == "price_optimization":
            if price_df is None:
                raise ValueError("price_df is required for strategy='price_optimization'.")
            price_copy = price_df.copy()
            price_copy["DATUM_TIJDSTIP_2024"] = pd.to_datetime(price_copy["DATUM_TIJDSTIP_2024"])
            merged = df.merge(
                price_copy[["DATUM_TIJDSTIP_2024", "price_eur_per_kwh"]],
                on="DATUM_TIJDSTIP_2024",
                how="left",
            )
            prices = merged["price_eur_per_kwh"].fillna(merged["price_eur_per_kwh"].mean()).values
            # Daily mean price per interval — O(n), avoids inner loop
            dates = pd.to_datetime(df["DATUM_TIJDSTIP_2024"]).dt.date
            daily_mean_prices = (
                pd.Series(prices, index=df.index)
                .groupby(dates)
                .transform("mean")
                .values
            )

        # --- Peak threshold for peak_reduction ---
        positive_load = load[load > 0]
        peak_threshold = (
            float(np.percentile(positive_load, peak_percentile))
            if strategy == "peak_reduction" and len(positive_load) > 0
            else 0.0
        )
        # Charge only when load is in the bottom 30% — keeps battery ready for peaks
        charge_threshold = (
            float(np.percentile(positive_load, 30))
            if strategy == "peak_reduction" and len(positive_load) > 0
            else 0.0
        )

        # --- Simulation loop ---
        soc = float(np.clip(battery_kwh * soc_init_fraction, soc_min, soc_max))
        bat_power = np.zeros(len(df))
        soc_trace = np.zeros(len(df))

        for i in range(len(df)):
            p = 0.0  # desired power (kW): positive = charge, negative = discharge

            if strategy == "self_consumption":
                # Charge when net load is negative (solar export); discharge when importing
                p = -load[i]

            elif strategy == "price_optimization":
                if prices[i] < daily_mean_prices[i]:
                    p = max_power       # cheap slot → charge
                elif prices[i] > daily_mean_prices[i]:
                    p = -max_power      # expensive slot → discharge

            elif strategy == "peak_reduction":
                if load[i] > peak_threshold:
                    # Discharge: push load down to the target level
                    p = -(load[i] - peak_threshold)
                elif load[i] <= charge_threshold:
                    # Charge only during genuinely low-demand periods (bottom 30%)
                    # Cap so that battery charging never pushes modified load above peak_threshold
                    p = min(max_power, max(0.0, peak_threshold - load[i]))
                else:
                    # Neutral during moderate load — preserve charge for upcoming peaks
                    p = 0.0

            # Clip to max charge/discharge power
            p = float(np.clip(p, -max_power, max_power))

            # Apply SoC constraints and recalculate actual power
            if p > 0:  # Charging: energy stored = p * DT * ETA_CHARGE
                max_chargeable = (soc_max - soc) / (DT * ETA_CHARGE)
                p = max(0.0, min(p, max_chargeable))
                soc += p * DT * ETA_CHARGE
            elif p < 0:  # Discharging: energy drawn from battery = |p| * DT / ETA_DISCHARGE
                max_dischargeable = (soc - soc_min) * ETA_DISCHARGE / DT
                p = min(0.0, max(p, -max_dischargeable))
                soc += p * DT / ETA_DISCHARGE  # p is negative, so soc decreases

            soc = float(np.clip(soc, soc_min, soc_max))
            bat_power[i] = p
            soc_trace[i] = soc

        df["Batterij vermogen [kW]"] = bat_power
        df["Batterij SoC [kWh]"] = soc_trace
        # Positive battery power = charging = extra load on MSR; negative = discharging = less load
        df["MSR totaal met batterij [kW]"] = load + bat_power

        return df


if __name__ == "__main__":
    loaded = load_Gsheets()
