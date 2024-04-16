from ayon_server.activities.models import ActivityReferenceModel
from ayon_server.entities import TaskEntity, VersionEntity
from ayon_server.entities.core import ProjectLevelEntity
from ayon_server.lib.postgres import Postgres


async def get_references_from_task(task: TaskEntity) -> list[ActivityReferenceModel]:
    references = []

    for assignee in task.assignees:
        references.append(
            ActivityReferenceModel(
                entity_type="user",
                entity_name=assignee,
                entity_id=None,
                reference_type="relation",
            )
        )

    async for row in Postgres.iterate(
        f"""
        SELECT id FROM project_{task.project_name}.versions WHERE task_id = $1
        """,
        task.id,
    ):
        references.append(
            ActivityReferenceModel(
                entity_type="version",
                entity_name=None,
                entity_id=row["id"],
                reference_type="relation",
            )
        )

    return references


async def get_references_from_version(
    version: VersionEntity,
) -> list[ActivityReferenceModel]:
    references = []

    if version.author:
        references.append(
            ActivityReferenceModel(
                entity_type="user",
                entity_name=version.author,
                entity_id=None,
                reference_type="relation",
            )
        )

    if version.task_id:
        references.append(
            ActivityReferenceModel(
                entity_type="task",
                entity_name=None,
                entity_id=version.task_id,
                reference_type="relation",
            )
        )

    return references


async def get_references_from_entity(
    entity: ProjectLevelEntity,
) -> list[ActivityReferenceModel]:
    if isinstance(entity, TaskEntity):
        return await get_references_from_task(entity)
    elif isinstance(entity, VersionEntity):
        return await get_references_from_version(entity)
    else:
        return []