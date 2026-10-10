# 文件格式规范

## 1. 原始日志 — `memory/YYYY-MM-DD.md`

```text
# Daily log for YYYY-MM-DD

## [HH:MM] User
<用户消息内容>

## [HH:MM] Assistant
<助手的完整回复>

## [HH:MM] User
...

## [HH:MM] Assistant
...
```

**规则：**
- 文件名严格使用 ISO 日期格式 `YYYY-MM-DD`
- 每个对话轮次为一个块，包含 User 和 Assistant 两个条目
- 时间戳使用 24 小时制 `HH:MM`
- 先写 User 消息，再写 Assistant 回复，两者时间戳相同（代表同一轮）
- 文件不存在时自动创建带标题头

---

## 2. 分解笔记 — `memory/YYYY-MM-DD-decompose.md`

```text
# Decompose for YYYY-MM-DD

## 预读指南
此文件帮助你快速决定是否需要阅读完整的原始日志。它包含关键词和一份摘要。

## 关键词
- 关键词1
- 关键词2
- ...

## 摘要
<对当天事件的简要概括>
```

**生成时机：** 对应原始日志的第二天生成
**目的：** 快速索引，无需逐一阅读原始日志
**关键词来源：** 中文按**词级**切分（优先 `jieba`，未安装时回退到内置词典最长匹配），不再产生 `务架构` 这类切在词中间的碎片；英文保留 3+ 字符词并过滤停用词。

---

## 3. 经验文件 — `experience/<事件类型>_<事件名称>.md`

```text
Event Type: <事件类型>
Event Name: <事件名称>
Date: YYYY-MM-DD
Summary: <一段概述>

## Full Description / Complete Resolution Process
<详细叙述过程>

## Design / Solution
<技术细节、替代方案、最终方法>
```

**事件类型（Event Type）：**

| 类型 | 用途 |
|------|------|
| `decision` | 重要决策及理由 |
| `lesson` | 经验教训 |
| `success` | 成功案例 |
| `failure` | 失败分析 |
| `idea` | 创意或提议 |
| `config` | 配置变更 |
| `note` | 一般性重要记录 |

**命名规则：**
- 事件名称使用 `snake_case`
- 文件名：`<event_type>_<snake_case_name>.md`
- 示例：`decision_api_choice.md`, `lesson_null_pointer_bug.md`, `config_cors_setup.md`

---

## 4. 长期索引 — `MEMORY.md`

每个工作区根目录下维护一个 `MEMORY.md`，存放所有经验文件的索引条目。

**条目格式：**
```text
- <文件名>
  触发关键词：“关键词1”、“关键词2”、...（至少3个，最多12个）
  触发条件：“条件1”、“条件2”、...（至少1个，最多6个）
  权重：1.0            # 可选；省略时默认为 1.0（完全信任的人工条目）
```

**规则：**
- 每个经验文件对应一个条目
- 触发关键词：暗示该经验可能相关的词语或短句
- 触发条件：应提示加载该经验的具体情境或问题（不要用 `关于<名>的事项` 这类占位句）
- 条目按添加顺序排列（追加到文件末尾）
- **`权重`（可选，0.0–1.0）：** 该条目在 `startup_scan` 匹配时的信任度。
  - **省略 = 1.0**：人工撰写的条目，完全信任。
  - **0.5**：自动补索引的条目，或混合了自动来源合并出的条目。`startup_scan`
    会把它的最终匹配分数乘以该权重——弱匹配时不会压过人工条目，但强相关时仍会返回。
  - 不要求人工校准；自动降权即可。
- 中文文件名原样保留（只过滤路径分隔符与 Windows 保留字符）。

**主会话 vs 子会话：**
- 主会话：工作区根目录的 `MEMORY.md`，存储所有个人长期经验
- 子会话：每个子会话有自己工作区内的独立 `MEMORY.md`，与主会话和其他子会话完全隔离
- **绝不要**在读取或写入记忆文件时跨越工作区边界

---

## 5. 目录结构

```
workspace/
├── MEMORY.md                         # 长期索引（每个工作区一个）
├── memory/
│   ├── 2026-05-10.md                 # 原始日志
│   ├── 2026-05-10-decompose.md       # 分解版
│   ├── 2026-05-11.md
│   └── 2026-05-11-decompose.md
└── experience/
    ├── decision_api_choice.md        # 经验文件
    ├── lesson_null_pointer_bug.md
    └── config_cors_setup.md
```

**目录保证：** 在 `memory/` 或 `experience/` 内写入任何文件之前，确保目录存在。如不存在则创建。
