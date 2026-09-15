# encoding:utf-8
"""成员「我的记忆」：可信作用域、版本条件与可恢复的索引一致性.

（change ``enable-member-personal-console``，stage 5）

为什么单独一个模块
------------------
现有 ``MemoryService(workspace_root)`` 是**该私有 Agent 记忆**的读取入口：它按 Agent
工作区列举 ``MEMORY.md`` 和 ``memory/*.md``。成员本人的长期记忆不是 Agent 的属性，
而是「当前租户 + 当前用户」的事实（``state_dir.user_root()`` /
``shared_root()/users/<user_id>``），跨本人获准的任意智能体可见。两者共用归属校验，
但存储根不同，所以入口和判决都必须分开，而不是给前者加一个 ``scope`` 参数。

本模块固定三条规则：

1. **归属来自身份，不来自请求。** 入口只接受相对标识，没有 agent、没有 user_id、
   没有绝对路径；解析结果永远落在调用者自己的用户域内，因此「读取他人个人记忆」
   没有可构造的地址。缺少可信租户/用户时一律拒绝。
2. **写入带版本条件。** 编辑、删除、清空都携带上一次读到的 revision，
   冲突返回 409 而不是覆盖新内容。
3. **修改与索引一致且可恢复。** 内容删除/清空后，索引清理在所有已知 Agent 的索引库
   上进行；任一失败不算成功，失败标签进入待重试记录，并在检索入口继续屏蔽，
   直到重试成功——删除的正文不会从旧索引返回。
4. **清空推进作用域版本。** 清空前排队的自动固化任务（flush/dream）携带派发时的
   版本，版本过期即拒绝写回；清空后的新任务按新版本正常固化。

5. **路径归属在使用时校验。** 入口只接受相对标识，解析与读写都经
   ``common.safe_fs`` 的锚定访问：逐段 ``O_NOFOLLOW`` 打开、软链接（条目、
   中间目录或根）与 ``..``/绝对路径一律拒绝。校验过的目录描述符就是实际使用的
   目录，因此「先检查后替换」不能把操作重定向到根外。

6. **正文、索引与清空共用一个操作版本。** 作用域状态里保存单调递增的
   ``op_version``（每次保存/删除/清空都 +1）与 ``generation``（仅清空 +1）。
   变更在提交正文时先登记发布意图，索引发布前后都重新校验版本；版本已变的发布
   不得写回旧内容，也不得删除较新版本的索引。发布意图持久化，进程中断后由
   ``recover_incomplete_publish`` 提升为待重试并继续屏蔽，重启不会把未完成
   的发布当作成功。

索引标签（与 ``MemoryManager.sync`` 使用的标签一致）::

    MEMORY.md          -> memory/users/<user_id>/MEMORY.md
    memory/notes.md    -> memory/users/<user_id>/notes.md

单写者约束
----------
``_entry_lock`` 是进程内的串行点：正文提交与索引发布在同一个临界区内，因此
「正文已保存 → 清空删除正文并清理索引 → 原保存重新写回旧索引」这一窗口不存在。
跨进程没有文件锁，因此**同一用户的作用域不得由多个进程同时写入**；部署必须保证
一个用户域只有一个写入进程，否则 :func:`read_scope_state` 记录的 ``op_version``
只能做到"过期发布被拒绝"，不能做到原子性。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from common import safe_fs
from common.log import logger
from common.safe_fs import UnsafePathError

MAIN_ENTRY_ID = "MEMORY.md"

#: Only these two shapes are addressable. Deliberately narrow: every wider
#: grammar (nested dirs, other extensions, absolute paths) is a way for a
#: caller to name something that is not a personal memory entry.
_ENTRY_ID_RE = re.compile(r"^(?:MEMORY\.md|memory/[A-Za-z0-9._-]+\.md)$")

#: The per-scope marker file, beside the memory it governs. Holds the clear
#: generation and the index labels awaiting a retried purge.
_SCOPE_FILE = ".memory-scope.json"

#: Guards read-modify-write of the scope file within one process.
_scope_lock = threading.RLock()

#: Serialises the read-compare-write of an entry's version condition. The
#: console is served by one process, so this is what makes "two pages save the
#: same revision" resolve to exactly one winner instead of both passing the
#: check before either write lands.
_entry_lock = threading.RLock()


class PersonalMemoryError(Exception):
    """A refused personal-memory operation.

    ``code`` is the machine-readable reason the console branches on;
    ``status`` is the HTTP status the handler should answer with.
    """

    def __init__(self, message: str, *, code: str = "error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _revision_of(text: str) -> str:
    """Content revision: a hash, not a timestamp.

    Two edits within the same second must not look like the same version, and a
    timestamp can be preserved by a copy. The hash changes whenever the bytes do.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` without ever exposing a half-written file."""
    safe_fs.write_text_atomic(path.parent, path.name, text)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- scope state: clear generation + operation version + index repairs -------


def _default_scope_state() -> Dict[str, Any]:
    return {"generation": 0, "op_version": 0, "cleared_at": None,
            "pending_index": [], "publishing": None}


def _coerce_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_publishing(value) -> Optional[Dict[str, Any]]:
    """A persisted publish intent, or ``None`` when absent/unreadable.

    A malformed record is dropped rather than guessed at: the labels it named
    are unknown, and inventing a set would either mask the wrong entries or
    claim a publish completed that never did.
    """
    if not isinstance(value, dict):
        return None
    labels = value.get("labels")
    if not isinstance(labels, list):
        return None
    return {
        "pid": _coerce_int(value.get("pid")),
        "token": _coerce_int(value.get("token")),
        "kind": str(value.get("kind") or ""),
        "labels": [str(x) for x in labels],
        "at": value.get("at"),
    }


def read_scope_state(root: Path) -> Dict[str, Any]:
    """Read the scope marker, tolerating absence and corruption.

    A corrupt marker must not make the user's memory unreadable; it degrades to
    the default (no generation, nothing pending), and the next write repairs it.
    """
    try:
        raw = safe_fs.read_text(Path(root), _SCOPE_FILE)
    except (UnsafePathError, OSError) as e:
        logger.warning("[PersonalMemory] unreadable scope marker: %s", e)
        return _default_scope_state()
    if raw is None:
        return _default_scope_state()
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.warning("[PersonalMemory] unreadable scope marker: %s", e)
        return _default_scope_state()
    if not isinstance(data, dict):
        return _default_scope_state()
    state = _default_scope_state()
    state["generation"] = _coerce_int(data.get("generation"))
    state["op_version"] = _coerce_int(data.get("op_version"))
    state["cleared_at"] = data.get("cleared_at")
    pending = data.get("pending_index")
    state["pending_index"] = ([str(x) for x in pending]
                              if isinstance(pending, list) else [])
    state["publishing"] = _coerce_publishing(data.get("publishing"))
    return state


def _write_scope_state(root: Path, state: Dict[str, Any]) -> None:
    payload = dict(state)
    payload.pop("incomplete", None)
    safe_fs.write_text_atomic(
        Path(root), _SCOPE_FILE,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _scope_update(root: Path, mutate: Callable[[Dict[str, Any]], Any]) -> Any:
    """Read-modify-write the scope marker under :data:`_scope_lock`."""
    with _scope_lock:
        state = read_scope_state(root)
        result = mutate(state)
        _write_scope_state(root, state)
        return result


def _stale_publish(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The publish intent left behind by another (dead) process, if any."""
    publishing = state.get("publishing")
    if not isinstance(publishing, dict):
        return None
    if _coerce_int(publishing.get("pid")) == os.getpid():
        # This process is still inside the operation that recorded it.
        return None
    return publishing


def read_scope_generation(identity=None) -> int:
    """The clear generation for ``identity``'s personal memory (0 when unset)."""
    from common import state_dir
    try:
        root = Path(state_dir.user_root(identity))
    except Exception:
        return 0
    return _coerce_int(read_scope_state(root).get("generation"))


def read_scope_token(identity=None) -> Dict[str, int]:
    """``{"generation": n, "op_version": n}`` for a publisher to carry.

    A consolidation task that captures this pair can refuse to publish when
    either has moved on: the clear generation catches "the user cleared", and
    the operation version catches "another writer committed meanwhile".
    """
    from common import state_dir
    try:
        root = Path(state_dir.user_root(identity))
    except Exception:
        return {"generation": 0, "op_version": 0}
    state = read_scope_state(root)
    return {"generation": _coerce_int(state.get("generation")),
            "op_version": _coerce_int(state.get("op_version"))}


def scope_publish_is_current(identity=None, token: Optional[Dict[str, int]] = None
                             ) -> bool:
    """True when ``token`` still matches the persisted scope state."""
    if not token:
        return True
    return read_scope_token(identity) == {
        "generation": _coerce_int(token.get("generation")),
        "op_version": _coerce_int(token.get("op_version")),
    }


def pending_index_labels(identity=None) -> Set[str]:
    """Labels whose index contents may not be trusted yet.

    The retrieval entry point subtracts these from its results, so a failed
    purge degrades to "not found" rather than "deleted body still returned" --
    and so does a publish that was interrupted before it could be confirmed.
    """
    from common import state_dir
    try:
        root = Path(state_dir.user_root(identity))
    except Exception:
        return set()
    state = read_scope_state(root)
    labels = set(str(x) for x in state.get("pending_index") or [])
    stale = _stale_publish(state)
    if stale:
        labels.update(stale["labels"])
    return labels


def scope_incomplete(identity=None) -> bool:
    """True when a previous process left a publish unreconciled."""
    from common import state_dir
    try:
        root = Path(state_dir.user_root(identity))
    except Exception:
        return False
    return _stale_publish(read_scope_state(root)) is not None


def recover_incomplete_publish(root: Path) -> bool:
    """Promote an interrupted publish into the pending journal.

    Called by the service before it serves or mutates a scope. The interrupted
    labels stay masked from retrieval and are reported as pending, so a restart
    never turns "the index publish may not have completed" into "everything is
    consistent". Idempotent.
    """
    root = Path(root)

    def _mutate(state: Dict[str, Any]) -> bool:
        stale = _stale_publish(state)
        if stale is None:
            return False
        merged = list(dict.fromkeys(
            list(state.get("pending_index") or []) + list(stale["labels"])))
        state["pending_index"] = merged
        state["publishing"] = None
        return True

    return bool(_scope_update(root, _mutate))


def _record_pending(root: Path, labels: Iterable[str]) -> None:
    labels = list(labels)
    if not labels:
        return

    def _mutate(state: Dict[str, Any]) -> None:
        state["pending_index"] = list(dict.fromkeys(
            list(state.get("pending_index") or []) + labels))

    _scope_update(root, _mutate)


def _clear_pending(root: Path, done: Iterable[str]) -> None:
    done = set(done)
    if not done:
        return

    def _mutate(state: Dict[str, Any]) -> None:
        state["pending_index"] = [x for x in state.get("pending_index") or []
                                  if x not in done]

    _scope_update(root, _mutate)


# --- index plumbing ---------------------------------------------------------


def _purge_label(db_path, label: str) -> None:
    """Remove one label's rows from one index database.

    Module-level so a test (or an operator tool) can substitute a failing
    implementation and prove the caller degrades correctly. Raises on failure.
    """
    from agent.memory.storage import MemoryStorage
    if not Path(db_path).exists():
        return
    storage = MemoryStorage(Path(db_path))
    try:
        storage.delete_by_path(label)
    finally:
        storage.close()


def _index_label(db_path, label: str, text: str, user_id: str) -> None:
    """Replace one label's rows with ``text``'s current chunks.

    Embeddings are deliberately not synthesised here: this process does not own
    the Agent's embedding provider, and writing an unverified vector is worse
    than writing none. The file metadata row is therefore left absent, so the
    Agent's own next ``sync()`` re-reads the file and adds vectors.
    """
    from agent.memory.chunker import TextChunker
    from agent.memory.storage import MemoryStorage, MemoryChunk

    path = Path(db_path)
    # The first edit on a fresh install arrives before that Agent has ever
    # synced, so the index database (and its directory) may not exist yet.
    path.parent.mkdir(parents=True, exist_ok=True)
    storage = MemoryStorage(path)
    try:
        storage.delete_by_path(label)
        chunks = TextChunker().chunk_markdown(text)
        if not chunks:
            return
        batch = []
        for chunk in chunks:
            chunk_id = hashlib.md5(
                f"{label}:{chunk.start_line}:{chunk.end_line}".encode("utf-8")
            ).hexdigest()
            batch.append(MemoryChunk(
                id=chunk_id,
                user_id=user_id,
                scope="user",
                source="memory",
                path=label,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                text=chunk.text,
                embedding=None,
                hash=MemoryStorage.compute_hash(chunk.text),
                metadata=None,
            ))
        storage.save_chunks_batch(batch)
    finally:
        storage.close()


class PersonalMemoryService:
    """List/read/edit/delete/clear for one (tenant, user) personal memory."""

    def __init__(self, identity=None, *, index_dbs=None, registry_provider=None):
        self._explicit = identity
        self._index_dbs_override = list(index_dbs) if index_dbs is not None else None
        self._registry_provider = registry_provider

    # -- identity / paths ----------------------------------------------------

    def _identity(self):
        from common.runtime_identity import current_identity
        return self._explicit if self._explicit is not None else current_identity()

    def _require_scope(self):
        """The trusted (tenant, user) this service may act for."""
        ident = self._identity()
        user_id = getattr(ident, "user_id", None)
        tenant_id = getattr(ident, "tenant_id", None)
        if not user_id or not tenant_id:
            # No verified tenant+user means there is no personal memory to
            # address. Guessing "the default user" here is exactly the bug the
            # scope rule exists to prevent.
            raise PersonalMemoryError(
                "本人记忆需要已验证的租户与用户身份",
                code="no_identity", status=403)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", str(user_id)):
            raise PersonalMemoryError("无效的用户标识", code="no_identity", status=403)
        return ident

    def user_root(self) -> Path:
        from common import state_dir
        root = Path(state_dir.user_root(self._identity()))
        # A symlinked user root would make every containment promise in
        # ``safe_fs`` meaningless: the caller would be writing "inside" a
        # directory that actually points somewhere else.
        if os.path.islink(root):
            raise PersonalMemoryError(
                "本人记忆根目录被替换", code="unsafe_path", status=403)
        return root

    def _entry_relative(self, entry_id: str) -> str:
        """Validate the addressable id and return the path relative to the root.

        The grammar and the anchored access are deliberately separate: this
        only decides *which* entry is named, ``safe_fs`` decides whether the
        name still resolves to a plain file inside the caller's own root.
        """
        self._require_scope()
        if not isinstance(entry_id, str) or not _ENTRY_ID_RE.match(entry_id):
            raise PersonalMemoryError(
                "无效的记忆标识", code="invalid_entry", status=400)
        return entry_id if entry_id != MAIN_ENTRY_ID else MAIN_ENTRY_ID

    def _target(self, entry_id: str) -> Path:
        """Absolute path of an entry after full anchored validation.

        Kept for callers that need to name the file (logging, display); every
        read/write/delete in this module goes through ``common.safe_fs`` so the
        validated directory is the directory that is used.
        """
        relative = self._entry_relative(entry_id)
        try:
            return Path(safe_fs.resolve_within(self.user_root(), relative))
        except UnsafePathError as e:
            raise PersonalMemoryError(
                "本人记忆路径被替换，已拒绝访问",
                code="unsafe_path", status=403) from e

    def _read_entry(self, entry_id: str) -> Optional[str]:
        relative = self._entry_relative(entry_id)
        try:
            return safe_fs.read_text(self.user_root(), relative)
        except UnsafePathError as e:
            raise PersonalMemoryError(
                "本人记忆路径被替换，已拒绝访问",
                code="unsafe_path", status=403) from e

    def _remove_entry(self, entry_id: str) -> bool:
        relative = self._entry_relative(entry_id)
        try:
            return safe_fs.unlink(self.user_root(), relative)
        except UnsafePathError as e:
            raise PersonalMemoryError(
                "本人记忆路径被替换，已拒绝访问",
                code="unsafe_path", status=403) from e

    def label_for(self, entry_id: str) -> str:
        """The index label ``MemoryManager.sync`` uses for this entry."""
        ident = self._require_scope()
        if entry_id == MAIN_ENTRY_ID:
            return f"memory/users/{ident.user_id}/{MAIN_ENTRY_ID}"
        return f"memory/users/{ident.user_id}/{entry_id[len('memory/'):]}"

    # -- listing / reading ---------------------------------------------------

    def list_entries(self) -> List[Dict[str, Any]]:
        self._require_scope()
        root = self.user_root()
        recover_incomplete_publish(root)
        entries: List[Dict[str, Any]] = []
        if safe_fs.is_file(root, MAIN_ENTRY_ID):
            entries.append(self._entry_info(MAIN_ENTRY_ID))
        try:
            names = safe_fs.list_names(root, "memory", suffix=".md",
                                       skip_dotfiles=True)
        except (UnsafePathError, OSError):
            names = []
        for name in sorted(names, reverse=True):
            entry_id = f"memory/{name}"
            if safe_fs.is_file(root, entry_id):
                entries.append(self._entry_info(entry_id))
        return entries

    def _entry_info(self, entry_id: str) -> Dict[str, Any]:
        root = self.user_root()
        text = self._read_entry(entry_id)
        if text is None:
            text = ""
        info = safe_fs.stat(root, self._entry_relative(entry_id))
        return {
            "id": entry_id,
            "size": len(text.encode("utf-8")),
            "updated_at": datetime.fromtimestamp(
                info.st_mtime).strftime("%Y-%m-%d %H:%M:%S") if info else "",
            "revision": _revision_of(text),
            # The verbs the console may offer for this row (task 8.1). Editing
            # and deleting are the same operation set the write paths enforce
            # with a revision check, so the page never invents a verb the API
            # would refuse; ``create`` is not a memory concept here (the entries
            # are files under the member's own root).
            "actions": {"edit": True, "delete": True},
        }

    def read(self, entry_id: str) -> Dict[str, Any]:
        self._require_scope()
        recover_incomplete_publish(self.user_root())
        text = self._read_entry(entry_id)
        if text is None:
            # A valid id that has no file is "empty", not an error: the console
            # shows an empty personal memory on a fresh account, and the same
            # answer must not distinguish "absent" from "another user's".
            return {"id": entry_id, "content": "", "revision": None}
        return {"id": entry_id, "content": text, "revision": _revision_of(text)}

    # -- writing -------------------------------------------------------------

    @staticmethod
    def _require_write_capability() -> None:
        """Refuse *adding* memory when ``personal_memory_write`` is withdrawn.

        Task 9.1. Only the adding path is gated: reading, deleting and clearing
        stay reachable, so a deployment that withdraws the capability cannot
        strand a member with personal memory they may no longer retract. An
        unevaluable switch is a closed one — nothing here may fail open.
        """
        try:
            from auth.policy import personal_capability_enabled
        except Exception:  # noqa: BLE001 - fail closed
            enabled = False
        else:
            enabled = personal_capability_enabled("personal_memory_write")
        if not enabled:
            raise PersonalMemoryError(
                "本人记忆写入尚未在本部署启用", code="capability_disabled", status=403)

    def save(self, entry_id: str, content, expected_revision: Optional[str] = None
             ) -> Dict[str, Any]:
        self._require_write_capability()
        if not isinstance(content, str):
            raise PersonalMemoryError(
                "记忆内容必须是文本", code="invalid_content", status=400)
        relative = self._entry_relative(entry_id)
        root = self.user_root()
        try:
            # The whole mutation -- version check, body commit and index
            # publish -- is one critical section. Releasing the lock between the
            # two is what allowed "body saved -> clear removed it and purged the
            # index -> the original save wrote the old index back".
            with _entry_lock:
                recover_incomplete_publish(root)
                current = self._read_entry(entry_id)
                current_revision = (_revision_of(current)
                                    if current is not None else None)
                if current_revision is not None:
                    if not expected_revision:
                        raise PersonalMemoryError(
                            "该条目已存在，保存需要当前版本",
                            code="revision_required", status=409)
                    if expected_revision != current_revision:
                        raise PersonalMemoryError(
                            "记忆已被其他页面修改，请刷新后重试",
                            code="stale_revision", status=409)
                elif expected_revision:
                    # A caller that believes it is editing something that is
                    # gone must not silently create it.
                    raise PersonalMemoryError(
                        "记忆条目已不存在，请刷新后重试",
                        code="stale_revision", status=409)

                token = self._begin_mutation("save", [self.label_for(entry_id)])
                try:
                    safe_fs.write_text_atomic(root, relative, content)
                except UnsafePathError as e:
                    self._abort_mutation(token)
                    raise PersonalMemoryError(
                        "本人记忆路径被替换，已拒绝写入",
                        code="unsafe_path", status=403) from e
                except BaseException:
                    self._abort_mutation(token)
                    raise
                index_state = self._after_write(entry_id, content, token)
                self._finish_mutation(token, [self.label_for(entry_id)],
                                      index_state)
        except UnsafePathError as e:
            raise PersonalMemoryError(
                "本人记忆路径被替换，已拒绝访问",
                code="unsafe_path", status=403) from e
        revision = _revision_of(content)
        return {"id": entry_id, "revision": revision,
                "size": len(content.encode("utf-8")),
                "index_state": index_state}

    def delete(self, entry_id: str, expected_revision: Optional[str] = None
               ) -> Dict[str, Any]:
        self._require_scope()
        root = self.user_root()
        label = self.label_for(entry_id)
        with _entry_lock:
            recover_incomplete_publish(root)
            current = self._read_entry(entry_id)
            if current is None:
                raise PersonalMemoryError("记忆条目不存在", code="not_found",
                                          status=404)
            if expected_revision and expected_revision != _revision_of(current):
                raise PersonalMemoryError(
                    "记忆已被其他页面修改，请刷新后重试",
                    code="stale_revision", status=409)
            token = self._begin_mutation("delete", [label])
            try:
                self._remove_entry(entry_id)
            except BaseException:
                self._abort_mutation(token)
                raise
            index_state = self._after_remove([label], token)
            self._finish_mutation(token, [label], index_state)
        return {"id": entry_id, "index_state": index_state}

    def clear(self, expected_revision: Optional[str] = None) -> Dict[str, Any]:
        with _entry_lock:
            return self._clear_locked(expected_revision)

    def _clear_locked(self, expected_revision: Optional[str]) -> Dict[str, Any]:
        root = self.user_root()
        recover_incomplete_publish(root)
        entries = self.list_entries()
        if expected_revision is not None:
            combined = self._collection_revision(entries)
            if expected_revision != combined:
                raise PersonalMemoryError(
                    "记忆已被其他页面修改，请刷新后重试",
                    code="stale_revision", status=409)

        labels = [self.label_for(entry["id"]) for entry in entries]
        # Order matters: the generation and the publish intent are recorded
        # *before* anything is removed. A queued consolidation task that reads
        # the version after this point sees a stale value and refuses; one that
        # already passed the check and is mid-write is caught by the pending
        # filter, because its content is gone and its label stays masked.
        token = self._begin_mutation("clear", labels, bump_generation=True)

        for entry in entries:
            try:
                self._remove_entry(entry["id"])
            except OSError as e:
                logger.warning("[PersonalMemory] clear unlink failed %s: %s",
                               entry["id"], e)
                self._abort_mutation(token, labels)
                raise PersonalMemoryError(
                    "清空未完成，请重试", code="clear_incomplete", status=500)
        # Anything left over in the personal dir (dream diaries, evolution logs)
        # is not user-authored memory and is deliberately not touched here.

        index_state = self._after_remove(labels, token)
        self._finish_mutation(token, labels, index_state)
        return {"status": "success" if index_state == "ok" else "incomplete",
                "index_state": index_state,
                "removed": len(entries),
                "generation": self.scope_generation()}

    def _collection_revision(self, entries: List[Dict[str, Any]]) -> str:
        payload = "\n".join(f"{e['id']}:{e['revision']}" for e in sorted(
            entries, key=lambda x: x["id"]))
        return _revision_of(payload)

    # -- scope generation / operation version --------------------------------

    def scope_generation(self) -> int:
        self._require_scope()
        return _coerce_int(read_scope_state(self.user_root()).get("generation"))

    def scope_token(self) -> Dict[str, int]:
        """The version pair a publisher must still match when it commits."""
        self._require_scope()
        state = read_scope_state(self.user_root())
        return {"generation": _coerce_int(state.get("generation")),
                "op_version": _coerce_int(state.get("op_version"))}

    def _begin_mutation(self, kind: str, labels: List[str], *,
                        bump_generation: bool = False) -> int:
        """Record the intent to publish and return its operation token.

        Must be called with :data:`_entry_lock` held. The token is the value of
        ``op_version`` this operation commits at; any publisher that finds a
        different value has been overtaken and must not write.
        """
        root = self.user_root()

        def _mutate(state: Dict[str, Any]) -> int:
            state["op_version"] = _coerce_int(state.get("op_version")) + 1
            if bump_generation:
                state["generation"] = _coerce_int(state.get("generation")) + 1
                state["cleared_at"] = _now()
            state["publishing"] = {
                "pid": os.getpid(),
                "token": state["op_version"],
                "kind": kind,
                "labels": list(labels),
                "at": _now(),
            }
            return state["op_version"]

        return int(_scope_update(root, _mutate))

    def _abort_mutation(self, token: int, labels: Optional[List[str]] = None
                        ) -> None:
        """Release the publish intent after a failure, keeping labels masked."""
        root = self.user_root()

        def _mutate(state: Dict[str, Any]) -> None:
            publishing = state.get("publishing")
            if (isinstance(publishing, dict)
                    and _coerce_int(publishing.get("token")) == token):
                state["publishing"] = None
                labels_ = publishing.get("labels") or []
            else:
                labels_ = labels or []
            if labels_:
                state["pending_index"] = list(dict.fromkeys(
                    list(state.get("pending_index") or []) + list(labels_)))

        try:
            _scope_update(root, _mutate)
        except Exception as e:  # noqa: BLE001 - abort must not mask the cause
            logger.warning("[PersonalMemory] abort bookkeeping failed: %s", e)

    def _finish_mutation(self, token: int, labels: List[str],
                         index_state: str) -> None:
        root = self.user_root()

        def _mutate(state: Dict[str, Any]) -> None:
            publishing = state.get("publishing")
            if (isinstance(publishing, dict)
                    and _coerce_int(publishing.get("token")) == token):
                state["publishing"] = None
            if index_state == "ok":
                done = set(labels)
                state["pending_index"] = [x for x in state.get("pending_index") or []
                                          if x not in done]
            else:
                state["pending_index"] = list(dict.fromkeys(
                    list(state.get("pending_index") or []) + list(labels)))

        _scope_update(root, _mutate)

    # -- index maintenance ---------------------------------------------------

    def _index_dbs(self) -> List[Path]:
        """Every index database that may hold a copy of this user's memory.

        Each Agent keeps its own index, which is what makes personal memory
        visible from every Agent the user may use — and therefore what makes a
        purge in one Agent's database insufficient.
        """
        if self._index_dbs_override is not None:
            return [Path(p) for p in self._index_dbs_override]
        db_paths: List[Path] = []

        def _add(workspace) -> None:
            if not workspace:
                return
            path = Path(workspace) / "memory" / "long-term" / "index.db"
            if path not in db_paths:
                db_paths.append(path)

        try:
            from common import state_dir
            _add(Path(state_dir.state_root(self._identity())))
        except Exception as e:
            logger.debug("[PersonalMemory] current agent index unresolved: %s", e)
        try:
            provider = self._registry_provider
            if provider is None:
                from agent.registry import get_agent_registry
                provider = get_agent_registry()
            for profile in provider.list(include_disabled=True):
                _add(getattr(profile, "workspace", None))
        except Exception as e:
            logger.debug("[PersonalMemory] agent index scan failed: %s", e)
        return db_paths

    def _publish_is_current(self, token: int) -> bool:
        """True when the operation that produced ``token`` is still the latest.

        A publisher that has been overtaken (a clear, or another writer's
        commit) must not write rows back: doing so would restore content the
        user just removed. The check runs before *every* index write, so a
        partially applied publish stops at the first database rather than
        spreading stale rows.
        """
        state = read_scope_state(self.user_root())
        return _coerce_int(state.get("op_version")) == token

    def _after_write(self, entry_id: str, content: str, token: int) -> str:
        """Refresh the edited entry in every known index; report the outcome."""
        ident = self._require_scope()
        label = self.label_for(entry_id)
        failures = []
        for db in self._index_dbs():
            if not self._publish_is_current(token):
                # The operation is obsolete: something newer committed while
                # this publish was in flight (only reachable in a
                # multi-writer deployment, which this module refuses to
                # pretend to support atomically). Leave the label masked so no
                # stale row is served, and never claim success.
                _record_pending(self.user_root(), [label])
                return "obsolete"
            try:
                _index_label(db, label, content, str(ident.user_id))
            except Exception as e:
                logger.warning("[PersonalMemory] index refresh failed %s %s: %s",
                               db, label, e)
                failures.append(label)
        if failures:
            # The *content* is saved; the index is behind. The next sync would
            # repair it anyway, but recording it keeps the state honest and
            # makes it visible to the caller.
            _record_pending(self.user_root(), failures)
            return "pending"
        return "ok"

    def _after_remove(self, labels: List[str], token: int) -> str:
        """Purge labels from every known index; report the outcome."""
        failures: List[str] = []
        for db in self._index_dbs():
            if not self._publish_is_current(token):
                _record_pending(self.user_root(), labels)
                return "obsolete"
            for label in labels:
                try:
                    _purge_label(db, label)
                except Exception as e:
                    logger.warning("[PersonalMemory] index purge failed %s %s: %s",
                                   db, label, e)
                    failures.append(label)
        if failures:
            # Fail closed: the rows may survive, so the labels stay in the
            # pending journal and the retrieval entry point keeps hiding them
            # until a retry succeeds. Reporting "ok" here would claim a
            # consistency the store does not have.
            _record_pending(self.user_root(), failures)
            return "pending"
        return "ok"

    def scope_status(self) -> Dict[str, Any]:
        """Recovery-visible scope state for the console (task 5.7)."""
        self._require_scope()
        root = self.user_root()
        recovered = recover_incomplete_publish(root)
        state = read_scope_state(root)
        return {
            "generation": _coerce_int(state.get("generation")),
            "op_version": _coerce_int(state.get("op_version")),
            "pending": list(state.get("pending_index") or []),
            "recovered_incomplete_publish": recovered,
        }

    def retry_pending_index(self) -> Dict[str, Any]:
        """Retry the recorded purges; only the target labels are touched."""
        self._require_scope()
        root = self.user_root()
        recover_incomplete_publish(root)
        pending = list(read_scope_state(root).get("pending_index") or [])
        if not pending:
            return {"pending": [], "index_state": "ok"}
        done: List[str] = []
        still: List[str] = []
        for label in pending:
            ok = True
            for db in self._index_dbs():
                try:
                    _purge_label(db, label)
                except Exception as e:
                    logger.warning("[PersonalMemory] retry purge failed %s %s: %s",
                                   db, label, e)
                    ok = False
            (done if ok else still).append(label)
        if still:
            return {"pending": still, "index_state": "pending"}
        _clear_pending(root, done)
        return {"pending": [], "index_state": "ok"}
