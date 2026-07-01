from __future__ import annotations

import enum
from datetime import date
from typing import Any, Mapping

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aquada_bdm.database import Base


class ParkType(str, enum.Enum):
    """Allowed wind farm park types."""

    ONSHORE = "Onshore"
    OFFSHORE = "Offshore"


class SeverityType(str, enum.Enum):
    """Allowed damage severity classifications."""

    CRITICAL = "Critical"
    TO_REPAIR = "To repair"
    COSMETIC = "Cosmetic"


class DepthType(str, enum.Enum):
    """Allowed damage depth classifications."""

    SURFACE = "Surface"
    STRUCTURE_SEEN = "Structure seen"
    DAMAGED_STRUCTURE = "Damaged Structure"
    HOLE = "Hole"


class DamageType(str, enum.Enum):
    """Allowed damage type classifications."""

    CRACK = "Crack"
    EROSION_TYPE_1 = "Erosion Type 1"
    EROSION_TYPE_2 = "Erosion Type 2"
    LIGHTNING = "Lightning"
    MECHANICAL = "Mechanical"
    LE_FILM_DAMAGE = "LE film damage"


class CSPosition(str, enum.Enum):
    """Allowed cross-section / chordwise damage positions."""

    PRESSURE_SIDE = "Pressure Side"
    SUCTION_SIDE = "Suction Side"
    LE = "LE"
    LE_AREA = "LE Area"
    LE_PRESSURE = "LE Pressure"
    LE_SUCTION = "LE Suction"
    SPAR_AREA = "Spar Area"
    SPAR_PRESSURE = "Spar Pressure"
    SPAR_SUCTION = "Spar Suction"
    TE = "TE"
    TE_AREA = "TE Area"
    TE_PRESSURE = "TE Pressure"
    TE_SUCTION = "TE Suction"


class WindFarm(Base):
    """Wind farm / park level metadata from the MATLAB database."""

    __tablename__ = "wind_farms"
    __table_args__ = (
        UniqueConstraint("park_name", name="uq_wind_farms_park_name"),
        CheckConstraint("blade_length > 0", name="ck_wind_farms_blade_length_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_name: Mapped[str] = mapped_column(String(255), nullable=False)
    park_type: Mapped[ParkType] = mapped_column(
        Enum(
            ParkType,
            name="park_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    turbine_model: Mapped[str] = mapped_column(String(255), nullable=False)
    blade_length: Mapped[float] = mapped_column(Float, nullable=False)

    turbines: Mapped[list[Turbine]] = relationship(
        back_populates="wind_farm",
        cascade="all, delete-orphan",
    )

    @classmethod
    def from_matlab_bdDB(cls, bdDB: Mapping[str, Any]) -> WindFarm:
        """Build a WindFarm object from the loaded MATLAB bdDB struct."""
        return cls(
            park_name=str(bdDB["parkname"]),
            park_type=ParkType(str(bdDB["parkType"])),
            country=str(bdDB["country"]),
            operator=str(bdDB["operator"]),
            turbine_model=str(bdDB["turbine"]),
            blade_length=float(bdDB["bladeLength"]),
        )

    def __repr__(self) -> str:
        return (
            f"WindFarm(id={self.id!r}, park_name={self.park_name!r}, "
            f"park_type={self.park_type.value!r}, country={self.country!r})"
        )


class Turbine(Base):
    """Individual turbine location and identifier data."""

    __tablename__ = "turbines"

    wtg_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    wind_farm_id: Mapped[int] = mapped_column(
        ForeignKey("wind_farms.id", ondelete="CASCADE"),
        nullable=False,
    )
    wt_installation_number: Mapped[str] = mapped_column(String(100), nullable=False)
    coord_x: Mapped[float] = mapped_column(Float, nullable=False)
    coord_y: Mapped[float] = mapped_column(Float, nullable=False)

    wind_farm: Mapped[WindFarm] = relationship(back_populates="turbines")
    blades: Mapped[list[Blade]] = relationship(
        back_populates="turbine",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"Turbine(wtg_id={self.wtg_id!r}, "
            f"wt_installation_number={self.wt_installation_number!r}, "
            f"coord_x={self.coord_x!r}, coord_y={self.coord_y!r})"
        )


class Blade(Base):
    """Wind turbine blade attached to a turbine."""

    __tablename__ = "blades"

    blade_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    wtg_id: Mapped[int] = mapped_column(
        ForeignKey("turbines.wtg_id", ondelete="CASCADE"),
        nullable=False,
    )

    turbine: Mapped[Turbine] = relationship(back_populates="blades")
    damages: Mapped[list[Damage]] = relationship(
        back_populates="blade",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"Blade(blade_id={self.blade_id!r}, wtg_id={self.wtg_id!r})"


class Damage(Base):
    """Blade damage record from an inspection."""

    __tablename__ = "damages"

    damage_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    blade_id: Mapped[int] = mapped_column(
        ForeignKey("blades.blade_id", ondelete="CASCADE"),
        nullable=False,
    )
    inspection_date: Mapped[date] = mapped_column(Date, nullable=False)
    inspector_name: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[SeverityType] = mapped_column(
        Enum(
            SeverityType,
            name="severity_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    damage_type: Mapped[DamageType] = mapped_column(
        Enum(
            DamageType,
            name="damage_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    depth: Mapped[DepthType] = mapped_column(
        Enum(
            DepthType,
            name="depth_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    cs_position: Mapped[CSPosition] = mapped_column(
        Enum(
            CSPosition,
            name="cs_position_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    radial_position: Mapped[float] = mapped_column(Float, nullable=False)
    radial_area_size: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    density: Mapped[float] = mapped_column(Float, nullable=False)
    orientation: Mapped[float] = mapped_column(Float, nullable=False)
    photo: Mapped[str] = mapped_column(String(1024), nullable=False)
    inspection_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    blade: Mapped[Blade] = relationship(back_populates="damages")

    def __repr__(self) -> str:
        return (
            f"Damage(damage_id={self.damage_id!r}, blade_id={self.blade_id!r}, "
            f"severity={self.severity.value!r}, damage_type={self.damage_type.value!r})"
        )
