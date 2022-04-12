import json

from pydantic import BaseModel


class FolderAccess(BaseModel):
    """Base class for folder access.

    Not to be used directly.
    """
    access_type: str

    def __hash__(self):
        return hash(json.dumps(self.dict()))


class AccessHierarchy(FolderAccess):
    access_type: str = "hierarchy"
    path: str


class AccessChildren(FolderAccess):
    access_type: str = "children"
    path: str


class AccessAssigned(FolderAccess):
    access_type: str = "assigned"
