# 记忆系统 (Memory System)

三层记忆架构：原始日志 → 分解索引 → 经验归档。用于 AI 会话的持久化记忆管理。

> **相对原始版本（SKILL 标签）的破坏性变更**：`append_turn.py` / `daily_log.py` **不再接受命令行里的对话正文**。
> 旧写法 `append_turn.py <ws> "<用户消息>" "<回复>"` 与 `daily_log.py --user-msg "..." --reply "..."`
> 均已被移除（会以 argparse 错误退出）。改成 `--user-file` / `--reply-file` / `--stdin`，见下节。
> 原因：正文里的 `$(...)` 会被 shell 展开，旧版实测确实执行了注入载荷。其余流程不变。

仓库里的 `scripts/` 与 `references/` 就是技能本体，可直接复制进 `~/.workbuddy/skills/`
（或其他智能体的技能目录）。`memory-system-community.tar.gz` 是同一个内容的打包版。

## 核心流程

### 1. 每轮日志记录（强制实时 + 自动触发）

每次给出回复后，**立即**在回复末尾执行 `append_turn.py` 记录本轮对话。该脚本静默运行，
失败不崩溃（错误写 stderr，退出码仍为 0；加 `--strict` 可让失败返回 1）。

**正文只能通过文件或 stdin 传入，不能写在命令行参数里。** 回复是任意文本——它可能引用
网页、文件或用户消息。把它拼进命令行就等于交给 shell 解析：实测旧版
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

非回复场景批量追加（如补录历史日志）用 `daily_log.py`，参数相同：

```bash
scripts/daily_log.py --workspace <workspace-path> \
  --user-file /tmp/turn-user.txt --reply-file /tmp/turn-reply.txt
```

### 2. 分解笔记生成（次日）

次日对昨日完成的原始日志运行 `decompose.py`：

```bash
scripts/decompose.py --workspace <workspace-path> --date YYYY-MM-DD
```

生成 `memory/YYYY-MM-DD-decompose.md`，含关键词和摘要。用于快速索引。

### 3. 经验归档（手动 + 被动钩子）

手动触发：

```bash
scripts/add_experience.py --workspace <path> \
  --event-type decision --event-name "数据库选型" \
  --summary "..." --description "..." --design "..." \
  --keywords "数据库,选型" --conditions "需要选数据库"
```

事件名可以是中文；文件名只过滤路径分隔符与 Windows 保留字符。
`--interactive` 需要真实终端，智能体调用会收到明确错误（退出码 2）。

被动钩子：**当完全完成一个独立完整事件时**（① 单个完整事件、② 彻底完成、③ 流程闭环），
**必须主动询问用户是否需要存档为经验文件**。用户确认后调用 `add_experience.py`；
用户说不用就不强行归档。事件刚完成时上下文完整、决策链条清晰、最终结果已知——经验
文件质量最高。

### 4. 经验合并与清理（每 5-7 天）

建议每 5-7 天运行一次 `merge_experiences.py`。

> **不会自己触发**：本技能不含任何 cron / 调度器，需由调用方或外部平台驱动。脚本启动后全自动，无需人工介入。
>
> 想要真的周期跑起来（技能本体不含调度，二选一）：
>
> ```bash
> # A. 系统 cron：每周一 04:15
> 15 4 * * 1 /usr/bin/python3 /path/to/skills/memory-system/scripts/merge_experiences.py \
>     --workspace /path/to/workspace >> /tmp/memory-merge.log 2>&1
>
> # B. systemd timer：service + timer（OnCalendar=weekly）
> systemctl --user enable --now memory-merge.timer
> ```
>
> 只有 `merge_experiences.py` 适合排期；`append_turn.py` / `daily_log.py` 必须在对话当下调用。

处理以下情况：

| 类型 | 条件 | 处理方式 |
|------|------|---------|
| ① 孤儿索引 | 文件已删，MEMORY.md 还挂着 | **自动清理** — 移除无用索引 |
| ② 未索引文件 | experience/ 存在但 MEMORY.md 没记 | **自动索引** — 解析 header，用优化过的中文分词提取词级关键词（无碎片），触发条件用事件名，并以 `权重：0.5` 标记（自动降权，不要求人工校准） |
| ③ 高度相似 | 同 event_type；**人工条目**：触发词/条件 ≥80% 且 内容 ≥80%；**含自动索引条目**：走下方「两级阈值」（重叠系数 ≥50%） | **自动合并** — 最新文件保留身份，其余成员内容全部追加 |
| ④ 破损文件 | header 字段缺失 | **报告留痕** — 列出具体缺失字段，不做自动操作 |

```bash
# 只扫描不修改
scripts/merge_experiences.py --workspace <path> --dry-run

# 强制手动合并（忽略阈值，可多于两个文件）
scripts/merge_experiences.py --workspace <path> --force-merge "file_a.md,file_b.md"

# 自定义阈值 / 关闭备份（不推荐）
scripts/merge_experiences.py --workspace <path> \
  --keyword-threshold 0.9 --content-threshold 0.9 --no-backup

# 只调自动索引条目的判定（人工条目仍走严格阈值）
scripts/merge_experiences.py --workspace <path> \
  --auto-keyword-threshold 0.6 --auto-content-threshold 0.6
```

**两级阈值**：同一 event_type 内两两比较，触发词与正文都达标才合并。人工条目双方都用 Jaccard ≥80%；只要有一方是自动索引条目，就改用重叠系数（交集/较小集）≥50% 且共享触发词 ≥3 个。原因是自动抽取的关键词噪声大，实测两个真重复文件在严格 Jaccard 下只有 7/9=0.778 而被漏掉；而"不应合并"的样本（含同域不同主题）最高只到 0.22/0.25。

**同轮联动**：本轮补索引的文件立即进入本轮合并候选，重复文件第一次运行就合并。**不再静默**：被拒绝的组合会打印 `[BELOW TRIGGERS]` / `[BELOW CONTENT]` 并给出数值原因。

合并是破坏性操作，三条硬约束：

- **删除前先备份**：源文件复制到 `experience/.archive/<时间戳>/`。
- **先写合并结果，再删源文件**：中途崩溃不会两边都没了。
- **相似对先聚簇，再一次合并**：三个文件两两相似时逐对合并会覆盖前一次结果并丢掉
  一个文件的全部内容。

### 5. 启动时关联扫描（新会话开始）

```bash
scripts/startup_scan.py --workspace <workspace-path> --task "<当前任务描述>"
```

匹配的经验文件路径会打印出来。`--min-matches N`（默认 1）要求至少命中 N 个触发词；
`--min-score S`（默认 0.05）按命中占比过滤；`--max-results M`（默认 5）限制返回条数。
分数是"命中占比"，触发词写得越多的条目占比越低，所以过滤要看命中数而不只是占比。
自动索引的条目（权重 0.5）分数会被乘权重，弱匹配时不会压过人工条目，但强相关时仍会返回。

中文关键词提取已改为词级切分：装了 `jieba`（`pip install jieba`）时用它得到最佳分词；
否则回退到内置词典最长匹配，不再产生 `务架构`、`库连` 这类切在词中间的碎片。

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

## 安全约束

1. **正文永不走命令行** —— 只走 `--user-file` / `--reply-file` / `--stdin`。
2. **事件名可以是中文** —— 只过滤路径分隔符和 Windows 保留字符。
3. **`--interactive` 需要终端** —— 非 TTY 时给明确错误（退出码 2），不抛栈。
4. **合并前必有备份** —— `experience/.archive/` 不会被扫描为经验文件。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

每个用例对应一个在真实运行中测到过的缺陷，注释里写了当时的实测现象。
