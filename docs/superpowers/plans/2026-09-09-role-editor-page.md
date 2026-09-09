# Role Editor Page Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Demo source of truth: `doc/demo.html`.

**Goal:** Replace role create/edit/copy modal with a full-page tabbed editor matching the approved demo.

**Architecture:** Stay on `roles` view; swap list ↔ editor panels. Tabs: 基本信息(+权限) · 菜单 · 技能 · 工具 · 智能体 · 模型(分配+默认). Reuse `_resourceState`, catalog APIs, unified save payload. Keep `admin-modal` for other entities.

**Tech Stack:** Vanilla JS (`identity-admin.js`), `chat.html`, `console.js` i18n, `console.css`, Node `test_identity_admin_frontend.cjs`.

---

### Task 1: Failing tests for page editor
### Task 2: HTML shell + i18n + CSS
### Task 3: Open/close editor, tabs, dirty guard
### Task 4: Permissions grouped + resource/model tabs + save
### Task 5: Update remaining tests; verify
