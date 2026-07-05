from __future__ import annotations

import html
import math
import numbers
import os
import random
from collections import Counter
from datetime import date
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
RADIAL_HISTOGRAM_BIN_SIZE_M = 1.0
DAMAGE_PHOTO_DIRECTORY = os.path.join("examples", "Wind Farm Inspection", "photos")
SEVERITY_COLORS = {
    SeverityType.CRITICAL.value: "#d62728",
    SeverityType.TO_REPAIR.value: "#ff9f1c",
    SeverityType.COSMETIC.value: "#2ca02c",
}


def dismiss_edit_wind_farm_dialog() -> None:
    """Clear edit wind farm dialog state after dismissing the modal."""
    st.session_state.pop("edit_wind_farm_id", None)


def dismiss_delete_wind_farm_dialog() -> None:
    """Clear delete wind farm dialog state after dismissing the modal."""
    st.session_state.pop("delete_wind_farm_id", None)


def dismiss_add_turbines_dialog() -> None:
    """Clear add turbine dialog state after dismissing the modal."""
    st.session_state.pop("add_turbines_wind_farm_id", None)


def dismiss_remove_turbine_dialog() -> None:
    """Clear remove turbine dialog state after dismissing the modal."""
    st.session_state.pop("remove_turbine_wind_farm_id", None)


def available_damage_photo_paths() -> list[str]:
    """Return existing damage photo paths from the example photo directory."""
    if not os.path.isdir(DAMAGE_PHOTO_DIRECTORY):
        return []
    return sorted(
        os.path.join(DAMAGE_PHOTO_DIRECTORY, file_name)
        for file_name in os.listdir(DAMAGE_PHOTO_DIRECTORY)
        if os.path.isfile(os.path.join(DAMAGE_PHOTO_DIRECTORY, file_name))
    )


def existing_or_random_damage_photo_path(
    candidate_photo_path: str,
    photo_paths: list[str],
    rng: random.Random,
) -> str:
    """Use the candidate photo when it exists; otherwise use a random photo."""
    if os.path.exists(candidate_photo_path):
        return candidate_photo_path
    if not photo_paths:
        raise FileNotFoundError(f"No photos found in {DAMAGE_PHOTO_DIRECTORY!r}.")
    return rng.choice(photo_paths)


def damage_type_example_photo_path(damage_type: DamageType) -> str:
    """Return the example photo path for a damage type."""
    photo_name_by_damage_type = {
        DamageType.CRACK: "crack",
        DamageType.EROSION_TYPE_1: "erosion",
        DamageType.EROSION_TYPE_2: "erosion",
        DamageType.LIGHTNING: "mechanical",
        DamageType.MECHANICAL: "mechanical",
        DamageType.LE_FILM_DAMAGE: "film_damage",
    }
    photo_path = os.path.join(
        DAMAGE_PHOTO_DIRECTORY,
        f"example_{photo_name_by_damage_type[damage_type]}.jpg",
    )
    if not os.path.exists(photo_path):
        raise FileNotFoundError(f"Damage example photo not found: {photo_path!r}.")
    return photo_path


def repair_existing_damage_records(session: Session) -> None:
    """Repair old demo rows so they satisfy current photo and measurement rules."""
    photo_paths = available_damage_photo_paths()
    photo_rng = random.Random(43)
    updated = False

    for damage in session.scalars(select(Damage)).all():
        if photo_paths and (not damage.photo or not os.path.exists(damage.photo)):
            damage.photo = photo_rng.choice(photo_paths)
            updated = True

        if damage.radial_area_size < 0:
            damage.radial_area_size = 0.0
            updated = True

        if damage.radial_area_size == 0:
            # Single/local damage: size describes the damage; density is unused.
            if damage.size <= 0:
                damage.size = 0.01
                updated = True
            if damage.density != 0:
                damage.density = 0.0
                updated = True
        else:
            # Radial area damage: radial_area_size describes the area; size is unused.
            if damage.size != 0:
                damage.size = 0.0
                updated = True
            if damage.density < 0:
                damage.density = 0.0
                updated = True
            elif damage.density >= 100:
                damage.density = 99.0
                updated = True

    if updated:
        session.commit()


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
        repair_existing_damage_records(session)
        return

    wind_farm = WindFarm(
        park_name="DTU Wind Park",
        park_type=ParkType.OFFSHORE,
        country="Denmark",
        operator="DTU",
        turbine_model="DTU 10MW RWT",
        blade_length=86.35,
    )

    severity_values = [
        SeverityType.COSMETIC,
        SeverityType.TO_REPAIR,
        SeverityType.CRITICAL,
    ]
    severity_weights = [0.62, 0.30, 0.08]
    damage_types = list(DamageType)
    depths = list(DepthType)
    cs_positions = list(CSPosition)
    turbine_count = 50
    turbines_per_row = 10
    row_spacing_m = 1000.0
    column_spacing_m = 750.0
    origin_x = 500_000.0
    origin_y = 6_200_000.0
    min_damages_per_blade = 5
    max_damages_per_blade = 50
    blade_length = float(wind_farm.blade_length)
    rng = random.Random(42)

    for turbine_number in range(1, turbine_count + 1):
        layout_index = turbine_number - 1
        row = layout_index // turbines_per_row
        column = layout_index % turbines_per_row
        # Offshore wind farms are typically arranged in rows with large spacing;
        # alternate rows are staggered to reduce wake effects.
        stagger_offset_m = (row % 2) * (column_spacing_m / 2)
        turbine = Turbine(
            wtg_id=turbine_number,
            wt_installation_number=f"WT-{turbine_number:03}",
            coord_x=origin_x + column * column_spacing_m + stagger_offset_m,
            coord_y=origin_y + row * row_spacing_m,
        )
        wind_farm.turbines.append(turbine)

        for blade_number in range(1, 4):
            blade_id = turbine_number * 100 + blade_number
            blade = Blade(blade_id=blade_id)
            turbine.blades.append(blade)

            damage_count = rng.randint(min_damages_per_blade, max_damages_per_blade)
            radial_positions = sorted(
                rng.uniform(0.0, blade_length) for _ in range(damage_count)
            )
            for damage_number, radial_position in enumerate(radial_positions, start=1):
                enum_index = turbine_number + blade_number + damage_number
                damage_id = blade_id * 1000 + damage_number
                inspection_month = ((damage_number - 1) // 28) % 12 + 1
                inspection_day = ((damage_number - 1) % 28) + 1
                damage_type = damage_types[enum_index % len(damage_types)]
                photo_path = damage_type_example_photo_path(damage_type)
                is_area_damage = damage_number % 3 != 0
                radial_area_size = (
                    0.05 + 0.01 * (damage_number % 20) if is_area_damage else 0.0
                )
                damage_size = (
                    0.0 if is_area_damage else 0.02 + 0.005 * (damage_number % 30)
                )
                damage_density = (
                    5.0 + float((damage_number * 3) % 95) if is_area_damage else 0.0
                )
                blade.damages.append(
                    Damage(
                        damage_id=damage_id,
                        inspection_date=date(2026, inspection_month, inspection_day),
                        inspector_name=f"Inspector {((turbine_number - 1) % 8) + 1}",
                        severity=rng.choices(
                            severity_values, weights=severity_weights, k=1
                        )[0],
                        damage_type=damage_type,
                        depth=depths[enum_index % len(depths)],
                        cs_position=cs_positions[enum_index % len(cs_positions)],
                        radial_position=radial_position,
                        radial_area_size=radial_area_size,
                        size=damage_size,
                        density=damage_density,
                        orientation=float((damage_number * 17) % 180),
                        photo=photo_path,
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


def wind_farm_damage_radial_dataframe(
    session: Session, wind_farm_id: int
) -> pd.DataFrame:
    stmt = (
        select(
            Damage.severity,
            Damage.radial_position,
            Damage.radial_area_size,
            Damage.size,
            Damage.orientation,
        )
        .join(Blade, Damage.blade_id == Blade.blade_id)
        .join(Turbine, Blade.wtg_id == Turbine.wtg_id)
        .where(Turbine.wind_farm_id == wind_farm_id)
        .order_by(Damage.radial_position)
    )
    rows = session.execute(stmt).all()
    return pd.DataFrame(
        [
            {
                "severity": severity.value,
                "radial_position_m": radial_position,
                "radial_area_size_m": radial_area_size,
                "size_m": size,
                "orientation": orientation,
            }
            for severity, radial_position, radial_area_size, size, orientation in rows
        ]
    )


def wind_farm_damage_type_counts(session: Session, wind_farm_id: int) -> dict[str, int]:
    stmt = (
        select(Damage.damage_type, func.count(Damage.damage_id))
        .join(Blade, Damage.blade_id == Blade.blade_id)
        .join(Turbine, Blade.wtg_id == Turbine.wtg_id)
        .where(Turbine.wind_farm_id == wind_farm_id)
        .group_by(Damage.damage_type)
    )
    counts = {
        (
            damage_type.value
            if isinstance(damage_type, DamageType)
            else str(damage_type)
        ): int(count)
        for damage_type, count in session.execute(stmt).all()
    }
    return {
        damage_type.value: counts.get(damage_type.value, 0)
        for damage_type in DamageType
    }


def wind_farm_damage_severity_counts(
    session: Session, wind_farm_id: int
) -> dict[str, int]:
    stmt = (
        select(Damage.severity, func.count(Damage.damage_id))
        .join(Blade, Damage.blade_id == Blade.blade_id)
        .join(Turbine, Blade.wtg_id == Turbine.wtg_id)
        .where(Turbine.wind_farm_id == wind_farm_id)
        .group_by(Damage.severity)
    )
    counts = {
        (severity.value if isinstance(severity, SeverityType) else str(severity)): int(
            count
        )
        for severity, count in session.execute(stmt).all()
    }
    return {severity.value: counts.get(severity.value, 0) for severity in SeverityType}


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


def wind_farm_damages_dataframe(session: Session, wind_farm_id: int) -> pd.DataFrame:
    damages = session.execute(
        select(Damage, Turbine.wt_installation_number)
        .join(Blade, Damage.blade_id == Blade.blade_id)
        .join(Turbine, Blade.wtg_id == Turbine.wtg_id)
        .where(Turbine.wind_farm_id == wind_farm_id)
        .order_by(Turbine.wt_installation_number, Blade.blade_id, Damage.damage_id)
    ).all()
    return pd.DataFrame(
        [
            {
                "damage_id": damage.damage_id,
                "wt_installation_number": wt_installation_number,
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
            for damage, wt_installation_number in damages
        ]
    )


@st.dialog("Damage details", width="large")
def damage_dialog(selected_damage: pd.Series) -> None:
    info_column, photo_column = st.columns(
        [0.3, 0.7], gap="small", vertical_alignment="top"
    )

    label_map = {
        "damage_id": "Damage ID",
        "wtg_id": "Wind Turbine ID",
        "wt_installation_number": "WT Installation Number",
        "blade_id": "Blade ID",
        "inspection_date": "Inspection Date",
        "inspector_name": "Inspector Name",
        "severity": "Severity",
        "damage_type": "Damage Type",
        "depth": "Depth",
        "cs_position": "CS Position",
        "radial_position_m": "Radial Position [m]",
        "radial_area_size_m": "Radial Area Size [m]",
        "damage_measurement_mode": "Single Damage / Damage Area",
        "size_m": "Size [m]",
        "density_percent": "Density [%]",
        "orientation": "Orientation [deg]",
        "inspection_comment": "Inspection Comment",
        "analyzer_comment": "Analyzer Comment",
    }
    comment_fields = ["inspection_comment", "analyzer_comment"]
    excluded_info_fields = {"photo", *comment_fields}
    dialog_values = selected_damage.to_dict()
    radial_area_size = pd.to_numeric(
        pd.Series([selected_damage.get("radial_area_size_m")]),
        errors="coerce",
    ).iloc[0]  # type: ignore
    dialog_values["damage_measurement_mode"] = (
        "Damage Area"
        if pd.notna(radial_area_size) and float(radial_area_size) > 0.0
        else "Single Damage"
    )
    ordered_fields = [
        field
        for field in label_map
        if field in dialog_values and field not in excluded_info_fields
    ]
    ordered_fields.extend(
        field  # type: ignore
        for field in selected_damage.index
        if field not in ordered_fields and field not in excluded_info_fields
    )

    def format_dialog_value(field: str, value: Any) -> str:
        if pd.isna(value):
            return ""
        if field in {
            "radial_position_m",
            "radial_area_size_m",
            "size_m",
            "orientation",
        }:
            return f"{float(value):.2f}"
        if field == "density_percent":
            return f"{float(value):.0f}"
        return str(value)

    dialog_style = """
        <style>
            .damage-dialog-info,
            .damage-dialog-comments {
                border: 1px solid rgba(49, 51, 63, 0.18);
                border-radius: 0.35rem;
                overflow: hidden;
            }
            .damage-dialog-field,
            .damage-dialog-comment-field {
                display: grid;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18);
            }
            .damage-dialog-field {
                grid-template-columns: minmax(7rem, 42%) 1fr;
            }
            .damage-dialog-comment-field {
                grid-template-columns: minmax(5.5rem, 24%) 1fr;
            }
            .damage-dialog-field:last-child,
            .damage-dialog-comment-field:last-child {
                border-bottom: 0;
            }
            .damage-dialog-label,
            .damage-dialog-value,
            .damage-dialog-comment-label,
            .damage-dialog-comment-value {
                padding: 0.45rem 0.6rem;
                overflow-wrap: anywhere;
            }
            .damage-dialog-label,
            .damage-dialog-comment-label {
                font-weight: 600;
                background: rgba(49, 51, 63, 0.04);
                border-right: 1px solid rgba(49, 51, 63, 0.18);
                white-space: normal;
            }
            .damage-dialog-comment-value {
                min-height: 5rem;
                white-space: pre-wrap;
            }
        </style>
    """

    with info_column:
        with st.container(key="damage_dialog_info_container"):
            st.markdown("#### Damage information")
            rows_html = "".join(
                "<div class='damage-dialog-field'>"
                f"<div class='damage-dialog-label'>{html.escape(label_map.get(field, field.replace('_', ' ').title()))}</div>"
                f"<div class='damage-dialog-value'>{html.escape(format_dialog_value(field, dialog_values[field]))}</div>"
                "</div>"
                for field in ordered_fields
            )
            st.markdown(
                dialog_style + f"<div class='damage-dialog-info'>{rows_html}</div>",
                unsafe_allow_html=True,
            )

    with photo_column:
        with st.container(key="damage_dialog_photo_container"):
            st.markdown("#### Damage photo")
            photo_path = str(selected_damage["photo"])
            if photo_path and os.path.exists(photo_path):
                st.image(photo_path, width="stretch")
            else:
                st.warning("No damage photo found.")

            comments_html = "".join(
                "<div class='damage-dialog-comment-field'>"
                f"<div class='damage-dialog-comment-label'>{html.escape(label_map[field])}</div>"
                f"<div class='damage-dialog-comment-value'>{'' if pd.isna(selected_damage[field]) else html.escape(str(selected_damage[field]))}</div>"  # type: ignore
                "</div>"
                for field in comment_fields
                if field in selected_damage.index
            )
            if comments_html:
                st.markdown("#### Comments")
                st.markdown(
                    f"<div class='damage-dialog-comments'>{comments_html}</div>",
                    unsafe_allow_html=True,
                )


def render_damage_table(
    damages_df: pd.DataFrame,
    *,
    rows_per_page: int = 5,
    include_wtg_id: bool = False,
    include_severity: bool = True,
    page_key_context: str | None = None,
) -> int | None:
    """Render a compact paginated damage table."""
    total_rows = len(damages_df)
    if total_rows == 0:
        st.info("No damages to show.")
        return None

    page_key_context = page_key_context or str(
        st.session_state.get("wtg_id")
        or st.session_state.get("wind_farm_id")
        or "unknown"
    )
    page_key = f"damage_summary_page_{page_key_context}"
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
            div[class*="st-key-damage_summary_header"] {
                min-height: 3.1rem !important;
                margin-bottom: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_header"] div[data-testid="stHorizontalBlock"] {
                min-height: 3.1rem !important;
                align-items: stretch !important;
                gap: 0 !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_header"] div[data-testid="column"] {
                min-height: 3.1rem !important;
                display: flex !important;
                align-items: stretch !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_header"] div[data-testid="stElementContainer"],
            div[class*="st-key-damage_summary_header"] div[data-testid="stMarkdownContainer"] {
                width: 100% !important;
                min-height: 3.1rem !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            .damage-summary-header {
                font-weight: 600;
                text-align: center;
                padding: 0.35rem 0;
                min-height: 3.1rem;
                display: flex;
                align-items: center;
                justify-content: center;
                line-height: 1.4;
                border-right: 1px solid rgba(49, 51, 63, 0.18);
                border-bottom: 1px solid rgba(49, 51, 63, 0.18);
                box-sizing: border-box;
            }
            .damage-summary-cell {
                min-height: 3.1rem;
                width: 100%;
                padding: 0.35rem 0;
                display: flex;
                align-items: center;
                justify-content: center;
                text-align: center;
                line-height: 1.4;
                font-size: 1rem;
                border-right: 1px solid rgba(49, 51, 63, 0.18);
                box-sizing: border-box;
            }
            .damage-summary-header.first {
                border-left: 1px solid rgba(49, 51, 63, 0.18);
            }
            div[class*="st-key-damage_summary_table_body"] div[data-testid="stVerticalBlock"] {
                gap: 0 !important;
                row-gap: 0 !important;
            }
            div[class*="st-key-damage_summary_table_body"] div[data-testid="stElementContainer"] {
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_row_"] {
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_row_"] div[data-testid="stHorizontalBlock"] {
                gap: 0 !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_row_"] div[data-testid="column"] {
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_row_"] div[data-testid="stElementContainer"] {
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_cell_"] {
                min-height: 3.1rem !important;
                height: 3.1rem !important;
                display: flex !important;
                align-items: center !important;
                padding: 0 !important;
                margin: 0 !important;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18) !important;
                box-sizing: border-box !important;
            }
            div[class*="st-key-damage_summary_cell_"] > div,
            div[class*="st-key-damage_summary_cell_"] div[data-testid="stElementContainer"] {
                width: 100% !important;
                margin: 0 !important;
            }
            div[class*="st-key-damage_summary_cell_id_"] {
                border-left: 1px solid rgba(49, 51, 63, 0.18) !important;
                border-right: 1px solid rgba(49, 51, 63, 0.18) !important;
            }
            div[class*="st-key-damage_summary_cell_id_"] div[data-testid="stButton"] {
                min-height: 3.1rem !important;
                height: 3.1rem !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_cell_id_"] button[kind="tertiary"] {
                color: inherit !important;
                text-decoration: none !important;
                min-height: 0 !important;
                height: auto !important;
                padding: 0 !important;
                margin: 0 !important;
                font-family: inherit !important;
                font-size: 1rem !important;
                font-weight: normal !important;
                line-height: 1.4 !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
            }
            div[class*="st-key-damage_summary_cell_id_"] button[kind="tertiary"] p {
                color: inherit !important;
                text-decoration: none !important;
                font-family: inherit !important;
                font-size: 1rem !important;
                font-weight: normal !important;
                line-height: 1.4 !important;
                margin: 0 !important;
            }
            div[class*="st-key-damage_summary_cell_id_"] button[kind="tertiary"]:hover,
            div[class*="st-key-damage_summary_cell_id_"] button[kind="tertiary"]:hover p {
                color: inherit !important;
                text-decoration: underline !important;
            }
            .damage-summary-cell.severity-critical {
                background-color: rgba(255, 99, 99, 0.22);
            }
            .damage-summary-cell.severity-to-repair {
                background-color: rgba(255, 214, 102, 0.28);
            }
            .damage-summary-cell.severity-cosmetic,
            .damage-mobile-value.severity-cosmetic {
                background-color: rgba(102, 187, 106, 0.22);
            }
            .damage-mobile-value.severity-critical {
                background-color: rgba(255, 99, 99, 0.22);
            }
            .damage-mobile-value.severity-to-repair {
                background-color: rgba(255, 214, 102, 0.28);
            }
            div[class*="st-key-damage_summary_mobile_table"] {
                display: none !important;
            }
            .damage-mobile-fields {
                width: 100%;
                overflow: hidden;
                margin-top: 0 !important;
                border: 1px solid rgba(49, 51, 63, 0.18);
                border-top: 0;
                border-bottom: 0;
                border-radius: 0;
            }
            .damage-mobile-field {
                display: grid;
                grid-template-columns: minmax(7rem, 42%) 1fr;
                min-height: 2.4rem;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18);
            }
            .damage-mobile-label,
            .damage-mobile-value {
                display: flex;
                align-items: center;
                padding: 0.45rem 0.6rem;
                overflow-wrap: anywhere;
            }
            .damage-mobile-label,
            .damage-mobile-id-label {
                font-weight: 600;
                background: rgba(49, 51, 63, 0.04);
                border-right: 1px solid rgba(49, 51, 63, 0.18);
            }
            .damage-mobile-value {
                justify-content: center;
                text-align: center;
            }
            div[class*="st-key-damage_summary_mobile_card_"] div[data-testid="stVerticalBlock"] {
                gap: 0 !important;
                row-gap: 0 !important;
            }
            div[class*="st-key-damage_summary_mobile_card_"] {
                margin-bottom: 1rem !important;
                padding-bottom: 2rem !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] {
                overflow: hidden !important;
                min-height: 2.4rem !important;
                height: 2.4rem !important;
                margin-bottom: 0 !important;
                padding: 0 !important;
                border: 1px solid rgba(49, 51, 63, 0.18) !important;
                border-bottom: 1px solid rgba(49, 51, 63, 0.18) !important;
                border-radius: 0.35rem 0.35rem 0 0 !important;
                box-sizing: border-box !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stHorizontalBlock"] {
                min-height: 2.4rem !important;
                height: 2.4rem !important;
                width: 100% !important;
                gap: 0 !important;
                margin: 0 !important;
                padding: 0 !important;
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                align-items: stretch !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stElementContainer"] {
                min-width: 0 !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stElementContainer"]:has(.damage-mobile-id-label) {
                flex: 0 0 max(7rem, 42%) !important;
                width: max(7rem, 42%) !important;
                max-width: max(7rem, 42%) !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stElementContainer"]:has(button[kind="tertiary"]) {
                flex: 1 1 auto !important;
                width: auto !important;
                max-width: none !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stMarkdownContainer"] {
                width: 100% !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stButton"] {
                width: 100% !important;
            }
            .damage-mobile-id-label {
                min-height: 2.4rem;
                height: 2.4rem;
                display: flex;
                align-items: center;
                min-width: 0;
                padding: 0.45rem 0.6rem;
                font-weight: 600;
                background: rgba(49, 51, 63, 0.04);
                border-right: 1px solid rgba(49, 51, 63, 0.18);
                box-sizing: border-box;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] div[data-testid="stButton"] {
                width: 100% !important;
                min-height: 2.4rem !important;
                height: 2.4rem !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                padding: 0 !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] button[kind="tertiary"] {
                width: 100% !important;
                min-height: 2.4rem !important;
                height: 2.4rem !important;
                padding: 0.45rem 0.6rem !important;
                margin: 0 !important;
                color: inherit !important;
                text-decoration: none !important;
                font-family: inherit !important;
                font-size: 1rem !important;
                font-weight: normal !important;
                line-height: 1.4 !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                border-radius: 0 !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] button[kind="tertiary"] p {
                color: inherit !important;
                text-decoration: none !important;
                font-family: inherit !important;
                font-size: 1rem !important;
                font-weight: normal !important;
                line-height: 1.4 !important;
                margin: 0 !important;
            }
            div[class*="st-key-damage_summary_mobile_id_row_"] button[kind="tertiary"]:hover,
            div[class*="st-key-damage_summary_mobile_id_row_"] button[kind="tertiary"]:hover p {
                color: inherit !important;
                text-decoration: underline !important;
            }

            @media (max-width: 640px) {
                div[class*="st-key-damage_summary_desktop_table"] {
                    display: none !important;
                }
                div[class*="st-key-damage_summary_mobile_table"] {
                    display: block !important;
                }
            }
            @media (min-width: 641px) {
                div[class*="st-key-damage_summary_desktop_table"] {
                    display: block !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    table_columns = [
        ("damage_id", "Damage ID", 0.8),
    ]
    if include_wtg_id:
        turbine_identifier_column = (
            "wt_installation_number"
            if "wt_installation_number" in damages_df.columns
            else "wtg_id"
        )
        turbine_identifier_label = (
            "WT Installation Number"
            if turbine_identifier_column == "wt_installation_number"
            else "Wind Turbine ID"
        )
        table_columns.append((turbine_identifier_column, turbine_identifier_label, 1.4))
    table_columns.append(("blade_id", "Blade ID", 0.8))
    if include_severity:
        table_columns.append(("severity", "Severity", 1.0))
    table_columns.extend(
        [
            ("damage_type", "Type", 1.3),
            ("depth", "Depth", 1.3),
            ("cs_position", "CS Position", 1.2),
            ("radial_position_m", "Radial Position [m]", 1.2),
        ]
    )
    widths = [column[2] for column in table_columns]
    headers = [column[1] for column in table_columns]
    selected_damage_id = None
    damage_records = page_df.to_dict("records")

    def severity_css_class(damage: dict[str, Any]) -> str:
        return {
            "Critical": "severity-critical",
            "To repair": "severity-to-repair",
            "Cosmetic": "severity-cosmetic",
        }.get(str(damage.get("severity", "")), "")

    def render_value(field: str, damage: dict[str, Any]) -> str:
        if field == "radial_position_m":
            return number(damage[field])
        return text(damage[field])

    with st.container(key="damage_summary_desktop_table", gap=None):
        with st.container(key="damage_summary_header", gap=None):
            header_columns = st.columns(widths, gap=None, vertical_alignment="bottom")
            for index, (column, header) in enumerate(zip(header_columns, headers)):
                css_class = (
                    "damage-summary-header first"
                    if index == 0
                    else "damage-summary-header"
                )
                column.markdown(
                    f"<div class='{css_class}'>{html.escape(header)}</div>",
                    unsafe_allow_html=True,
                )

        with st.container(key="damage_summary_table_body", gap=None):
            for damage in damage_records:
                damage_id = int(damage["damage_id"])
                with st.container(key=f"damage_summary_row_{damage_id}", gap=None):
                    row_columns = st.columns(
                        widths, gap=None, vertical_alignment="center"
                    )
                    with row_columns[0].container(
                        key=f"damage_summary_cell_id_{damage_id}"
                    ):
                        if st.button(
                            str(damage_id),
                            key=f"open_damage_{damage_id}",
                            type="tertiary",
                        ):
                            selected_damage_id = damage_id

                    cells = [
                        (
                            render_value(field, damage),
                            severity_css_class(damage) if field == "severity" else "",
                        )
                        for field, _header, _width in table_columns[1:]
                    ]
                    for cell_index, (column, (cell, css_class)) in enumerate(
                        zip(row_columns[1:], cells),
                        start=1,
                    ):
                        with column.container(
                            key=f"damage_summary_cell_{damage_id}_{cell_index}"
                        ):
                            st.markdown(
                                f"<div class='damage-summary-cell {css_class}'>{cell}</div>",
                                unsafe_allow_html=True,
                            )

    with st.container(key="damage_summary_mobile_table", gap=None):
        for damage in damage_records:
            damage_id = int(damage["damage_id"])
            mobile_rows = [
                (
                    header,
                    render_value(field, damage),
                    severity_css_class(damage) if field == "severity" else "",
                )
                for field, header, _width in table_columns[1:]
            ]
            fields_html = "".join(
                "<div class='damage-mobile-field'>"
                f"<div class='damage-mobile-label'>{html.escape(label)}</div>"
                f"<div class='damage-mobile-value {css_class}'>{value}</div>"
                "</div>"
                for label, value, css_class in mobile_rows
            )

            with st.container(
                key=f"damage_summary_mobile_card_{damage_id}",
                border=True,
                gap=None,
            ):
                with st.container(
                    key=f"damage_summary_mobile_id_row_{damage_id}",
                    horizontal=True,
                    vertical_alignment="center",
                    gap=None,
                ):
                    st.markdown(
                        "<div class='damage-mobile-id-label'>Damage ID</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        str(damage_id),
                        key=f"open_damage_mobile_{damage_id}",
                        type="tertiary",
                    ):
                        selected_damage_id = damage_id

                st.markdown(
                    f"<div class='damage-mobile-fields'>{fields_html}</div>",
                    unsafe_allow_html=True,
                )

    return selected_damage_id


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


@st.dialog("Edit wind farm", on_dismiss=dismiss_edit_wind_farm_dialog)
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


@st.dialog(
    "Delete wind farm?",
    icon="⚠️",
    on_dismiss=dismiss_delete_wind_farm_dialog,
)
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


def numeric_dataframe_column(
    df: pd.DataFrame,
    column_name: str,
    *,
    default: float = 0.0,
    lower: float | None = None,
) -> pd.Series:
    """Return a numeric dataframe column, or a default-valued series if missing."""
    if column_name not in df.columns:
        return pd.Series(default, index=df.index)

    numeric_values = pd.to_numeric(df[column_name], errors="coerce")
    if not isinstance(numeric_values, pd.Series):
        raise TypeError

    numeric_values = numeric_values.fillna(default)
    if lower is not None:
        numeric_values = numeric_values.clip(lower=lower)
    return numeric_values


def damage_radial_extents_dataframe(
    damages_df: pd.DataFrame, blade_length: float
) -> pd.DataFrame:
    """Return damages with clipped radial extent start/end columns."""
    extent_df = damages_df.copy()
    extent_df["radial_position_m"] = pd.to_numeric(
        extent_df["radial_position_m"], errors="coerce"
    )
    radial_area_sizes = numeric_dataframe_column(
        extent_df,
        "radial_area_size_m",
        lower=0.0,
    )
    damage_sizes = numeric_dataframe_column(extent_df, "size_m", lower=0.0)
    orientations = numeric_dataframe_column(extent_df, "orientation")

    local_half_radial_extents = (damage_sizes / 2.0) * orientations.map(
        lambda orientation: math.cos(math.radians(float(orientation)))
    )
    local_half_radial_extents = local_half_radial_extents.mask(
        local_half_radial_extents.abs() < 1e-12,
        0.0,
    )
    half_radial_extents = (radial_area_sizes / 2.0).where(
        radial_area_sizes > 0.0,
        local_half_radial_extents,
    )
    raw_extent_starts = extent_df["radial_position_m"] - half_radial_extents
    raw_extent_ends = extent_df["radial_position_m"] + half_radial_extents
    extent_df["radial_extent_start_m"] = raw_extent_starts.combine(
        raw_extent_ends,
        min,
    )
    extent_df["radial_extent_end_m"] = raw_extent_starts.combine(
        raw_extent_ends,
        max,
    )
    extent_df = extent_df[
        extent_df["radial_position_m"].notna()
        & extent_df["radial_extent_end_m"].ge(0)
        & extent_df["radial_extent_start_m"].le(blade_length)
    ].copy()
    if not isinstance(extent_df, pd.DataFrame):
        raise TypeError

    extent_df["radial_extent_start_m"] = extent_df["radial_extent_start_m"].clip(
        lower=0.0,
        upper=blade_length,
    )
    extent_df["radial_extent_end_m"] = extent_df["radial_extent_end_m"].clip(
        lower=0.0,
        upper=blade_length,
    )
    return extent_df


def radial_damage_histogram_bins_dataframe(
    histogram_df: pd.DataFrame, blade_length: float
) -> pd.DataFrame:
    """Expand each damage into every radial histogram bin its extent overlaps."""
    bin_count = max(1, math.ceil(blade_length / RADIAL_HISTOGRAM_BIN_SIZE_M))
    max_bin_index = bin_count - 1
    records: list[dict[str, float | str]] = []

    for severity, extent_start, extent_end in histogram_df[
        ["severity", "radial_extent_start_m", "radial_extent_end_m"]
    ].itertuples(index=False, name=None):
        extent_start = float(extent_start)
        extent_end = float(extent_end)
        if not math.isfinite(extent_start) or not math.isfinite(extent_end):
            continue

        if extent_end <= extent_start:
            bin_indexes = [
                min(
                    max(math.floor(extent_start / RADIAL_HISTOGRAM_BIN_SIZE_M), 0),
                    max_bin_index,
                )
            ]
        else:
            first_bin_index = min(
                max(math.floor(extent_start / RADIAL_HISTOGRAM_BIN_SIZE_M), 0),
                max_bin_index,
            )
            last_bin_position = math.nextafter(extent_end, -math.inf)
            last_bin_index = min(
                max(math.floor(last_bin_position / RADIAL_HISTOGRAM_BIN_SIZE_M), 0),
                max_bin_index,
            )
            bin_indexes = range(first_bin_index, last_bin_index + 1)

        records.extend(
            {
                "severity": str(severity),
                "radial_bin_start_m": bin_index * RADIAL_HISTOGRAM_BIN_SIZE_M,
            }
            for bin_index in bin_indexes
        )

    return pd.DataFrame(records, columns=["severity", "radial_bin_start_m"])


def render_wind_farm_radial_damage_histogram(
    damages_df: pd.DataFrame, blade_length: float
) -> tuple[str, float, float] | None:
    """Render grouped histogram of damage severity counts by radial position."""
    if damages_df.empty:
        st.info("No damages are available for the radial severity histogram.")
        return None

    histogram_df = damage_radial_extents_dataframe(damages_df, blade_length)

    if histogram_df.empty:
        st.info(
            "No damages with valid radial positions are available for the histogram."
        )
        return None

    severity_order = [
        SeverityType.COSMETIC.value,
        SeverityType.TO_REPAIR.value,
        SeverityType.CRITICAL.value,
    ]
    binned_histogram_df = radial_damage_histogram_bins_dataframe(
        histogram_df,
        blade_length,
    )

    fig = go.Figure()
    for severity in severity_order:
        severity_df = binned_histogram_df[binned_histogram_df["severity"] == severity]
        if not isinstance(severity_df, pd.DataFrame):
            fig.add_trace(
                go.Bar(
                    x=[],
                    y=[],
                    name=severity,
                    marker_color=SEVERITY_COLORS[severity],
                    opacity=0.75,
                )
            )
            continue
        counts = severity_df.groupby("radial_bin_start_m").size()
        if counts.empty:
            fig.add_trace(
                go.Bar(
                    x=[],
                    y=[],
                    name=severity,
                    marker_color=SEVERITY_COLORS[severity],
                    opacity=0.75,
                )
            )
            continue

        bin_starts = [float(bin_start) for bin_start in counts.index]
        bin_ends = [
            min(bin_start + RADIAL_HISTOGRAM_BIN_SIZE_M, blade_length)
            for bin_start in bin_starts
        ]
        bin_centers = [(start + end) / 2 for start, end in zip(bin_starts, bin_ends)]
        bin_widths = [(end - start) * 0.96 for start, end in zip(bin_starts, bin_ends)]
        fig.add_trace(
            go.Bar(
                x=bin_centers,
                y=counts.tolist(),
                width=bin_widths,
                name=severity,
                customdata=[
                    [severity, bin_start, bin_end]
                    for bin_start, bin_end in zip(bin_starts, bin_ends)
                ],
                hovertemplate=(
                    "Severity: %{customdata[0]}<br>"
                    "Radial position: %{customdata[1]:.1f}–%{customdata[2]:.1f} m<br>"
                    "Damage count: %{y}<extra></extra>"
                ),
                marker_color=SEVERITY_COLORS[severity],
                opacity=0.75,
            )
        )

    fig.update_layout(
        title=("Damage counts by severity and radial position"),
        xaxis_title="Radial position [m]",
        yaxis_title="Damage count",
        legend_title_text="Severity",
        barmode="overlay",
        bargap=0.04,
        xaxis={"range": [0, blade_length]},
        yaxis={"dtick": 5},
        clickmode="event+select",
    )

    histogram_key = (
        "radial_damage_severity_histogram_"
        f"{st.session_state.get('histogram_chart_version', 0)}"
    )
    event = st.plotly_chart(
        fig,
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key=histogram_key,
    )
    return selected_histogram_bin(event, blade_length)


def selected_histogram_bin(
    event: Any, blade_length: float
) -> tuple[str, float, float] | None:
    """Return selected severity and radial bin from a Plotly histogram event."""
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
    if isinstance(customdata, (list, tuple)) and len(customdata) >= 3:
        return str(customdata[0]), float(customdata[1]), float(customdata[2])

    if isinstance(point, dict) and "x" in point:
        severity_order = [
            SeverityType.COSMETIC.value,
            SeverityType.TO_REPAIR.value,
            SeverityType.CRITICAL.value,
        ]
        curve_number = (
            point.get("curve_number")
            or point.get("curveNumber")
            or point.get("curve_index")
            or 0
        )
        severity = severity_order[int(curve_number)]
        bin_start = (
            math.floor(float(point["x"]) / RADIAL_HISTOGRAM_BIN_SIZE_M)
            * RADIAL_HISTOGRAM_BIN_SIZE_M
        )
        max_bin_start = (
            max(1, math.ceil(blade_length / RADIAL_HISTOGRAM_BIN_SIZE_M)) - 1
        ) * RADIAL_HISTOGRAM_BIN_SIZE_M
        bin_start = min(bin_start, max_bin_start)
        bin_end = min(bin_start + RADIAL_HISTOGRAM_BIN_SIZE_M, blade_length)
        return severity, float(bin_start), float(bin_end)

    return None


TURBINE_EXCEL_COLUMNS = [
    "WTGID",
    "WT Installation Number",
    "Coord. X",
    "Coord. Y",
    "Blade ID 1",
    "Blade ID 2",
    "Blade ID 3",
]
MIN_TURBINE_DISTANCE_M = 150.0


def duplicate_values(values: list[Any]) -> list[Any]:
    """Return duplicate values while preserving their first duplicate order."""
    counts = Counter(values)
    return [value for value, count in counts.items() if count > 1]


def parse_excel_integer(
    value: Any, row_label: str, column_name: str, errors: list[str]
) -> int | None:
    """Parse an Excel cell as an integer and collect validation errors."""
    if pd.isna(value) or isinstance(value, bool):
        errors.append(f"{row_label}: {column_name} must be an integer.")
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real) and float(value).is_integer():
        return int(value)  # type: ignore
    errors.append(f"{row_label}: {column_name} must be an integer.")
    return None


def parse_excel_float(
    value: Any, row_label: str, column_name: str, errors: list[str]
) -> float | None:
    """Parse an Excel cell as a finite float and collect validation errors."""
    if pd.isna(value) or isinstance(value, bool) or not isinstance(value, numbers.Real):
        errors.append(f"{row_label}: {column_name} must be a float.")
        return None
    float_value = float(value)
    if not math.isfinite(float_value):
        errors.append(f"{row_label}: {column_name} must be a finite float.")
        return None
    return float_value


def parse_excel_string(
    value: Any, row_label: str, column_name: str, errors: list[str]
) -> str | None:
    """Parse an Excel cell as a non-empty string and collect validation errors."""
    if pd.isna(value) or not isinstance(value, str) or not value.strip():
        errors.append(f"{row_label}: {column_name} must be a non-empty string.")
        return None
    return value.strip()


def validate_turbines_excel_upload(
    session: Session,
    uploaded_file: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate an uploaded turbine Excel file and return parsed turbine records."""
    errors: list[str] = []
    records: list[dict[str, Any]] = []

    try:
        uploaded_file.seek(0)
        excel_file = pd.ExcelFile(uploaded_file)
    except Exception as exc:
        return [], [f"Could not read Excel file: {exc}"]

    if "Turbines" not in excel_file.sheet_names:
        return [], ["Excel file must contain a sheet named 'Turbines'."]

    try:
        df = pd.read_excel(excel_file, sheet_name="Turbines")
    except Exception as exc:
        return [], [f"Could not read the 'Turbines' sheet: {exc}"]

    if list(df.columns) != TURBINE_EXCEL_COLUMNS:
        return [], [
            "The 'Turbines' sheet must have exactly these columns in this order: "
            + ", ".join(TURBINE_EXCEL_COLUMNS)
        ]

    if df.empty:
        return [], ["The 'Turbines' sheet is empty."]

    for row_index, row in df.iterrows():
        row_label = f"Row {row_index + 2}"  # type: ignore
        wtg_id = parse_excel_integer(row["WTGID"], row_label, "WTGID", errors)
        installation_number = parse_excel_string(
            row["WT Installation Number"],
            row_label,
            "WT Installation Number",
            errors,
        )
        coord_x = parse_excel_float(row["Coord. X"], row_label, "Coord. X", errors)
        coord_y = parse_excel_float(row["Coord. Y"], row_label, "Coord. Y", errors)
        blade_ids = [
            parse_excel_integer(row[column_name], row_label, column_name, errors)
            for column_name in ["Blade ID 1", "Blade ID 2", "Blade ID 3"]
        ]

        parsed_blade_ids = [blade_id for blade_id in blade_ids if blade_id is not None]
        if len(parsed_blade_ids) == 3 and len(set(parsed_blade_ids)) != 3:
            errors.append(f"{row_label}: Blade IDs must be different integers.")

        if (
            wtg_id is not None
            and installation_number is not None
            and coord_x is not None
            and coord_y is not None
            and len(parsed_blade_ids) == 3
        ):
            records.append(
                {
                    "row_label": row_label,
                    "wtg_id": wtg_id,
                    "wt_installation_number": installation_number,
                    "coord_x": coord_x,
                    "coord_y": coord_y,
                    "blade_ids": parsed_blade_ids,
                }
            )

    if errors:
        return [], errors

    duplicate_wtg_ids = duplicate_values([record["wtg_id"] for record in records])
    if duplicate_wtg_ids:
        errors.append(
            "WTGID values must be unique in the file: "
            + ", ".join(str(value) for value in duplicate_wtg_ids)
        )

    duplicate_installation_numbers = duplicate_values(
        [record["wt_installation_number"] for record in records]
    )
    if duplicate_installation_numbers:
        errors.append(
            "WT Installation Number values must be unique in the file: "
            + ", ".join(str(value) for value in duplicate_installation_numbers)
        )

    all_blade_ids = [blade_id for record in records for blade_id in record["blade_ids"]]
    duplicate_blade_ids = duplicate_values(all_blade_ids)
    if duplicate_blade_ids:
        errors.append(
            "Blade IDs must be unique in the file: "
            + ", ".join(str(value) for value in duplicate_blade_ids)
        )

    wtg_ids = [record["wtg_id"] for record in records]
    existing_wtg_ids = set(
        session.scalars(select(Turbine.wtg_id).where(Turbine.wtg_id.in_(wtg_ids))).all()
    )
    if existing_wtg_ids:
        errors.append(
            "WTGID values already exist in the database: "
            + ", ".join(str(value) for value in sorted(existing_wtg_ids))
        )

    installation_numbers = [record["wt_installation_number"] for record in records]
    existing_installation_numbers = set(
        session.scalars(
            select(Turbine.wt_installation_number).where(
                Turbine.wt_installation_number.in_(installation_numbers)
            )
        ).all()
    )
    if existing_installation_numbers:
        errors.append(
            "WT Installation Number values already exist in the database: "
            + ", ".join(str(value) for value in sorted(existing_installation_numbers))
        )

    existing_blade_ids = set(
        session.scalars(
            select(Blade.blade_id).where(Blade.blade_id.in_(all_blade_ids))
        ).all()
    )
    if existing_blade_ids:
        errors.append(
            "Blade IDs already exist in the database: "
            + ", ".join(str(value) for value in sorted(existing_blade_ids))
        )

    existing_turbines = session.scalars(select(Turbine)).all()
    for record in records:
        too_close_existing = [
            (
                turbine.wt_installation_number,
                math.hypot(
                    record["coord_x"] - float(turbine.coord_x),
                    record["coord_y"] - float(turbine.coord_y),
                ),
            )
            for turbine in existing_turbines
        ]
        if too_close_existing:
            closest_installation_number, closest_distance = min(
                too_close_existing,
                key=lambda item: item[1],
            )
            if closest_distance <= MIN_TURBINE_DISTANCE_M:
                errors.append(
                    f"{record['row_label']}: coordinate pair must be more than "
                    f"{MIN_TURBINE_DISTANCE_M:.0f} m from every existing turbine. "
                    f"Closest turbine is {closest_installation_number} at "
                    f"{closest_distance:.1f} m."
                )

    for index, record in enumerate(records):
        for other_record in records[index + 1 :]:
            distance = math.hypot(
                record["coord_x"] - other_record["coord_x"],
                record["coord_y"] - other_record["coord_y"],
            )
            if distance <= MIN_TURBINE_DISTANCE_M:
                errors.append(
                    f"{record['row_label']} and {other_record['row_label']}: "
                    f"coordinate pairs must be more than {MIN_TURBINE_DISTANCE_M:.0f} m "
                    f"apart. Distance is {distance:.1f} m."
                )

    return ([] if errors else records), errors


@st.dialog(
    "Add turbine(s)",
    width="medium",
    on_dismiss=dismiss_add_turbines_dialog,
)
def add_turbines_dialog(session: Session) -> None:
    """Add turbines to the current wind farm."""
    wind_farm_id = st.session_state.get("add_turbines_wind_farm_id")
    farm = session.get(WindFarm, wind_farm_id) if wind_farm_id is not None else None

    if farm is None:
        st.error("Wind farm not found.")
        if st.button("Close"):
            st.session_state.pop("add_turbines_wind_farm_id", None)
            st.rerun()
        return

    next_wtg_id = int(session.scalar(select(func.max(Turbine.wtg_id))) or 0) + 1

    input_tab, excel_tab = st.tabs(["By Input Form", "By Excel"])

    with input_tab:
        st.caption(f"Wind farm: {farm.park_name}")
        with st.form(f"add_single_turbine_form_{farm.id}", enter_to_submit=False):
            wtg_id_col, wt_installation_number_col = st.columns(2)
            wtg_id = wtg_id_col.number_input(
                "Wind Turbine ID",
                min_value=1,
                value=next_wtg_id,
                step=1,
                format="%d",
            )
            wt_installation_number = wt_installation_number_col.text_input(
                "WT installation number"
            )

            coord_x_col, coord_y_col = st.columns(2)
            coord_x = coord_x_col.number_input(
                "Coord X [m]", value=0.0, step=1.0, format="%.3f"
            )
            coord_y = coord_y_col.number_input(
                "Coord Y [m]", value=0.0, step=1.0, format="%.3f"
            )

            st.markdown("##### Blade IDs")
            blade_columns = st.columns(3)
            blade_ids = [
                column.number_input(
                    f"Blade {blade_number} ID",
                    min_value=1,
                    value=int(wtg_id) * 100 + blade_number,
                    step=1,
                    format="%d",
                )
                for blade_number, column in enumerate(blade_columns, start=1)
            ]

            add_col, cancel_col = st.columns(2)
            add = add_col.form_submit_button(
                "Add Turbine", type="primary", width="stretch"
            )
            cancel = cancel_col.form_submit_button("Cancel", width="stretch")

        if cancel:
            st.session_state.pop("add_turbines_wind_farm_id", None)
            st.rerun()

        if add:
            errors: list[str] = []
            wtg_id_int = int(wtg_id)
            blade_id_values = [int(blade_id) for blade_id in blade_ids]
            installation_number = wt_installation_number.strip()
            coord_x_float = float(coord_x)
            coord_y_float = float(coord_y)

            if session.get(Turbine, wtg_id_int) is not None:
                errors.append("Wind Turbine ID must not already be in the database.")

            if not installation_number:
                errors.append("WT installation number must be a non-empty string.")
            elif (
                session.scalar(
                    select(Turbine.wtg_id).where(
                        Turbine.wt_installation_number == installation_number
                    )
                )
                is not None
            ):
                errors.append(
                    "WT installation number must not already be in the database."
                )

            if not math.isfinite(coord_x_float) or not math.isfinite(coord_y_float):
                errors.append("Coordinates must be finite float values.")

            if len(set(blade_id_values)) != len(blade_id_values):
                errors.append("Blade IDs must be different integers.")

            existing_blade_ids = set(
                session.scalars(
                    select(Blade.blade_id).where(Blade.blade_id.in_(blade_id_values))
                ).all()
            )
            if existing_blade_ids:
                errors.append(
                    "Blade IDs must not already be in the database: "
                    + ", ".join(
                        str(blade_id) for blade_id in sorted(existing_blade_ids)
                    )
                )

            existing_turbines = session.scalars(select(Turbine)).all()
            too_close_turbines = [
                (
                    turbine.wt_installation_number,
                    math.hypot(
                        coord_x_float - float(turbine.coord_x),
                        coord_y_float - float(turbine.coord_y),
                    ),
                )
                for turbine in existing_turbines
            ]
            if too_close_turbines:
                closest_installation_number, closest_distance = min(
                    too_close_turbines, key=lambda item: item[1]
                )
                if closest_distance <= 150.0:
                    errors.append(
                        "Coordinate pair must be more than 150 m from every existing "
                        f"turbine. Closest turbine is {closest_installation_number} "
                        f"at {closest_distance:.1f} m."
                    )

            if errors:
                for error in errors:
                    st.error(error)
                return

            try:
                turbine = Turbine(
                    wtg_id=wtg_id_int,
                    wind_farm_id=farm.id,
                    wt_installation_number=installation_number,
                    coord_x=coord_x_float,
                    coord_y=coord_y_float,
                )
                turbine.blades = [
                    Blade(blade_id=blade_id) for blade_id in blade_id_values
                ]
                session.add(turbine)
                session.commit()
                st.session_state.pop("add_turbines_wind_farm_id", None)
                st.session_state.turbine_success_message = (
                    f"Added turbine: {installation_number}"
                )
                st.rerun()
            except IntegrityError:
                session.rollback()
                st.error("A turbine or blade with one of those IDs already exists.")
            except SQLAlchemyError as exc:
                session.rollback()
                st.error(f"Could not add turbine: {exc}")

    with excel_tab:
        st.caption(f"Wind farm: {farm.park_name}")
        st.markdown(
            """
            Upload an Excel file with a sheet named **Turbines**.

            Columns must be:
            **WTGID**, **WT Installation Number**, **Coord. X**, **Coord. Y**,
            **Blade ID 1**, **Blade ID 2**, **Blade ID 3**.

            IDs must be unique integers, installation numbers must be unique strings,
            and coordinates must be floats in meters `[m]`.
            """
        )
        example_excel_df = pd.DataFrame(
            [
                {
                    "WTGID": 51,
                    "WT Installation Number": "WT-051",
                    "Coord. X": 1250.0,
                    "Coord. Y": 4356.8,
                    "Blade ID 1": 5101,
                    "Blade ID 2": 5102,
                    "Blade ID 3": 5103,
                }
            ]
        )
        st.table(
            example_excel_df.style.format({"Coord. X": "{:.1f}", "Coord. Y": "{:.1f}"})
        )
        uploaded_file = st.file_uploader(
            "Upload Excel file",
            type=["xlsx", "xls"],
            key=f"add_turbines_excel_upload_{farm.id}",
        )
        if uploaded_file is not None:
            st.info(f"Selected file: {uploaded_file.name}")

        add_excel_col, cancel_excel_col = st.columns(2)
        if add_excel_col.button("Add Turbines", type="primary", width="stretch"):
            if uploaded_file is None:
                st.error("Please upload an Excel file before adding turbines.")
            else:
                turbine_records, errors = validate_turbines_excel_upload(
                    session,
                    uploaded_file,
                )
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    try:
                        turbines = []
                        for record in turbine_records:
                            turbine = Turbine(
                                wtg_id=record["wtg_id"],
                                wind_farm_id=farm.id,
                                wt_installation_number=record["wt_installation_number"],
                                coord_x=record["coord_x"],
                                coord_y=record["coord_y"],
                            )
                            turbine.blades = [
                                Blade(blade_id=blade_id)
                                for blade_id in record["blade_ids"]
                            ]
                            turbines.append(turbine)

                        session.add_all(turbines)
                        session.commit()
                        st.session_state.pop("add_turbines_wind_farm_id", None)
                        st.session_state.turbine_success_message = f"Added {len(turbines)} turbine(s) from {uploaded_file.name}"
                        st.rerun()
                    except IntegrityError:
                        session.rollback()
                        st.error(
                            "A turbine, installation number, or blade ID already exists."
                        )
                    except SQLAlchemyError as exc:
                        session.rollback()
                        st.error(f"Could not add turbines: {exc}")
        if cancel_excel_col.button("Cancel", width="stretch"):
            st.session_state.pop("add_turbines_wind_farm_id", None)
            st.rerun()


@st.dialog(
    "Remove turbine",
    icon="⚠️",
    on_dismiss=dismiss_remove_turbine_dialog,
)
def remove_turbine_dialog(session: Session) -> None:
    """Select and delete a turbine from the current wind farm."""
    wind_farm_id = st.session_state.get("remove_turbine_wind_farm_id")
    farm = session.get(WindFarm, wind_farm_id) if wind_farm_id is not None else None

    if farm is None:
        st.error("Wind farm not found.")
        if st.button("Close"):
            st.session_state.pop("remove_turbine_wind_farm_id", None)
            st.rerun()
        return

    turbines = session.scalars(
        select(Turbine)
        .where(Turbine.wind_farm_id == farm.id)
        .order_by(Turbine.wt_installation_number)
    ).all()

    if not turbines:
        st.info("No turbines are defined for this wind farm.")
        if st.button("Close"):
            st.session_state.pop("remove_turbine_wind_farm_id", None)
            st.rerun()
        return

    turbine_by_wtg_id = {turbine.wtg_id: turbine for turbine in turbines}
    selected_wtg_id = st.selectbox(
        "WT installation number",
        [turbine.wtg_id for turbine in turbines],
        format_func=lambda wtg_id: turbine_by_wtg_id[wtg_id].wt_installation_number,
    )
    selected_turbine = turbine_by_wtg_id[int(selected_wtg_id)]

    st.warning("This will also delete the turbine's blades and damages.")

    with st.container(horizontal=True, gap="small"):
        if st.button("Delete", type="primary"):
            deleted_wtg_id = selected_turbine.wtg_id
            deleted_installation_number = selected_turbine.wt_installation_number
            try:
                session.delete(selected_turbine)
                session.commit()
                st.session_state.pop("remove_turbine_wind_farm_id", None)
                if st.session_state.get("wtg_id") == deleted_wtg_id:
                    st.session_state.wtg_id = None
                st.session_state.turbine_success_message = (
                    f"Deleted turbine: {deleted_installation_number}"
                )
                st.rerun()
            except SQLAlchemyError as exc:
                session.rollback()
                st.error(f"Could not delete turbine: {exc}")

        if st.button("Cancel"):
            st.session_state.pop("remove_turbine_wind_farm_id", None)
            st.rerun()


def render_turbines_overview_section(
    turbines_df: pd.DataFrame,
    damage_type_counts: dict[str, int],
    damage_severity_counts: dict[str, int],
) -> None:
    total_turbines = len(turbines_df)
    if turbines_df.empty:
        turbines_with_damages = 0
        total_damages = 0
    else:
        turbines_with_damages = int((turbines_df["damage_count"] > 0).sum())
        total_damages = int(turbines_df["damage_count"].sum())  # type: ignore

    turbine_damage_percent = (
        (turbines_with_damages / total_turbines) * 100 if total_turbines else 0.0
    )

    damage_type_rows = "".join(
        "<div class='turbine-overview-damage-type-row'>"
        "<div class='turbine-overview-row-header'>"
        f"<span>{html.escape(damage_type)}</span>"
        f"<strong>{((count / total_damages) * 100 if total_damages else 0.0):.1f}%</strong>"
        "</div>"
        "<div class='turbine-overview-mini-progress'>"
        f"<div style='width: {((count / total_damages) * 100 if total_damages else 0.0):.2f}%;'></div>"
        "</div>"
        "</div>"
        for damage_type, count in damage_type_counts.items()
    )

    severity_total = sum(damage_severity_counts.values())
    severity_bar_segments = "".join(
        f"<div title='{html.escape(severity)}: {count}' "
        f"style='width: {((count / severity_total) * 100 if severity_total else 0.0):.2f}%; "
        f"background: {SEVERITY_COLORS[severity]};'></div>"
        for severity, count in damage_severity_counts.items()
        if count > 0
    )
    severity_legend_rows = "".join(
        "<div class='turbine-overview-severity-legend-row'>"
        f"<span><i style='background: {SEVERITY_COLORS[severity]};'></i>{html.escape(severity)}</span>"
        f"<strong>{((count / severity_total) * 100 if severity_total else 0.0):.1f}%</strong>"
        "</div>"
        for severity, count in damage_severity_counts.items()
    )

    st.markdown(
        """
        <style>
            .turbine-overview-card {
                border: 1px solid rgba(49, 51, 63, 0.16);
                border-radius: 0.5rem;
                padding: 1rem;
                min-height: 100%;
                background: rgba(49, 51, 63, 0.02);
            }
            .turbine-overview-main-value {
                font-size: 1.7rem;
                font-weight: 700;
                line-height: 1.2;
            }
            .turbine-overview-progress-label,
            .turbine-overview-row-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 0.75rem;
                margin-bottom: 0.35rem;
            }
            .turbine-overview-progress-track,
            .turbine-overview-mini-progress {
                width: 100%;
                overflow: hidden;
                border-radius: 999px;
                background: rgba(49, 51, 63, 0.12);
            }
            .turbine-overview-progress-track {
                height: 0.9rem;
            }
            .turbine-overview-progress-track > div,
            .turbine-overview-mini-progress > div {
                height: 100%;
                border-radius: inherit;
                background: rgb(255, 75, 75);
            }
            .turbine-overview-progress-percent {
                margin-top: 0.3rem;
                color: rgba(49, 51, 63, 0.68);
                font-size: 0.9rem;
                text-align: right;
            }
            .turbine-overview-damage-type-row,
            .turbine-overview-severity-section {
                margin-top: 0.55rem;
            }
            .turbine-overview-mini-progress {
                height: 0.45rem;
            }
            .turbine-overview-severity-bar {
                display: flex;
                width: 100%;
                height: 1rem;
                overflow: hidden;
                border-radius: 999px;
                background: rgba(49, 51, 63, 0.12);
            }
            .turbine-overview-severity-bar > div {
                height: 100%;
            }
            .turbine-overview-severity-legend-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 0.75rem;
                margin-top: 0.35rem;
                font-size: 0.9rem;
            }
            .turbine-overview-severity-legend-row span {
                display: inline-flex;
                align-items: center;
                gap: 0.4rem;
            }
            .turbine-overview-severity-legend-row i {
                display: inline-block;
                width: 0.7rem;
                height: 0.7rem;
                border-radius: 999px;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    turbine_summary_column, damages_column = st.columns([2, 2])
    with turbine_summary_column:
        st.markdown(
            "<div class='turbine-overview-card'>"
            f"<div class='turbine-overview-main-value'>{total_turbines} Turbines</div>"
            "<div class='turbine-overview-damage-type-row'>"
            "<div class='turbine-overview-progress-label'>"
            "<span>Turbines with Damages</span>"
            f"<strong>{turbines_with_damages} | {total_turbines}</strong>"
            "</div>"
            "<div class='turbine-overview-progress-track'>"
            f"<div style='width: {turbine_damage_percent:.2f}%;'></div>"
            "</div>"
            f"<div class='turbine-overview-progress-percent'>{turbine_damage_percent:.1f}%</div>"
            "</div>"
            "<div class='turbine-overview-severity-section'>"
            "<div class='turbine-overview-progress-label'>"
            "<span>Damage Severity Distribution</span>"
            "</div>"
            f"<div class='turbine-overview-severity-bar'>{severity_bar_segments}</div>"
            f"{severity_legend_rows}"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with damages_column:
        st.markdown(
            f"<div class='turbine-overview-card'><div class='turbine-overview-main-value'>{total_damages} Damages</div>{damage_type_rows}</div>",
            unsafe_allow_html=True,
        )


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

    if success_message := st.session_state.pop("turbine_success_message", None):
        st.toast(success_message, icon="✅", duration=3)

    turbines_df = turbines_dataframe(session, farm.id)

    with st.container(horizontal=True, gap="small"):
        if st.button("Add Turbine(s)", key="add_turbines_button"):
            st.session_state.add_turbines_wind_farm_id = farm.id
            st.session_state.pop("remove_turbine_wind_farm_id", None)
        if st.button(
            "Remove Turbine",
            key="remove_turbine_button",
            disabled=turbines_df.empty,
        ):
            st.session_state.remove_turbine_wind_farm_id = farm.id
            st.session_state.pop("add_turbines_wind_farm_id", None)
        st.markdown(
            "<div style='border-left: 1px solid rgba(49, 51, 63, 0.25); height: 2.4rem;'></div>",
            unsafe_allow_html=True,
        )
        st.button("Add Inspection Data", key="add_inspection_data_button")

    st.divider()

    st.subheader("Damage Overview")
    render_turbines_overview_section(
        turbines_df,
        wind_farm_damage_type_counts(session, farm.id),
        wind_farm_damage_severity_counts(session, farm.id),
    )
    st.divider()

    if st.session_state.get("add_turbines_wind_farm_id") == farm.id:
        add_turbines_dialog(session)
    if st.session_state.get("remove_turbine_wind_farm_id") == farm.id:
        remove_turbine_dialog(session)

    if turbines_df.empty:
        st.info("No turbines are defined for this wind farm yet.")
        return

    st.subheader("Wind Park Layout")
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
    st.divider()

    st.subheader("Damage severity distribution from root to tip")
    radial_damage_df = wind_farm_damage_radial_dataframe(session, farm.id)
    selected_histogram_filter = render_wind_farm_radial_damage_histogram(
        radial_damage_df,
        float(farm.blade_length),
    )

    if selected_histogram_filter is not None:
        severity, radial_start, radial_end = selected_histogram_filter
        st.query_params.clear()
        st.session_state.page = "wind_farm_damages"
        st.session_state.wind_farm_id = farm.id
        st.session_state.wtg_id = None
        st.session_state.histogram_damage_severity = severity
        st.session_state.histogram_damage_radial_start = radial_start
        st.session_state.histogram_damage_radial_end = radial_end
        st.session_state.histogram_chart_version = (
            st.session_state.get("histogram_chart_version", 0) + 1
        )
        st.session_state[f"wind_farm_damage_type_filter_{farm.id}"] = [
            damage_type.value for damage_type in DamageType
        ]
        st.session_state[f"damage_summary_page_wind_farm_histogram_{farm.id}"] = 1
        st.rerun()

    selected_wtg_id = selected_plot_wtg_id(plot_event, turbines_df)
    if selected_wtg_id is not None:
        st.query_params.clear()
        st.session_state.page = "damages"
        st.session_state.wtg_id = selected_wtg_id
        st.rerun()


def show_wind_farm_damages_page(session: Session) -> None:
    farm = session.get(WindFarm, st.session_state.wind_farm_id)
    if farm is None:
        st.session_state.page = "wind_farms"
        st.error("Wind farm not found.")
        return

    severity = st.session_state.get("histogram_damage_severity")
    radial_start = st.session_state.get("histogram_damage_radial_start")
    radial_end = st.session_state.get("histogram_damage_radial_end")
    if severity is None or radial_start is None or radial_end is None:
        st.session_state.page = "turbines"
        st.error("Histogram filter selection was not found.")
        return

    radial_start = float(radial_start)
    radial_end = float(radial_end)
    blade_length = float(farm.blade_length)

    if st.button("← Back to turbine map"):
        st.query_params.clear()
        st.session_state.page = "turbines"
        st.session_state.wind_farm_id = farm.id
        st.session_state.wtg_id = None
        st.session_state.pop("histogram_damage_severity", None)
        st.session_state.pop("histogram_damage_radial_start", None)
        st.session_state.pop("histogram_damage_radial_end", None)
        st.rerun()

    st.header(f"Damages in {farm.park_name}")
    st.caption(
        f"Applied histogram selection: severity **{severity}**, "
        f"radial position **{radial_start:.1f}–{radial_end:.1f} m**."
    )

    damages_df = wind_farm_damages_dataframe(session, farm.id)
    if damages_df.empty:
        st.info("No damages are defined for this wind farm.")
        return

    page_key = f"damage_summary_page_wind_farm_histogram_{farm.id}"
    damage_type_options = [damage_type.value for damage_type in DamageType]
    damage_type_filter_key = f"wind_farm_damage_type_filter_{farm.id}"
    if damage_type_filter_key not in st.session_state:
        st.session_state[damage_type_filter_key] = damage_type_options
    else:
        st.session_state[damage_type_filter_key] = [
            damage_type
            for damage_type in st.session_state[damage_type_filter_key]
            if damage_type in damage_type_options
        ]

    with st.container(border=True):
        st.markdown("**Filters**")
        st.caption("Damage Type")
        selected_damage_types = st.multiselect(
            "Damage Type",
            options=damage_type_options,
            key=damage_type_filter_key,
            on_change=lambda: st.session_state.update({page_key: 1}),
            label_visibility="collapsed",
        )

    damages_with_extents_df = damage_radial_extents_dataframe(damages_df, blade_length)
    radial_starts = damages_with_extents_df["radial_extent_start_m"]
    radial_ends = damages_with_extents_df["radial_extent_end_m"]
    local_damage_mask = radial_starts == radial_ends
    if radial_end >= blade_length:
        local_radial_mask = radial_starts.between(
            radial_start,
            radial_end,
            inclusive="both",
        )
    else:
        local_radial_mask = (radial_starts >= radial_start) & (
            radial_starts < radial_end
        )
    area_radial_mask = (radial_ends > radial_start) & (radial_starts < radial_end)
    radial_mask = (local_damage_mask & local_radial_mask) | (
        ~local_damage_mask & area_radial_mask
    )

    filtered_damages_df = damages_with_extents_df[
        (damages_with_extents_df["severity"] == severity)
        & radial_mask
        & damages_with_extents_df["damage_type"].isin(selected_damage_types)
    ]

    st.subheader("Damage Table")
    selected_damage_id = render_damage_table(
        filtered_damages_df,  # type: ignore
        rows_per_page=10,
        include_wtg_id=True,
        include_severity=False,
        page_key_context=f"wind_farm_histogram_{farm.id}",
    )

    if selected_damage_id is not None:
        selected_damage = filtered_damages_df.loc[
            filtered_damages_df["damage_id"].astype(int) == selected_damage_id
        ].iloc[0]
        damage_dialog(selected_damage)


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

    blade_ids = session.scalars(
        select(Blade.blade_id)
        .where(Blade.wtg_id == turbine.wtg_id)
        .order_by(Blade.blade_id)
    ).all()
    page_key = f"damage_summary_page_{turbine.wtg_id}"
    severity_options = [
        SeverityType.CRITICAL.value,
        SeverityType.TO_REPAIR.value,
        SeverityType.COSMETIC.value,
    ]
    damage_type_options = [damage_type.value for damage_type in DamageType]
    blade_length = float(turbine.wind_farm.blade_length)
    selected_blade_ids = []
    selected_severities = []
    selected_damage_types = []
    st.markdown(
        """
        <style>
            .damage-filter-separator {
                border-left: 1px solid rgba(49, 51, 63, 0.18);
                min-height: 4.25rem;
                margin: 0.2rem auto 0 auto;
                width: 1px;
            }
            .damage-filter-horizontal-separator {
                border-top: 1px solid rgba(49, 51, 63, 0.18);
                margin: 0.75rem 0;
            }
            .radial-slider-values {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 0.5rem;
                font-size: 0.85rem;
                color: rgba(49, 51, 63, 0.75);
                margin-top: -0.35rem;
            }
            .radial-slider-values span:nth-child(2),
            .radial-slider-values span:nth-child(3) {
                text-align: center;
                font-weight: 600;
            }
            .radial-slider-values span:last-child {
                text-align: right;
            }
            @media (max-width: 640px) {
                .damage-filter-separator {
                    border-left: 0;
                    border-top: 1px solid rgba(49, 51, 63, 0.18);
                    min-height: 0;
                    width: 100%;
                    margin: 0.75rem 0;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown("**Filters**")
        blade_filter_col, separator_col, severity_filter_col = st.columns(
            [1, 0.04, 1],
            vertical_alignment="top",
        )
        separator_col.markdown(
            "<div class='damage-filter-separator'></div>",
            unsafe_allow_html=True,
        )

        blade_filter_col.caption("Blade IDs")
        severity_filter_col.caption("Severity")

        blade_checkbox_cols = blade_filter_col.columns(
            len(blade_ids),
            gap="small",
            vertical_alignment="top",
        )
        for blade_col, blade_id in zip(blade_checkbox_cols, blade_ids):
            if blade_col.checkbox(
                str(blade_id),
                value=True,
                key=f"damage_blade_filter_{turbine.wtg_id}_{blade_id}",
                on_change=lambda: st.session_state.update({page_key: 1}),
            ):
                selected_blade_ids.append(blade_id)

        severity_checkbox_cols = severity_filter_col.columns(
            len(severity_options),
            gap=None,
            vertical_alignment="top",
        )
        for severity_col, severity in zip(severity_checkbox_cols, severity_options):
            if severity_col.checkbox(
                severity,
                value=True,
                key=f"damage_severity_filter_{turbine.wtg_id}_{severity}",
                on_change=lambda: st.session_state.update({page_key: 1}),
            ):
                selected_severities.append(severity)

        st.markdown(
            "<div class='damage-filter-horizontal-separator'></div>",
            unsafe_allow_html=True,
        )
        damage_type_filter_col, bottom_separator_col, radial_filter_col = st.columns(
            [1, 0.04, 1],
            vertical_alignment="top",
        )
        bottom_separator_col.markdown(
            "<div class='damage-filter-separator'></div>",
            unsafe_allow_html=True,
        )

        damage_type_filter_col.caption("Damage Type")
        selected_damage_types = damage_type_filter_col.multiselect(
            "Damage Type",
            options=damage_type_options,
            default=damage_type_options,
            key=f"damage_type_filter_{turbine.wtg_id}",
            on_change=lambda: st.session_state.update({page_key: 1}),
            label_visibility="collapsed",
        )

        radial_filter_col.caption("Radial Position [m]")
        selected_radial_region = radial_filter_col.slider(
            "Radial Position [m]",
            min_value=0.0,
            max_value=blade_length,
            value=(0.0, blade_length),
            step=0.1,
            format="%.1f m",
            key=f"damage_radial_filter_{turbine.wtg_id}",
            on_change=lambda: st.session_state.update({page_key: 1}),
            label_visibility="collapsed",
        )
        radial_filter_col.markdown(
            "<div class='radial-slider-values'>"
            f"<span>0.0 m</span>"
            f"<span>{selected_radial_region[0]:.1f} m</span>"
            f"<span>{selected_radial_region[1]:.1f} m</span>"
            f"<span>{blade_length:.1f} m</span>"
            "</div>",
            unsafe_allow_html=True,
        )

    radial_start, radial_end = selected_radial_region
    filtered_damages_df = damages_df[
        damages_df["blade_id"].isin(selected_blade_ids)
        & damages_df["severity"].isin(selected_severities)
        & damages_df["damage_type"].isin(selected_damage_types)
        & damages_df["radial_position_m"].between(radial_start, radial_end)
    ]

    st.subheader("Damage Table")
    if isinstance(filtered_damages_df, pd.DataFrame):
        selected_damage_id = render_damage_table(
            filtered_damages_df,
            rows_per_page=10,
        )
    else:
        selected_damage_id = None

    if selected_damage_id is not None:
        selected_damage = filtered_damages_df.loc[
            filtered_damages_df["damage_id"].astype(int) == selected_damage_id
        ].iloc[0]
        damage_dialog(selected_damage)


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
        elif st.session_state.page == "wind_farm_damages":
            show_wind_farm_damages_page(session)
        elif st.session_state.page == "damages":
            show_damages_page(session)
        else:
            show_wind_farms_page(session)


if __name__ == "__main__":
    main()
