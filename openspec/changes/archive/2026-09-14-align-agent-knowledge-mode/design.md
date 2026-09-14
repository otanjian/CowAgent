# 设计：知识库模式按数据根归属判定

## 1. 问题

`_knowledge_mode_of` 用「是不是默认智能体」代替「数据根在哪」，把两件不同的事混为一谈：

- **事实**：智能体读哪个 `knowledge/`，由 `state_dir` 按「存在即自有，否则回落共享」解析。
- **投影**：配置面为了让用户看到 shared/own 开关，需要报出这个事实。

只要默认智能体还在实例根，两者恰好重合，所以旧规则看不出问题。一旦默认智能体被移入自己的 workspace（本实例即是：`team.json` 把 `my-assistant-admin` 的 `workspace` 设为 `<instance_root>/agents/my-assistant-admin`），它就持有实体 `knowledge/`，运行时读自己的库，而配置面仍报 shared——投影在说谎，且 `set_knowledge_mode` 以「是默认智能体」为由拒绝，用户在界面上无路可走。

## 2. 判定基准：共享库是哪一个目录

关键问题是「拿什么当共享库基准」。三个候选：

| 候选 | 语义 | 问题 |
| --- | --- | --- |
| 默认智能体的 workspace | 旧规则的隐含基准 | 默认智能体一旦移入私有工作区，基准就变成它自己的私有库 |
| 实例根（`agent_workspace`） | 部署级共享根 | 该布局下确实对，但与运行时实际物化共享资源的位置不一致——测试 fixture 立刻暴露：`state_dir` 把共享资源物化在默认智能体的 workspace，而非实例根 |
| `state_dir.shared_root()` | 运行时的单一事实来源 | 无——它就是运行时给回落智能体解析 `knowledge/` 的那个调用 |

选择 `state_dir.shared_root()`：

- 与运行时读到的目录必然一致（同一函数），不会出现「投影和实际读的不是同一个目录」。
- 数据库模式下它是租户的可信共享根，与知识库页的数据根解析（`_knowledge_workspace_root`）同源。
- 无租户身份时它是默认智能体的 workspace；在「默认智能体在实例根」这一经典布局下两者相等，与旧行为完全一致。
- 无法解析共享根（如租户没有共享根）时回落到实例根；再失败返回 `None`，此时任何实体 `knowledge/` 都判为「独立」——宁可少报共享，也不猜测。

## 3. 判定与守卫

```python
kdir = profile.workspace_path / "knowledge"
if kdir.is_symlink() or not kdir.is_dir():   # 符号链接 / 不存在 → 回落共享
    return "shared"
if _is_shared_base(kdir, shared_base):       # 就是共享库本身
    return "shared"
return "own"
```

`_is_shared_base` 用 `os.path.realpath` 比较，容忍实例根是符号链接或未规范化。共享库无法解析时 `_is_shared_base` 恒为 `False`——即回到「实体目录就是自有库」的既有行为。

`set_knowledge_mode` 的拒绝条件从「是不是默认智能体」换成「`knowledge/` 是不是共享库本身」：

```python
if kdir.is_dir() and not kdir.is_symlink() and self._is_shared_base(kdir, shared):
    raise AgentAdminError("this Agent's knowledge/ is the shared knowledge base")
```

拒绝前不做任何写操作，磁盘不变。符号链接（共享模式）与不存在的目录（可创建自有库）都允许切换，因此被移入私有工作区的默认智能体可以自由来回切。`own` 模式不再引用共享路径，`shared` 模式需要真实共享库，故共享库解析失败时直接报错而不是退化成一次带风险的写。

## 4. 为什么不保留「默认智能体」这个例外

默认智能体在实例根时，它的 `knowledge/` **就是**共享库，新规则自然报 shared 并拒绝切换——原例外要覆盖的情形被新规则精确覆盖，不需要额外分支。反过来，把「默认」当判定依据会在它搬家后立刻失真。用一句可检验的规则（数据根归属）替换一个身份判断（是否默认），也让配置面投影与运行时读取共用同一个事实。

投影侧同理：`_tenant_agents_admin_projection` 不再取 roster 的 `default_agent_id`，改为取共享库基准，于是它与 `snapshot()`/`knowledge_mode()` 完全同源。

## 5. 兼容与风险

- **无租户身份的调用方**（CLI、单元测试）：`state_dir.shared_root()` = 默认智能体 workspace。默认智能体报 shared，其余智能体按各自目录判定——与旧行为一致。
- **跨租户克隆**（平台管理员带租户身份克隆他租户的智能体）：`clone_agent` 用调用方视角解析的共享库判断源智能体模式。若源智能体所在租户的共享根与调用方不同，且源智能体恰好「就是」源租户的共享库，会被判成 own。这是既有粒度（旧代码用 roster 默认智能体 id）的同类边界，未扩大；同租户克隆完全精确。
- **数据安全**：新守卫堵上一条此前会误伤共享库的路径（非默认智能体的工作区被指向实例根时，旧规则报 own，切 shared 会把整个共享库 `rename` 成 `knowledge.own`）；切换仍沿用 `knowledge.own` 暂存/恢复的非破坏规则。
- **不涉及**：`knowledge_ids` 条目级绑定、共享库落盘形态、租户共享根解析规则、知识库页数据根规则（后者由 `open-tenant-knowledge-console` 承载）。
