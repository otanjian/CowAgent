"""场景应用（Scene Applications）模块。

相对独立的场景应用体系，与 ``agent/``、``channel/``、``auth/`` 平级：

- ``scenes_config.json``  适配版场景配置（10 分类 / 26 场景 + required_permission）
- ``skill_mapping.json``  场景 ``skill_name`` 到技能目录的映射表
- ``config.py``           配置路径解析、加载/校验、可访问性占位
- ``service.py``          场景目录 / 激活 / 会话上下文读写
- ``renderer.py``         工作台渲染分发（后端对应物，按需）
- ``api.py``              HTTP 处理器（ScenesHandler / SceneActivateHandler）
- ``skills/``             场景关联技能（按映射表搬入）
- ``workbenches/``        专业工作台组件（每场景一个子目录）

设计约束（见 ``openspec/changes/port-scene-applications``）：
OneAgent 仅作参考，不复制运行时配置、凭据、客户数据或整文件覆盖。
"""
