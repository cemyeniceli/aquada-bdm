from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, func, select
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


def create_dummy_database() -> None:
    """Create an in-memory test database, insert dummy data, and print examples."""
    engine = create_engine("sqlite+pysqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)

    severities = list(SeverityType)
    damage_types = list(DamageType)
    depths = list(DepthType)
    cs_positions = list(CSPosition)

    with Session(engine) as session:
        wind_farm = WindFarm(
            park_name="DTU Wind Park",
            park_type=ParkType.OFFSHORE,
            country="Denmark",
            operator="DTU",
            turbine_model="DTU 10MW RWT",
            blade_length=86.35,
        )

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

                for damage_number in range(1, 4):
                    enum_index = turbine_number + blade_number + damage_number
                    damage_id = blade_id * 100 + damage_number
                    damage = Damage(
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
                        analyzer_comment=(
                            f"Dummy analyzer comment for damage {damage_id}."
                        ),
                    )
                    blade.damages.append(damage)

        session.add(wind_farm)
        session.commit()

        print("Created in-memory database with dummy data.\n")
        print("Record counts:")
        print(
            f"  Wind farms: {session.scalar(select(func.count()).select_from(WindFarm))}"
        )
        print(
            f"  Turbines: {session.scalar(select(func.count()).select_from(Turbine))}"
        )
        print(f"  Blades: {session.scalar(select(func.count()).select_from(Blade))}")
        print(
            f"  Damages: {session.scalar(select(func.count()).select_from(Damage))}\n"
        )

        farm = session.scalars(select(WindFarm)).one()
        print(f"Wind farm: {farm.park_name} ({farm.park_type.value})")
        print(f"Country/operator: {farm.country} / {farm.operator}")
        print(
            f"Turbine model: {farm.turbine_model}, blade length: {farm.blade_length} m\n"
        )

        for turbine in farm.turbines:
            print(
                f"Turbine {turbine.wtg_id} ({turbine.wt_installation_number}) "
                f"at ({turbine.coord_x:.3f}, {turbine.coord_y:.3f})"
            )
            for blade in turbine.blades:
                damage_ids = [damage.damage_id for damage in blade.damages]
                print(f"  Blade {blade.blade_id}: damages {damage_ids}")

        example_damage = session.scalars(
            select(Damage).order_by(Damage.damage_id)
        ).first()
        if example_damage is not None:
            print("\nExample damage record:")
            print(f"  Damage ID: {example_damage.damage_id}")
            print(f"  Blade ID: {example_damage.blade_id}")
            print(f"  Inspection date: {example_damage.inspection_date:%d.%m.%Y}")
            print(f"  Inspector: {example_damage.inspector_name}")
            print(f"  Severity: {example_damage.severity.value}")
            print(f"  Damage type: {example_damage.damage_type.value}")
            print(f"  Depth: {example_damage.depth.value}")
            print(f"  CS position: {example_damage.cs_position.value}")
            print(f"  Radial position: {example_damage.radial_position} m")
            print(f"  Photo: {example_damage.photo}")
            print(f"  Inspection comment: {example_damage.inspection_comment}")
            print(f"  Analyzer comment: {example_damage.analyzer_comment}")


def main():
    create_dummy_database()


if __name__ == "__main__":
    main()
