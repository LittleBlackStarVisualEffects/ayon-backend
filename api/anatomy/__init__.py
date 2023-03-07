from fastapi import APIRouter, Depends, Response

from ayon_server.api.dependencies import dep_current_user
from ayon_server.entities import UserEntity
from ayon_server.exceptions import ForbiddenException, NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.settings import postprocess_settings_schema
from ayon_server.settings.anatomy import Anatomy
from ayon_server.types import Field, OPModel

router = APIRouter(tags=["Anatomy"], prefix="/anatomy")

VERSION = "1.0.0"


class AnatomyPresetListItem(OPModel):
    name: str = Field(..., title="Name of the preset")
    primary: bool = Field(..., title="Is this preset primary")
    version: str = Field(..., title="Version of the anatomy model")


class AnatomyPresetListModel(OPModel):
    version: str = Field(
        ...,
        title="Model version",
        description="Anatomy model version currently used in Ayon",
    )
    presets: list[AnatomyPresetListItem] = Field(
        default_factory=list,
        title="List of anatomy presets",
    )


@router.get("/schema")
async def get_anatomy_schema(
    user: UserEntity = Depends(dep_current_user),
):
    """Returns the anatomy JSON schema.

    The schema is used to display the anatomy preset editor form.
    """

    schema = Anatomy.schema()
    await postprocess_settings_schema(schema, Anatomy)
    return schema


@router.get("/presets")
async def get_anatomy_presets(
    user: UserEntity = Depends(dep_current_user),
) -> AnatomyPresetListModel:
    """Return a list of stored anatomy presets."""

    presets = []
    query = "SELECT * from anatomy_presets ORDER BY name, version"
    async for row in Postgres.iterate(query):
        presets.append(
            AnatomyPresetListItem(
                name=row["name"],
                primary=row["is_primary"],
                version=row["version"],
            )
        )
    return AnatomyPresetListModel(version=VERSION, presets=presets)


@router.get("/presets/{preset_name}")
async def get_anatomy_preset(
    preset_name: str,
    user: UserEntity = Depends(dep_current_user),
) -> Anatomy:
    """Returns the anatomy preset with the given name.

    Use `_` character as a preset name to return the default preset.
    """
    if preset_name == "_":
        tpl = Anatomy()
        return tpl
    query = "SELECT * FROM anatomy_presets WHERE name = $1 AND version = $2"
    async for row in Postgres.iterate(query, preset_name, VERSION):
        tpl = Anatomy(**row["data"])
        return tpl
    raise NotFoundException(f"Anatomy preset {preset_name} not found.")


@router.put("/presets/{preset_name}")
async def update_anatomy_preset(
    preset_name: str,
    preset: Anatomy,
    user: UserEntity = Depends(dep_current_user),
):
    """Create/update an anatomy preset with the given name."""

    if not user.is_manager:
        raise ForbiddenException("Only managers can update anatomy presets.")

    await Postgres.execute(
        """
        INSERT INTO anatomy_presets (name, version, data)
        VALUES ($1, $2, $3)
        ON CONFLICT (name, version) DO update
        SET data = $4
        """,
        preset_name,
        VERSION,
        preset.dict(),
        preset.dict(),
    )
    return Response(status_code=204)


@router.post("/presets/{preset_name}/primary")
async def set_primary_preset(
    preset_name: str,
    user: UserEntity = Depends(dep_current_user),
):
    """Set the given preset as the primary preset."""

    if not user.is_manager:
        raise ForbiddenException("Only managers can set primary preset.")

    async with Postgres.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE anatomy_presets
                SET is_primary = FALSE
                WHERE is_primary = TRUE
                """
            )
            if preset_name != "_":
                await conn.execute(
                    """
                    UPDATE anatomy_presets
                    SET is_primary = TRUE
                    WHERE name = $1
                    """,
                    preset_name,
                )

    return Response(status_code=204)


@router.delete("/presets/{preset_name}")
async def delete_anatomy_preset(
    preset_name: str,
    user: UserEntity = Depends(dep_current_user),
):
    """Delete the anatomy preset with the given name."""

    if not user.is_manager:
        raise ForbiddenException("Only managers can set primary preset.")

    async with Postgres.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM anatomy_presets
                WHERE name = $1
                """,
                preset_name,
            )

    return Response(status_code=204)
