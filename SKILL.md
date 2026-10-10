---
name: memory-system
description: "Multi-layered memory management for personal AI workspaces. Supports per-turn daily logging, automatic next-day decomposition, structured experience archiving with trigger-based (Chinese-aware, weighted) indexing, and startup-time relevance scanning. Hardened against shell command injection (message bodies accepted via file/stdin only, never argv). Use when: (1) implementing or maintaining a memory persistence layer, (2) logging conversation turns to daily files, (3) creating experience/lesson/decision archives, (4) building MEMORY.md indexes for experience retrieval, (5) onboarding memory systems to new AI sessions or workspaces."
---

# 记忆系统 (Memory System)

三层记忆架构：原始日志 → 分解索引 → 经验归档。用于 AI 会话的持久化记忆管理。

> **相对原始版本（SKILL 标签）的破坏性变更 — 调用方必须改动**
> `append_turn.py` / `daily_log.py` **不再接受命令行里的对话正文**。原版用法会被拒绝并报错退出：
> ```text
> # 旧写法（已失效，会报 unrecognized arguments）
> python3 scripts/append_turn.py <workspace-path> "<用户消息>" "<回复>"
> scripts/daily_log.py --workspace <path> --user-msg "..." --reply "..."
> ```
> 新写法见下节：正文一律走 `--user-file` / `--reply-file` / `--stdin`。
> 改的原因不是偏好——正文里出现 `$(...)` 会被 shell 展开，实测旧版真的执行了注入载荷。
> 其余所有流程（five 步、文件格式、事件类型）保持不变。

## 核心流程

### 1. 每轮日志记录（强制实时 + 自动触发）

每次给出回复后，**立即**在回复末尾执行 `append_turn.py` 记录本轮对话。该脚本静默运行，失败不崩溃（错误写 stderr，退出码仍为 0；加 `--strict` 可让失败返回 1）。

**正文只能通过文件或 stdin 传入，不能写在命令行参数里。** 回复是任意文本——它可能引用网页、文件或用户消息。把它拼进命令行就等于交给 shell 解析：实测旧版
`append_turn.py <ws> "…" "参考内容：$(touch /tmp/PWNED)"` 会真的执行该载荷。

```bash
# 方式一：先写文件，再传路径（推荐）
printf '%s' "$USER_MSG" > /tmp/turn-user.txt
printf '%s' "$REPLY"    > /tmp/turn-reply.txt
python3 scripts/append_turn.py \
  --workspace <workspace-path> \
  --user-file /tmp/turn-user.txt \
  --reply-file /tmp/turn-reply.txt \
  --verbose

# 方式二：stdin，用单独一行的 %%REPLY%% 分隔
printf '%s\n%%REPLY%%\n%s' "$USER_MSG" "$REPLY" |
  python3 scripts/append_turn.py --workspace <workspace-path> --stdin --verbose
```

记录到 `memory/YYYY-MM-DD.md`。先于其他任何动作——会话可能意外终止。

如果需要在非回复场景批量追加（如补录历史日志），使用 `daily_log.py`，参数相同：

```bash
scripts/daily_log.py \
  --workspace <workspace-path> \
  --user-file /tmp/turn-user.txt \
  --reply-file /tmp/turn-reply.txt
```

### 2. 分解笔记生成（次日）

次日对昨日完成的原始日志运行 `decompose.py`：

```bash
scripts/decompose.py \
  --workspace <workspace-path> \
  --date YYYY-MM-DD
```

生成 `memory/YYYY-MM-DD-decompose.md`，含关键词和摘要。用于快速索引。

### 3. 经验归档（手动 + 被动钩子）

#### 手动触发
用户随时可以要求将刚刚完成的内容总结提炼为经验文件：

```bash
scripts/add_experience.py --workspace <path> --interactive
# 或
scripts/add_experience.py \
  --workspace <path> \
  --event-type decision \
  --event-name "api_choice" \
  --summary "..." \
  --description "..."
```

#### 被动钩子
**当完全完成一个独立完整事件时**（同时满足三个条件：① 单个完整事件、② 彻底完成、③ 流程闭环），**必须主动询问用户是否需要存档为经验文件**。示例：

> "这个 Redis 缓存方案已经完成部署验证了。要不要把这次选型决策和配置存档成经验文件？方便以后参考。"

用户确认后调用 `add_experience.py`。如果用户说不用，不强行归档。

**为什么是这个时机？** 事件刚完成时上下文完整、决策链条清晰、最终结果已知——经验文件质量最高。过了这个窗口期再补文件，信息已经丢失。

### 4. 经验合并与清理（每 5-7 天）

建议每 5-7 天运行一次 `merge_experiences.py` 检查经验文件健康状况。

> **不会自己触发（重要）**：本技能**不含任何 cron / 调度器 / 钩子**，不会定时自动运行。必须由使用方按本文件约定发起调用（AI 会话周期性执行），或由外部平台驱动。脚本一旦启动则**全自动**：无需人工确认，自动处理以下情况。
>
> **如何让它真的周期跑起来（二选一，技能本体不提供调度）**
>
> ```bash
> # A. 系统 cron：每周一 04:15 跑一次健康检查
> 15 4 * * 1 /usr/bin/python3 /path/to/skills/memory-system/scripts/merge_experiences.py \
>     --workspace /path/to/workspace >> /tmp/memory-merge.log 2>&1
>
> # B. systemd timer：memory-merge.service + memory-merge.timer（OnCalendar=weekly）
> systemctl --user enable --now memory-merge.timer
> ```
>
> 注意：`merge_experiences.py` 需要 `--workspace` 绝对路径；正文类脚本（`append_turn.py` / `daily_log.py`）**只能在对话当下调用**，不适合排进 cron。

| 类型 | 条件 | 处理方式 |
|------|------|---------|
| ① 孤儿索引 | 文件已删，MEMORY.md 还挂着 | **自动清理** — 移除无用索引 |
| ② 未索引文件 | experience/ 存在但 MEMORY.md 没记 | **自动索引** — 解析 header，用优化过的中文分词提取词级关键词（无碎片），触发条件用事件名，并以 `权重：0.5` 标记（自动降权，不要求人工校准） |
| ③ 高度相似 | 同 event_type；**人工条目**：触发词/条件 ≥80% 且 内容 ≥80%；**含自动索引条目**：走下方「两级阈值」（重叠系数 ≥50%） | **自动合并** — 最新文件保留身份，其余成员内容全部追加 |
| ④ 破损文件 | header 字段缺失 | **报告留痕** — 列出具体缺失字段，不做自动操作 |

```bash
# 只扫描不修改（查看各项情况）
scripts/merge_experiences.py --workspace <path> --dry-run

# 强制手动合并（忽略相似度阈值，可多于两个文件）
scripts/merge_experiences.py \
  --workspace <path> \
  --force-merge "file_a.md,file_b.md"

# 自定义阈值 / 不要备份（不推荐）
scripts/merge_experiences.py --workspace <path> \
  --keyword-threshold 0.9 --content-threshold 0.9 --no-backup

# 只调自动索引条目的判定（人工条目仍走原来的严格阈值）
scripts/merge_experiences.py --workspace <path> \
  --auto-keyword-threshold 0.6 --auto-content-threshold 0.6
```

**自动合并判定（两级阈值）**：同一 event_type 内两两比较，先比触发词、再比正文，两项都达标才合并。

| 条目来源 | 度量 | 默认阈值 | 附加闸门 |
|---|---|---|---|
| 双方都是人工条目 | Jaccard（交集/并集） | ≥80% | — |
| 任一方是自动索引条目 | 重叠系数（交集/较小集） | ≥50% | 共享触发词 ≥3 个 |

分两级的原因（实测，非推测）：自动抽取的关键词带噪声，一个小集合里多一两个专有名词就足以把严格 Jaccard 压到 **7/9 = 0.778**，导致真重复文件**合不掉且什么都不打印**。实测标定样本：应合并的重复是 0.87/0.86 与 0.67/0.60；不应合并的（含最易误伤的**同域不同主题**）最高只有 0.22/0.25。安全窗口约 0.25–0.60，取 **0.5** 居中留余量，而不是贴在上沿。

**同轮联动**：Phase 1 补索引的文件会立刻进入本轮的合并候选池。新丢进 `experience/` 的重复文件**第一次运行就合并**，不需要跑第二遍。

**不再静默**：任何被判定不合并的组合都会打印 `[BELOW TRIGGERS]` 或 `[BELOW CONTENT]`，并给出实测数值与原因（含用了哪种度量）。不会出现"其实没合并，却报 All clean ✓"。

新文件（按 Date 较新的）保留文件名和身份，其余成员的内容按时间顺序追加，**一个都不丢**。

合并是破坏性操作，因此有三条硬约束：

- **删除前先备份**：所有源文件复制到 `experience/.archive/<时间戳>/`（`--no-backup` 可关闭，不建议）。
- **先写合并结果，再删源文件**：中途崩溃不会两边都没了。
- **相似对先聚簇，再一次合并**：三个文件两两相似时（A~B、A~C、B~C），逐对合并会让第二次覆盖第一次的结果并丢掉一个文件的全部内容。

自动索引条目写入 `权重：0.5`；合并条目继承聚簇中**最低**的成员权重（聚簇含自动来源则为 0.5，否则保持 1.0）。人工条目省略该行为默认 1.0。`startup_scan`
匹配分数会乘以权重，所以弱相关的自动条目不会压过人工条目，强相关时仍会返回——无需人工校准。

中文关键词提取现已改为词级切分：若环境装了 `jieba`（`pip install jieba`）则用它得到最佳分词；
否则回退到内置词典最长匹配，不再产生 `务架构`、`库连` 这类切在词中间的碎片。

### 5. 启动时关联扫描（新会话开始）

新会话开始时，运行 `startup_scan.py` 发现相关历史经验：

```bash
scripts/startup_scan.py \
  --workspace <workspace-path> \
  --task "<当前任务描述>"
```

匹配的经验文件路径会打印出来。读取匹配文件的 Summary 和 Event Type，判断是否需要加载完整内容。

`--min-matches N`（默认 1）要求至少命中 N 个触发词才列出；`--min-score S`（默认 0.05）按命中占比下限过滤；`--max-results M`（默认 5）限制返回条数。分数是"命中占比"，触发词写得越多的条目占比越低，所以过滤要看命中数而不只是占比。自动索引的条目（权重 0.5）分数会被乘权重，弱匹配时不会压过人工条目，但强相关时仍会返回。

## 文件格式

**禁止跨工作区读取/写入记忆文件。**

- **原始日志**: `memory/YYYY-MM-DD.md` — 每个对话轮次一个 `## [HH:MM]` 块
- **分解笔记**: `memory/YYYY-MM-DD-decompose.md` — 关键词列表 + 摘要
- **经验文件**: `experience/<event-type>_<event-name>.md` — 含 Event Type / Date / Summary 头 + Full Description + Design 段
- **长期索引**: 工作区根目录 `MEMORY.md` — 每个经验一个条目，含触发关键词和触发条件

详细格式规范见 [references/format-specs.md](references/format-specs.md)。
完整示例见 [references/examples.md](references/examples.md)。

## 有效事件类型

`decision` `lesson` `success` `failure` `idea` `config` `note`

## 启动时经验匹配策略

1. 调用 `startup_scan.py`，得到匹配的经验文件列表
2. 按匹配度从高到低读取（先读 Summary 和 Event Type，判断是否继续加载 Full Description）
3. 只加载与当前任务明确相关的经验——非所有会话共用记忆层

## 安全约束

1. **正文永不走命令行。** 对话内容（用户消息、助手回复）只通过 `--user-file` /
   `--reply-file` 或 `--stdin` 传入。命令行的 `$(...)`、反引号会被 shell 展开，
   而回复内容来自网页、文件、用户消息——不受你控制。
2. **事件名可以是中文。** 文件名只过滤路径分隔符和 Windows 保留字符，中文原样保留。
3. **`--interactive` 需要终端。** 智能体调用时 stdin 不是 TTY，脚本会给出明确错误
   （退出码 2）并提示改用非交互参数，而不是抛 EOFError 栈。
4. **合并前必有备份。** 见上文合并三约束。`experience/.archive/` 里的备份不会被
   扫描为经验文件（只扫 `experience/*.md`，不递归）。
