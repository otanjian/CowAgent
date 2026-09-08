"""Personal todo items backed by a private SQLite database.

Two business tables only:
- ``todo_items``: the current state of one personally-owned todo.
- ``todo_events``: append-only processing history (also the minimal internal
  audit for this slice), written in the same transaction as the item update.

Ownership and scope are enforced at the service layer. This module only knows
how to read/write the two tables behind a well-defined ``scope``/``owner`` pair.
"""

from .store import (
    TodoStore,
    TodoStoreError,
    TodoNotFound,
    TodoConflict,
    TodoFieldError,
)
from .service import (
    TodoService,
    TodoServiceError,
    TodoDisabled,
    TodoUnauthorized,
    TodoPermissionDenied,
    TodoNotFoundError,
    TodoConflictError,
    TodoFieldValidationError,
    TodoUnavailable,
    resolve_todo_database_path,
    default_enabled,
    memory_db_path,
)

__all__ = [
    "TodoStore",
    "TodoStoreError",
    "TodoNotFound",
    "TodoConflict",
    "TodoFieldError",
    "TodoService",
    "TodoServiceError",
    "TodoDisabled",
    "TodoUnauthorized",
    "TodoPermissionDenied",
    "TodoNotFoundError",
    "TodoConflictError",
    "TodoFieldValidationError",
    "TodoUnavailable",
    "resolve_todo_database_path",
    "default_enabled",
    "memory_db_path",
]
