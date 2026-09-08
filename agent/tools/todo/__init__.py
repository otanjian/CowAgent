"""Minimal personal-todo agent tools.

Three actions only (per the spec):
- ``create``: save a todo for the current human owner.
- ``list``: list the current owner's todos.
- ``get``: read one of the current owner's todos.

No edit / status change / delete / assign / cross-tenant query. The tools reuse
the same ``TodoService`` as the Web API, resolve scope/owner from the *trusted*
request context (``RuntimeIdentity`` + a bound actor), and never trust a
model-supplied owner / tenant / directory. Calls from scheduler, external
channels, background runs or unauthenticated entry points are rejected because
no trusted Web delegation exists.
"""

from .todo_tool import TodoTool

__all__ = ["TodoTool"]
