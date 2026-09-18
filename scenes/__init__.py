"""场景应用（Scene Applications）模块。

相对独立的场景应用体系，与 ``agent/``、``channel/``、``auth/`` 平级：

- ``scenes_config.json``  场景配置，默认分类与场景均为空
- ``skill_mapping.json``  场景技能映射，默认为空
- ``config.py``           配置路径解析、加载/校验、可访问性占位
- ``service.py``          场景目录 / 激活 / 会话上下文读写
- ``renderer.py``         工作台渲染分发（后端对应物，按需）
- ``api.py``              HTTP 处理器（ScenesHandler / SceneActivateHandler）
- ``workbenches/``        通用工作台组件

原内置业务场景、专用工作台和配套技能已移除；保留目录入口与通用基础能力。

设计约束（见 ``openspec/changes/port-scene-applications``）：
OneAgent 仅作参考，不复制运行时配置、凭据、客户数据或整文件覆盖。
"""
