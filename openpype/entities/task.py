from typing import Any

from nxtools import logging

from openpype.entities.core import ProjectLevelEntity, attribute_library
from openpype.entities.models import ModelSet
from openpype.exceptions import NotFoundException, OpenPypeException
from openpype.lib.postgres import Postgres
from openpype.types import ProjectLevelEntityType
from openpype.utils import EntityID


class TaskEntity(ProjectLevelEntity):
    entity_type: ProjectLevelEntityType = "task"
    model = ModelSet("task", attribute_library["task"])

    @classmethod
    async def load(
        cls,
        project_name: str,
        entity_id: str,
        transaction=None,
        for_update=False,
    ) -> "TaskEntity":
        """Load a task from the database by its project name and ID.

        This is reimplemented, because we need to select
        attributes inherited from the parent folder.
        """

        if EntityID.parse(entity_id) is None:
            raise ValueError(f"Invalid {cls.entity_type} ID specified")

        query = f"""
            SELECT
                t.id as id,
                t.name as name,
                t.label as label,
                t.task_type as task_type,
                t.assignees as assignees,
                t.folder_id as folder_id,
                t.attrib as attrib,
                t.data as data,
                t.active as active,
                t.created_at as created_at,
                t.updated_at as updated_at,
                t.status as status,
                t.tags as tags,
                ia.attrib AS inherited_attrib
            FROM project_{project_name}.tasks as t
            LEFT JOIN
                project_{project_name}.exported_attributes as ia
                ON t.folder_id = ia.folder_id
            WHERE t.id=$1
            {'FOR UPDATE OF t'
                if transaction and for_update else ''
            }
            """

        try:
            async for record in Postgres.iterate(query, entity_id):
                attrib: dict[str, Any] = {}
                if (ia := record["inherited_attrib"]) is not None:
                    attrib |= ia
                elif record["parent_id"] is not None:
                    logging.warning(
                        f"Task {record['id']} does not have inherited attributes."
                        "this shouldn't happen"
                    )
                attrib |= record["attrib"]
                own_attrib = list(record["attrib"].keys())
                payload = {**record, "attrib": attrib}
                return cls.from_record(
                    project_name=project_name,
                    payload=payload,
                    validate=False,
                    own_attrib=own_attrib,
                )
        except Postgres.UndefinedTableError:
            raise NotFoundException(f"Project {project_name} not found")
        raise NotFoundException("Entity not found")

    async def save(self, transaction=False) -> bool:
        if self.task_type is None:
            res = await transaction.fetch(
                f"""
                SELECT name from project_{self.project_name}.task_types
                ORDER BY position ASC LIMIT 1
                """
            )
            if not res:
                raise OpenPypeException("No task types defined")
            self.folder_type = res[0]["name"]

        return await super().save(transaction=transaction)

    #
    # Properties
    #

    @property
    def label(self):
        """Return the label of the folder."""
        return self.label or self.name

    @label.setter
    def label(self, value):
        """Set the label of the folder."""
        self.label = value

    @property
    def folder_id(self) -> str:
        return self._payload.folder_id

    @folder_id.setter
    def folder_id(self, value: str) -> None:
        self._payload.folder_id = value

    @property
    def task_type(self) -> str:
        return self._payload.task_type

    @task_type.setter
    def task_type(self, value: str) -> None:
        self._payload.task_type = value

    @property
    def assignees(self) -> list:
        return self._payload.assignees

    @assignees.setter
    def assignees(self, value: list) -> None:
        self._payload.assignees = value

    @property
    def entity_subtype(self) -> str:
        return self.task_type
