from __future__ import annotations

import html
import os
from datetime import date
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from aquada_bdm.database import Base
from aquada_bdm.models import (
    Blade,
    CSPosition,
    Damage,
    DamageType,
    DepthType,
    ParkType,
    SeverityType,
    Turbine,
    WindFarm,
)

DEFAULT_APP_DATABASE_URL = "sqlite:///aquada_bdm_streamlit.db"
MOBILE_BREAKPOINT_PX = 1100


@st.cache_resource
def get_app_engine() -> Engine:
    """Create the Streamlit database engine.

    Set DATABASE_URL to use PostgreSQL. If DATABASE_URL is not set, the app uses
    a local SQLite file for quick testing.
    """
    database_url = os.environ.get("DATABASE_URL", DEFAULT_APP_DATABASE_URL)
    connect_args = (
        {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    )
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return engine


def seed_dummy_data(session: Session) -> None:
    """Seed demo data if the database is empty."""
    if session.scalar(select(func.count()).select_from(WindFarm)):
        return

    wind_farm = WindFarm(
        park_name="DTU Wind Park",
        park_type=ParkType.OFFSHORE,
        country="Denmark",
        operator="DTU",
        turbine_model="DTU 10MW RWT",
        blade_length=86.35,
    )

    severities = list(SeverityType)
    damage_types = list(DamageType)
    depths = list(DepthType)
    cs_positions = list(CSPosition)

    for turbine_number in range(1, 5):
        turbine = Turbine(
            wtg_id=turbine_number,
            wt_installation_number=f"WT-{turbine_number:03}",
            coord_x=12.300 + turbine_number * 0.025,
            coord_y=55.600 + turbine_number * 0.025,
        )
        wind_farm.turbines.append(turbine)

        for blade_number in range(1, 4):
            blade_id = turbine_number * 100 + blade_number
            blade = Blade(blade_id=blade_id)
            turbine.blades.append(blade)

            damage_count = ((turbine_number + blade_number) % 5) + 1
            for damage_number in range(1, damage_count + 1):
                enum_index = turbine_number + blade_number + damage_number
                damage_id = blade_id * 100 + damage_number
                blade.damages.append(
                    Damage(
                        damage_id=damage_id,
                        inspection_date=date(2026, 1, damage_number),
                        inspector_name=f"Inspector {turbine_number}",
                        severity=severities[enum_index % len(severities)],
                        damage_type=damage_types[enum_index % len(damage_types)],
                        depth=depths[enum_index % len(depths)],
                        cs_position=cs_positions[enum_index % len(cs_positions)],
                        radial_position=10.0 * blade_number + damage_number,
                        radial_area_size=0.5 * damage_number,
                        size=0.2 * damage_number,
                        density=10.0 * damage_number,
                        orientation=15.0 * damage_number,
                        photo=(
                            "examples/Wind Farm Inspection/photos/"
                            f"{turbine_number}_2016.06.02_{damage_number:02}.jpg"
                        ),
                        inspection_comment=(
                            f"Dummy inspection comment for turbine {turbine_number}, "
                            f"blade {blade_number}, damage {damage_number}."
                        ),
                        analyzer_comment=f"Dummy analyzer comment for damage {damage_id}.",
                    )
                )

    session.add(wind_farm)
    session.commit()


def wind_farms_dataframe(session: Session) -> pd.DataFrame:
    wind_farms = session.scalars(select(WindFarm).order_by(WindFarm.id)).all()
    return pd.DataFrame(
        [
            {
                "id": farm.id,
                "park_name": farm.park_name,
                "park_type": farm.park_type.value,
                "country": farm.country,
                "operator": farm.operator,
                "turbine_model": farm.turbine_model,
                "blade_length": farm.blade_length,
            }
            for farm in wind_farms
        ]
    )


def turbines_dataframe(session: Session, wind_farm_id: int) -> pd.DataFrame:
    stmt = (
        select(
            Turbine.wtg_id,
            Turbine.wt_installation_number,
            Turbine.coord_x,
            Turbine.coord_y,
            func.count(Damage.damage_id).label("damage_count"),
        )
        .select_from(Turbine)
        .outerjoin(Blade, Blade.wtg_id == Turbine.wtg_id)
        .outerjoin(Damage, Damage.blade_id == Blade.blade_id)
        .where(Turbine.wind_farm_id == wind_farm_id)
        .group_by(
            Turbine.wtg_id,
            Turbine.wt_installation_number,
            Turbine.coord_x,
            Turbine.coord_y,
        )
        .order_by(Turbine.wtg_id)
    )
    rows = session.execute(stmt).all()
    df = pd.DataFrame(
        rows,
        columns=[
            "wtg_id",
            "wt_installation_number",
            "coord_x",
            "coord_y",
            "damage_count",
        ],
    )
    if not df.empty:
        # Keep zero-damage turbines visible while still scaling by damage count.
        df["plot_size"] = df["damage_count"].astype(float).clip(lower=1.0)
    return df


def damages_dataframe(session: Session, wtg_id: int) -> pd.DataFrame:
    damages = session.scalars(
        select(Damage)
        .join(Blade, Damage.blade_id == Blade.blade_id)
        .where(Blade.wtg_id == wtg_id)
        .order_by(Blade.blade_id, Damage.damage_id)
    ).all()
    return pd.DataFrame(
        [
            {
                "damage_id": damage.damage_id,
                "blade_id": damage.blade_id,
                "inspection_date": damage.inspection_date.strftime("%d.%m.%Y"),
                "inspector_name": damage.inspector_name,
                "severity": damage.severity.value,
                "damage_type": damage.damage_type.value,
                "depth": damage.depth.value,
                "cs_position": damage.cs_position.value,
                "radial_position_m": damage.radial_position,
                "radial_area_size_m": damage.radial_area_size,
                "size_m": damage.size,
                "density_percent": damage.density,
                "orientation": damage.orientation,
                "photo": damage.photo,
                "inspection_comment": damage.inspection_comment,
                "analyzer_comment": damage.analyzer_comment,
            }
            for damage in damages
        ]
    )


def render_expandable_damages_table(
    damages_df: pd.DataFrame,
    *,
    rows_per_page: int = 5,
) -> None:
    """Render a compact paginated damage table."""
    total_rows = len(damages_df)
    if total_rows == 0:
        st.info("No damages to show.")
        return

    page_key = f"damage_summary_page_{st.session_state.get('wtg_id', 'unknown')}"
    total_pages = max(1, (total_rows + rows_per_page - 1) // rows_per_page)
    st.session_state[page_key] = min(
        max(1, int(st.session_state.get(page_key, 1))),
        total_pages,
    )

    start = (st.session_state[page_key] - 1) * rows_per_page
    end = min(start + rows_per_page, total_rows)

    with st.container(
        horizontal=True,
        horizontal_alignment="distribute",
        vertical_alignment="center",
        gap="small",
    ):
        if st.button(
            "← Previous",
            key=f"{page_key}_previous",
            disabled=st.session_state[page_key] <= 1,
        ):
            st.session_state[page_key] -= 1
            st.rerun()
        st.markdown(
            f"<div style='text-align:center; white-space:nowrap'>"
            f"Page {st.session_state[page_key]} of {total_pages} "
            f"&nbsp;·&nbsp; Showing {start + 1}-{end} of {total_rows}</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            "Next →",
            key=f"{page_key}_next",
            disabled=st.session_state[page_key] >= total_pages,
        ):
            st.session_state[page_key] += 1
            st.rerun()

    page_df = damages_df.iloc[start:end]

    def text(value: Any) -> str:
        if pd.isna(value):
            return ""
        return html.escape(str(value))

    def number(value: Any, suffix: str = "") -> str:
        if pd.isna(value):
            return ""
        return f"{float(value):.2f}{suffix}"

    st.markdown(
        """
        <style>
            .damage-summary-header-row,
            .damage-summary-row {
                display: grid;
                grid-template-columns: 0.8fr 0.8fr 1fr 1.3fr 1.3fr 1.2fr 1.2fr;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18);
            }
            .damage-summary-header {
                font-weight: 600;
            }
            .damage-summary-header,
            .damage-summary-cell {
                text-align: center;
                padding: 0.35rem 0;
                min-height: 3.1rem;
                display: flex;
                align-items: center;
                justify-content: center;
                line-height: 1.4;
                border-right: 1px solid rgba(49, 51, 63, 0.18);
            }
            .damage-summary-header:first-child,
            .damage-summary-cell:first-child {
                border-left: 1px solid rgba(49, 51, 63, 0.18);
            }
            .damage-summary-cell.severity-critical {
                background-color: rgba(255, 99, 99, 0.22);
            }
            .damage-summary-cell.severity-to-repair {
                background-color: rgba(255, 214, 102, 0.28);
            }
            .damage-summary-cell.severity-cosmetic {
                background-color: rgba(102, 187, 106, 0.22);
            }
            .damage-summary-link {
                color: inherit !important;
                text-decoration: none !important;
            }
            .damage-summary-link:hover {
                color: inherit !important;
                text-decoration: underline !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    headers = [
        "Damage ID",
        "Blade ID",
        "Severity",
        "Type",
        "Depth",
        "CS Position",
        "Radial Position [m]",
    ]
    st.markdown(
        "<div class='damage-summary-header-row'>"
        + "".join(
            f"<div class='damage-summary-header'>{html.escape(header)}</div>"
            for header in headers
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    wind_farm_id = st.session_state.get("wind_farm_id")
    wtg_id = st.session_state.get("wtg_id")
    for damage in page_df.to_dict("records"):
        severity_class = {
            "Critical": "severity-critical",
            "To repair": "severity-to-repair",
            "Cosmetic": "severity-cosmetic",
        }.get(str(damage["severity"]), "")
        cells = [
            (text(damage["damage_id"]), ""),
            (text(damage["blade_id"]), ""),
            (text(damage["severity"]), severity_class),
            (text(damage["damage_type"]), ""),
            (text(damage["depth"]), ""),
            (text(damage["cs_position"]), ""),
            (number(damage["radial_position_m"]), ""),
        ]
        st.markdown(
            "<div class='damage-summary-row'>"
            + "".join(
                f"<div class='damage-summary-cell {css_class}'>{cell}</div>"
                for cell, css_class in cells
            )
            + "</div>",
            unsafe_allow_html=True,
        )


def selected_rows(event: Any) -> list[int]:
    """Return selected row positions from a Streamlit dataframe event."""
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")

    if selection is None:
        return []
    if isinstance(selection, dict):
        return selection.get("rows", []) or []
    return getattr(selection, "rows", []) or []


def render_wind_farms_table(
    farms_df: pd.DataFrame,
) -> tuple[int | None, int | None, int | None]:
    """Render wind farms as a table and return clicked/open, edit, and delete ids."""
    st.markdown(
        f"""
        <style>
            div[data-testid="stButton"] {{
                display: flex !important;
                align-items: center !important;
                min-height: 2rem !important;
                padding: 0.25rem 0 !important;
            }}
            div[data-testid="stButton"] button[kind="tertiary"] {{
                color: inherit !important;
                text-decoration: none !important;
                min-height: 0 !important;
                height: auto !important;
                padding: 0 !important;
                margin: 0 !important;
                font-family: inherit !important;
                font-size: 1rem !important;
                font-weight: normal !important;
                line-height: 1.6 !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
            }}
            div[data-testid="stButton"] button[kind="tertiary"] p {{
                color: inherit !important;
                text-decoration: none !important;
                font-family: inherit !important;
                font-size: 1rem !important;
                font-weight: normal !important;
                line-height: 1.6 !important;
                margin: 0 !important;
            }}
            div[data-testid="stButton"] button[kind="tertiary"]:hover,
            div[data-testid="stButton"] button[kind="tertiary"]:hover p {{
                color: inherit !important;
                text-decoration: underline !important;
            }}
            div[class*="st-key-wind_farm_cell_"] {{
                min-height: 2.5rem !important;
                height: 2.5rem !important;
                display: flex !important;
                align-items: center !important;
                padding: 0 !important;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18) !important;
                box-sizing: border-box !important;
            }}
            div[class*="st-key-wind_farm_cell_"] > div,
            div[class*="st-key-wind_farm_cell_"] div[data-testid="stElementContainer"] {{
                width: 100% !important;
                margin: 0 !important;
            }}
            div[class*="st-key-wind_farm_cell_"] div[data-testid="stButton"] {{
                min-height: 2.5rem !important;
                height: 2.5rem !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                padding: 0 !important;
            }}
            div[class*="st-key-wind_farm_cell_"] button {{
                margin: 0 !important;
            }}
            .wind-farm-header {{
                font-weight: 600;
                border-bottom: 1px solid rgba(49, 51, 63, 0.25);
                padding-bottom: 0.35rem;
                margin-bottom: 0.25rem;
                text-align: center;
            }}
            .wind-farm-action-header {{
                min-height: 1.85rem;
                padding-bottom: 0.35rem;
                margin-bottom: 0.25rem;
                border-bottom: 1px solid rgba(49, 51, 63, 0.25);
            }}
            div[class*="st-key-wind_farm_desktop_first_row"] {{
                padding-top: 0.6rem !important;
            }}
            .wind-farm-cell {{
                min-height: 2.5rem;
                width: 100%;
                padding: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                text-align: center;
                line-height: 1.6;
                font-size: 1rem;
            }}
            div[class*="st-key-wind_farm_cell_actions_"] div[data-testid="stHorizontalBlock"] {{
                min-height: 2.5rem !important;
                height: 2.5rem !important;
                align-items: center !important;
            }}
            div[class*="st-key-wind_farm_cell_actions_"] div[data-testid="column"] {{
                min-height: 2.5rem !important;
                height: 2.5rem !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
            }}
            div[class*="st-key-wind_farm_cell_actions_"] div[data-testid="stButton"] {{
                min-height: 1.65rem !important;
                height: 1.65rem !important;
                padding: 0 !important;
                justify-content: center !important;
            }}
            div[class*="st-key-wind_farm_cell_actions_"] button {{
                min-height: 1.65rem !important;
                height: 1.65rem !important;
                width: 1.65rem !important;
                padding: 0 !important;
                font-size: 0.85rem !important;
                line-height: 1 !important;
            }}
            div[class*="st-key-wind_farms_mobile_table"] {{
                display: none !important;
            }}
            .wind-farm-mobile-fields {{
                width: 100%;
                overflow: hidden;
                margin-top: 0 !important;
                border: 1px solid rgba(49, 51, 63, 0.18);
                border-top: 0;
                border-bottom: 0;
                border-radius: 0;
            }}
            .wind-farm-mobile-field {{
                display: grid;
                grid-template-columns: minmax(7rem, 42%) 1fr;
                min-height: 2.4rem;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18);
            }}
            .wind-farm-mobile-label,
            .wind-farm-mobile-value {{
                display: flex;
                align-items: center;
                padding: 0.45rem 0.6rem;
                overflow-wrap: anywhere;
            }}
            .wind-farm-mobile-label {{
                font-weight: 600;
                background: rgba(49, 51, 63, 0.04);
                border-right: 1px solid rgba(49, 51, 63, 0.18);
            }}
            .wind-farm-mobile-value {{
                justify-content: center;
                text-align: center;
            }}
            div[class*="st-key-wind_farm_mobile_card_"] div[data-testid="stVerticalBlock"] {{
                gap: 0 !important;
                row-gap: 0 !important;
            }}
            div[class*="st-key-wind_farm_mobile_name_row_"] {{
                overflow: hidden !important;
                min-height: 2.4rem !important;
                height: 2.4rem !important;
                margin-bottom: 0 !important;
                padding: 0 !important;
                border: 1px solid rgba(49, 51, 63, 0.18) !important;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18) !important;
                border-radius: 0.35rem 0.35rem 0 0 !important;
                box-sizing: border-box !important;
            }}
            div[class*="st-key-wind_farm_mobile_name_row_"] div[data-testid="stElementContainer"],
            div[class*="st-key-wind_farm_mobile_name_row_"] div[data-testid="stMarkdownContainer"] {{
                width: 100% !important;
                margin: 0 !important;
                padding: 0 !important;
            }}
            .wind-farm-mobile-name-row {{
                display: grid;
                grid-template-columns: minmax(0, 42%) minmax(0, 58%);
                min-height: 2.4rem;
                height: 2.4rem;
                width: 100%;
            }}
            .wind-farm-mobile-name-label {{
                min-height: 2.4rem;
                display: flex;
                align-items: center;
                min-width: 0;
                padding: 0.45rem 0.6rem;
                font-weight: 600;
                background: rgba(49, 51, 63, 0.04);
                border-right: 1px solid rgba(49, 51, 63, 0.18);
                box-sizing: border-box;
            }}
            .wind-farm-mobile-name-value {{
                min-width: 0;
                min-height: 2.4rem;
                display: flex;
                align-items: stretch;
                justify-content: center;
            }}
            .wind-farm-mobile-name-link {{
                width: 100%;
                min-width: 0;
                min-height: 2.4rem;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 0.45rem 0.6rem;
                box-sizing: border-box;
                color: inherit !important;
                text-align: center;
                text-decoration: none !important;
                overflow-wrap: anywhere;
            }}
            .wind-farm-mobile-name-link:hover {{
                text-decoration: underline !important;
            }}
            div[class*="st-key-wind_farm_mobile_fields_"] {{
                margin-top: 0 !important;
                padding-top: 0 !important;
            }}
            div[class*="st-key-wind_farm_mobile_fields_"] div[data-testid="stElementContainer"] {{
                margin-top: 0 !important;
                padding-top: 0 !important;
            }}
            div[class*="st-key-wind_farm_mobile_actions_"] {{
                margin-top: 1.2rem !important;
            }}
            div[class*="st-key-wind_farm_mobile_actions_"] div[data-testid="stHorizontalBlock"] {{
                margin-top: 1.2rem !important;
                gap: 0.2rem !important;
                justify-content: flex-start !important;
            }}
            div[class*="st-key-wind_farm_mobile_actions_"] div[data-testid="stButton"] {{
                width: auto !important;
            }}
            div[class*="st-key-wind_farm_mobile_card_"] {{
                margin-bottom: 1rem !important;
                padding-bottom: 0.6rem !important;
            }}

            @media (max-width: {MOBILE_BREAKPOINT_PX}px) {{
                div[class*="st-key-wind_farms_desktop_table"] {{
                    display: none !important;
                }}
                div[class*="st-key-wind_farms_mobile_table"] {{
                    display: block !important;
                }}
            }}
            @media (min-width: {MOBILE_BREAKPOINT_PX + 1}px) {{
                div[class*="st-key-wind_farms_desktop_table"] {{
                    display: block !important;
                }}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    widths = [1.2, 1, 1, 1, 1.2, 1, 0.6]
    headers = [
        "Name",
        "Type",
        "Country",
        "Operator",
        "Turbine Model",
        "Blade Length",
        "",
    ]

    def render_text_cell(column: Any, value: str, key: str) -> None:
        with column.container(key=key):
            st.markdown(
                f"<div class='wind-farm-cell'>{html.escape(value)}</div>",
                unsafe_allow_html=True,
            )

    selected_farm_id = None
    edit_farm_id = None
    delete_farm_id = None
    farms = farms_df.to_dict("records")

    with st.container(key="wind_farms_desktop_table"):
        header_columns = st.columns(widths, gap=None, vertical_alignment="bottom")
        for column, header in zip(header_columns, headers):
            if header:
                column.markdown(
                    f"<div class='wind-farm-header'>{header}</div>",
                    unsafe_allow_html=True,
                )
            else:
                column.markdown(
                    "<div class='wind-farm-action-header'></div>",
                    unsafe_allow_html=True,
                )

        for index, farm in enumerate(farms):
            farm_id = int(farm["id"])
            row_key = (
                "wind_farm_desktop_first_row"
                if index == 0
                else f"wind_farm_desktop_row_{farm_id}"
            )
            with st.container(key=row_key):
                columns = st.columns(widths, gap=None, vertical_alignment="center")
                with columns[0].container(key=f"wind_farm_cell_name_{farm_id}"):
                    if st.button(
                        str(farm["park_name"]),
                        key=f"open_wind_farm_{farm_id}",
                        type="tertiary",
                    ):
                        selected_farm_id = farm_id
                render_text_cell(
                    columns[1],
                    str(farm["park_type"]),
                    f"wind_farm_cell_type_{farm_id}",
                )
                render_text_cell(
                    columns[2],
                    str(farm["country"]),
                    f"wind_farm_cell_country_{farm_id}",
                )
                render_text_cell(
                    columns[3],
                    str(farm["operator"]),
                    f"wind_farm_cell_operator_{farm_id}",
                )
                render_text_cell(
                    columns[4],
                    str(farm["turbine_model"]),
                    f"wind_farm_cell_turbine_model_{farm_id}",
                )
                render_text_cell(
                    columns[5],
                    f"{float(farm['blade_length']):.2f}",
                    f"wind_farm_cell_blade_length_{farm_id}",
                )
                with columns[6].container(key=f"wind_farm_cell_actions_{farm_id}"):
                    edit_col, delete_col = st.columns(
                        2,
                        gap=None,
                        vertical_alignment="center",
                    )
                    if edit_col.button(
                        "✏️",
                        key=f"edit_wind_farm_{farm_id}",
                        help=f"Edit {farm['park_name']}",
                    ):
                        edit_farm_id = farm_id
                    if delete_col.button(
                        "🗑️",
                        key=f"delete_wind_farm_{farm_id}",
                        help=f"Delete {farm['park_name']}",
                    ):
                        delete_farm_id = farm_id

    with st.container(key="wind_farms_mobile_table"):
        for farm in farms:
            farm_id = int(farm["id"])
            mobile_rows = [
                ("Type", str(farm["park_type"])),
                ("Country", str(farm["country"])),
                ("Operator", str(farm["operator"])),
                ("Turbine Model", str(farm["turbine_model"])),
                ("Blade Length", f"{float(farm['blade_length']):.2f}"),
            ]
            fields_html = "".join(
                "<div class='wind-farm-mobile-field'>"
                f"<div class='wind-farm-mobile-label'>{html.escape(label)}</div>"
                f"<div class='wind-farm-mobile-value'>{html.escape(value)}</div>"
                "</div>"
                for label, value in mobile_rows
            )

            with st.container(
                key=f"wind_farm_mobile_card_{farm_id}",
                border=True,
                gap=None,
            ):
                with st.container(
                    key=f"wind_farm_mobile_name_row_{farm_id}",
                    gap=None,
                ):
                    farm_name = html.escape(str(farm["park_name"]))
                    farm_href = html.escape(
                        f"?page=turbines&wind_farm_id={farm_id}",
                        quote=True,
                    )
                    st.markdown(
                        "<div class='wind-farm-mobile-name-row'>"
                        "<div class='wind-farm-mobile-name-label'>Name</div>"
                        "<div class='wind-farm-mobile-name-value'>"
                        f"<a class='wind-farm-mobile-name-link' href='{farm_href}' target='_self'>{farm_name}</a>"
                        "</div>"
                        "</div>",
                        unsafe_allow_html=True,
                    )
                with st.container(
                    key=f"wind_farm_mobile_fields_{farm_id}",
                    gap=None,
                ):
                    st.markdown(
                        f"<div class='wind-farm-mobile-fields'>{fields_html}</div>",
                        unsafe_allow_html=True,
                    )
                with st.container(
                    key=f"wind_farm_mobile_actions_{farm_id}",
                    horizontal=True,
                    horizontal_alignment="left",
                    vertical_alignment="center",
                    gap="small",
                ):
                    if st.button(
                        "✏️ Edit",
                        key=f"edit_wind_farm_mobile_{farm_id}",
                        help=f"Edit {farm['park_name']}",
                        width="content",
                    ):
                        edit_farm_id = farm_id
                    if st.button(
                        "🗑️ Delete",
                        key=f"delete_wind_farm_mobile_{farm_id}",
                        help=f"Delete {farm['park_name']}",
                        width="content",
                    ):
                        delete_farm_id = farm_id

    return selected_farm_id, edit_farm_id, delete_farm_id


@st.dialog("Edit wind farm")
def edit_wind_farm_dialog(session: Session) -> None:
    """Edit details for a selected wind farm."""
    farm_id = st.session_state.get("edit_wind_farm_id")
    farm = session.get(WindFarm, farm_id) if farm_id is not None else None

    if farm is None:
        st.error("Wind farm not found.")
        if st.button("Close"):
            st.session_state.pop("edit_wind_farm_id", None)
            st.rerun()
        return

    park_type_values = [item.value for item in ParkType]
    park_type_index = park_type_values.index(farm.park_type.value)

    with st.form(f"edit_wind_farm_form_{farm.id}", enter_to_submit=False):
        park_name = st.text_input("Park name", value=farm.park_name)
        park_type = st.selectbox(
            "Park type",
            park_type_values,
            index=park_type_index,
        )
        country = st.text_input("Country", value=farm.country)
        operator = st.text_input("Operator", value=farm.operator)
        turbine_model = st.text_input("Turbine model", value=farm.turbine_model)
        blade_length = st.number_input(
            "Blade length (m)",
            min_value=0.01,
            value=float(farm.blade_length),
        )

        save_col, cancel_col = st.columns(2)
        save = save_col.form_submit_button("Save", type="primary", width="stretch")
        cancel = cancel_col.form_submit_button("Cancel", width="stretch")

    if cancel:
        st.session_state.pop("edit_wind_farm_id", None)
        st.rerun()

    if save:
        park_name = park_name.strip()
        country = country.strip()
        operator = operator.strip()
        turbine_model = turbine_model.strip()

        if not all([park_name, country, operator, turbine_model]):
            st.error("Please fill in all fields before saving.")
            return

        try:
            farm.park_name = park_name
            farm.park_type = ParkType(park_type)
            farm.country = country
            farm.operator = operator
            farm.turbine_model = turbine_model
            farm.blade_length = float(blade_length)
            session.commit()
            st.session_state.pop("edit_wind_farm_id", None)
            st.session_state.wind_farm_success_message = (
                f"Updated wind farm: {park_name}"
            )
            st.rerun()
        except IntegrityError:
            session.rollback()
            st.error("A wind farm with that park name already exists.")
        except SQLAlchemyError as exc:
            session.rollback()
            st.error(f"Could not update wind farm: {exc}")


@st.dialog("Delete wind farm?", icon="⚠️")
def confirm_delete_wind_farm_dialog(session: Session) -> None:
    """Ask for confirmation before deleting a wind farm and its children."""
    farm_id = st.session_state.get("delete_wind_farm_id")
    farm = session.get(WindFarm, farm_id) if farm_id is not None else None

    if farm is None:
        st.error("Wind farm not found.")
        if st.button("Close"):
            st.session_state.pop("delete_wind_farm_id", None)
            st.rerun()
        return

    st.write(f"Are you sure you want to delete **{farm.park_name}**?")
    st.warning("This will also delete its turbines, blades, and damages.")

    confirm_col, cancel_col = st.columns(2)
    if confirm_col.button("Delete", type="primary", width="stretch"):
        deleted_name = farm.park_name
        try:
            session.delete(farm)
            session.commit()
            st.session_state.pop("delete_wind_farm_id", None)
            if st.session_state.get("wind_farm_id") == farm_id:
                st.session_state.wind_farm_id = None
                st.session_state.wtg_id = None
                st.session_state.page = "wind_farms"
            st.session_state.wind_farm_success_message = (
                f"Deleted wind farm: {deleted_name}"
            )
            st.rerun()
        except SQLAlchemyError as exc:
            session.rollback()
            st.error(f"Could not delete wind farm: {exc}")

    if cancel_col.button("Cancel", width="stretch"):
        st.session_state.pop("delete_wind_farm_id", None)
        st.rerun()


def selected_plot_wtg_id(event: Any, turbines_df: pd.DataFrame) -> int | None:
    """Return the selected WTGID from a Streamlit Plotly selection event."""
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")

    points = []
    if isinstance(selection, dict):
        points = selection.get("points", []) or []
    elif selection is not None:
        points = getattr(selection, "points", []) or []

    if not points:
        return None

    point = points[0]
    customdata = point.get("customdata") if isinstance(point, dict) else None
    if customdata is not None:
        if isinstance(customdata, dict):
            wtg_id = customdata.get("wtg_id")
            if wtg_id is not None:
                return int(wtg_id)
        elif isinstance(customdata, (list, tuple)) and customdata:
            return int(customdata[0])

    point_index = None
    if isinstance(point, dict):
        point_index = (
            point.get("point_index")
            or point.get("pointNumber")
            or point.get("point_number")
        )
    if point_index is not None and 0 <= point_index < len(turbines_df):
        return int(turbines_df.iloc[point_index]["wtg_id"])

    return None


def show_wind_farms_page(session: Session) -> None:
    st.header("Wind farms")

    if success_message := st.session_state.pop("wind_farm_success_message", None):
        st.toast(success_message, icon="✅", duration=3)

    form_version = st.session_state.setdefault("add_wind_farm_form_version", 0)
    # Change the invisible suffix after a successful submit so Streamlit recreates
    # the expander in its initial collapsed state.
    expander_label = "Add a new wind farm" + ("\u200b" * form_version)

    with st.expander(expander_label, expanded=False):
        with st.form(
            f"add_wind_farm_form_{form_version}",
            clear_on_submit=False,
            enter_to_submit=False,
        ):
            park_name = st.text_input("Park name", key=f"park_name_{form_version}")
            park_type = st.selectbox(
                "Park type",
                [item.value for item in ParkType],
                key=f"park_type_{form_version}",
            )
            country = st.text_input("Country", key=f"country_{form_version}")
            operator = st.text_input("Operator", key=f"operator_{form_version}")
            turbine_model = st.text_input(
                "Turbine model", key=f"turbine_model_{form_version}"
            )
            blade_length = st.number_input(
                "Blade length (m)",
                min_value=0.01,
                value=80.0,
                key=f"blade_length_{form_version}",
            )
            submitted = st.form_submit_button("Add wind farm")

        if submitted:
            park_name = park_name.strip()
            country = country.strip()
            operator = operator.strip()
            turbine_model = turbine_model.strip()

            if not all([park_name, country, operator, turbine_model]):
                st.error("Please fill in all fields before adding a wind farm.")
            else:
                try:
                    session.add(
                        WindFarm(
                            park_name=park_name,
                            park_type=ParkType(park_type),
                            country=country,
                            operator=operator,
                            turbine_model=turbine_model,
                            blade_length=float(blade_length),
                        )
                    )
                    session.commit()
                    st.session_state.add_wind_farm_form_version = form_version + 1
                    st.session_state.wind_farm_success_message = (
                        f"Added wind farm: {park_name}"
                    )
                    st.rerun()
                except IntegrityError:
                    session.rollback()
                    st.error("A wind farm with that park name already exists.")
                except SQLAlchemyError as exc:
                    session.rollback()
                    st.error(f"Could not add wind farm: {exc}")

    farms_df = wind_farms_dataframe(session)
    if farms_df.empty:
        st.info("No wind farms found. Add one with the form above.")
        return

    st.caption("Click a wind farm name to open its turbine map.")
    selected_farm_id, edit_farm_id, delete_farm_id = render_wind_farms_table(farms_df)

    if edit_farm_id is not None:
        st.session_state.edit_wind_farm_id = edit_farm_id
    if delete_farm_id is not None:
        st.session_state.delete_wind_farm_id = delete_farm_id

    if st.session_state.get("edit_wind_farm_id") is not None:
        edit_wind_farm_dialog(session)
    if st.session_state.get("delete_wind_farm_id") is not None:
        confirm_delete_wind_farm_dialog(session)

    if selected_farm_id is not None:
        st.query_params.clear()
        st.session_state.page = "turbines"
        st.session_state.wind_farm_id = selected_farm_id
        st.session_state.wtg_id = None
        st.rerun()


def show_turbines_page(session: Session) -> None:
    farm = session.get(WindFarm, st.session_state.wind_farm_id)
    if farm is None:
        st.session_state.page = "wind_farms"
        st.error("Wind farm not found.")
        return

    if st.button("← Back to wind farms"):
        st.query_params.clear()
        st.session_state.page = "wind_farms"
        st.session_state.wind_farm_id = None
        st.session_state.wtg_id = None
        st.rerun()

    st.header(f"Turbines in {farm.park_name}")
    turbines_df = turbines_dataframe(session, farm.id)

    if turbines_df.empty:
        st.info("No turbines are defined for this wind farm yet.")
        st.button("Add Turbines")
        return

    fig = px.scatter(
        turbines_df,
        x="coord_x",
        y="coord_y",
        size="plot_size",
        color="damage_count",
        hover_name="wt_installation_number",
        hover_data={
            "wtg_id": True,
            "damage_count": True,
            "coord_x": ":.3f",
            "coord_y": ":.3f",
            "plot_size": False,
        },
        custom_data=["wtg_id"],
        labels={
            "coord_x": "Coord X [m]",
            "coord_y": "Coord Y [m]",
            "damage_count": "Damage count",
        },
        title="Turbine locations by coordinates in X and Y<br>Point size indicates total damage count",
    )
    fig.update_traces(marker={"sizemin": 8, "line": {"width": 1, "color": "black"}})
    fig.update_layout(clickmode="event+select")

    plot_event = st.plotly_chart(
        fig,
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key="turbine_scatter",
    )
    st.caption("*The XY coordinates are in projection UTM 32 Euref89.*")

    selected_wtg_id = selected_plot_wtg_id(plot_event, turbines_df)
    if selected_wtg_id is not None:
        st.query_params.clear()
        st.session_state.page = "damages"
        st.session_state.wtg_id = selected_wtg_id
        st.rerun()


def show_damages_page(session: Session) -> None:
    wtg_id = st.session_state.wtg_id
    turbine = session.get(Turbine, wtg_id)
    if turbine is None:
        st.session_state.page = "turbines"
        st.error("Turbine not found.")
        return

    if st.button("← Back to turbine map"):
        st.query_params.clear()
        st.session_state.page = "turbines"
        st.session_state.wind_farm_id = turbine.wind_farm_id
        st.session_state.wtg_id = None
        st.rerun()

    st.header(
        f"Damages for turbine {turbine.wtg_id} ({turbine.wt_installation_number})"
    )
    damages_df = damages_dataframe(session, turbine.wtg_id)

    if damages_df.empty:
        st.info("No damages are defined for this turbine.")
        return

    st.subheader("Damage Table")
    if isinstance(filtered_damages_df, pd.DataFrame):
        render_expandable_damages_table(filtered_damages_df, rows_per_page=10)
    else:
        st.write("No damages found.")


def sync_navigation_from_query_params() -> None:
    """Use URL query parameters created by table links for navigation."""
    page = st.query_params.get("page")

    if page == "turbines":
        try:
            st.session_state.page = "turbines"
            st.session_state.wind_farm_id = int(st.query_params["wind_farm_id"])
            st.session_state.wtg_id = None
        except (KeyError, TypeError, ValueError):
            st.query_params.clear()
            st.session_state.page = "wind_farms"
            st.session_state.wind_farm_id = None
            st.session_state.wtg_id = None
    elif page == "damages":
        try:
            st.session_state.page = "damages"
            st.session_state.wind_farm_id = int(st.query_params["wind_farm_id"])
            st.session_state.wtg_id = int(st.query_params["wtg_id"])
        except (KeyError, TypeError, ValueError):
            st.query_params.clear()
            st.session_state.page = "wind_farms"
            st.session_state.wind_farm_id = None
            st.session_state.wtg_id = None


def main() -> None:
    st.set_page_config(page_title="AQUADA BDM", layout="wide")
    st.markdown(
        f"""
        <style>
            [data-testid="stMainBlockContainer"] {{
                max-width: none !important;
            }}

            /* Switch to the card layout at or below 1100px instead of forcing
               horizontal scrolling. */
            @media (max-width: {MOBILE_BREAKPOINT_PX}px) {{
                html,
                body,
                .stApp,
                [data-testid="stAppViewContainer"],
                [data-testid="stMain"],
                [data-testid="stMainBlockContainer"] {{
                    min-width: 0 !important;
                    width: 100% !important;
                }}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("AQUADA Blade Damage Map")

    st.session_state.setdefault("page", "wind_farms")
    st.session_state.setdefault("wind_farm_id", None)
    st.session_state.setdefault("wtg_id", None)
    sync_navigation_from_query_params()

    engine = get_app_engine()
    with Session(engine) as session:
        seed_dummy_data(session)

        if st.session_state.page == "turbines":
            show_turbines_page(session)
        elif st.session_state.page == "damages":
            show_damages_page(session)
        else:
            show_wind_farms_page(session)


if __name__ == "__main__":
    main()
