# 完整示例

以下展示一个完整的记忆周期：从原始日志 → 分解笔记 → 经验记录 → 索引归档。

## 示例场景

用户在 2026-05-10 与助手对话，讨论了一个技术决策：选择数据库方案。

---

## Step 1: 原始日志 — `memory/2026-05-10.md`

```text
# Daily log for 2026-05-10

## [09:15] User
我们需要为新项目选数据库，PostgreSQL 和 MySQL 你觉得哪个好？

## [09:15] Assistant
经过分析，推荐 PostgreSQL。理由：
1. 新项目需要复杂查询（JSONB、窗口函数）
2. 我们需要高并发写入能力
3. PostgreSQL 在扩展性上更优
建议使用 PgBouncer 连接池，初始配置 max_connections=50。

## [14:30] User
行，那就用 PostgreSQL。帮我写个建表脚本。

## [14:30] Assistant
好的，创建用户表和订单表的 DDL：

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    amount DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```
```

---

## Step 2: 分解笔记 — `memory/2026-05-10-decompose.md`

（在 2026-05-11 由 decompose.py 自动生成）

```text
# Decompose for 2026-05-10

## 预读指南
此文件帮助你快速决定是否需要阅读完整的原始日志。它包含关键词和一份摘要。

## 关键词
- 数据库
- PostgreSQL
- MySQL
- 方案选择
- 复杂查询
- 连接池
- DDL
- 建表
- decision
- database

## 摘要
共 4 轮对话。讨论了 PostgreSQL vs MySQL 的选型决策，最终确定使用 PostgreSQL，并给出了初始建表脚本。
```

---

## Step 3: 经验文件 — `experience/decision_db_choice.md`

（在决策确定后创建）

```text
Event Type: decision
Event Name: db_choice
Date: 2026-05-10
Summary: 为新项目选择了 PostgreSQL，原因是复杂查询支持和并发性能优于 MySQL

## Full Description / Complete Resolution Process
用户问及数据库选型。我分析了 PostgreSQL 和 MySQL 的差异：
- PostgreSQL 支持 JSONB 数据类型，适合文档存储
- PostgreSQL 的窗口函数和 CTE 支持更完善
- PostgreSQL 在高并发场景下表现稳定，MySQL 在 InnoDB 锁竞争下有瓶颈
- 社区生态：PostgreSQL 扩展丰富（PostGIS, pgvector 等）

最终用户采纳建议，选定 PostgreSQL。

## Design / Solution
- 使用 PgBouncer 作为连接池（轻量，专为 PostgreSQL 设计）
- 初始配置 max_connections=50, shared_buffers=1GB
- 使用 `TIMESTAMPTZ` 类型存储时间，避免时区问题
- 主键使用 `SERIAL` 自增，后续可迁移到 `IDENTITY` 列
```

---

## Step 4: 索引条目 — `MEMORY.md`

（由 add_experience.py 自动追加）

```text
- decision_db_choice.md
  触发关键词：“数据库”、“PostgreSQL”、“选型”、“复杂查询”、“连接池”、“方案选择”
  触发条件：“需要选择数据库方案”、“讨论数据库对比”、“配置数据库连接”、“评估性能”
```

> 这是人工条目，**省略 `权重` 行即默认 1.0（完全信任）**。若是 `merge_experiences`
> 自动补索引的条目，会额外带一行 `权重：0.5`，且触发条件用事件名（而非占位句）。

---

## 启动场景示例

**场景：** 用户新会话开始，说"我要为项目配置数据库"

**startup_scan.py 执行：**
```bash
python3 startup_scan.py \
  --workspace <workspace-path> \
  --task "为项目配置数据库"
```

**输出（人工条目，默认权重 1.0）：**
```text
Found 1 matching experience(s):

  [15%] decision_db_choice.md  (✓ exists, weight 1.0, curated)
         Keywords: 数据库, PostgreSQL, 选型, 复杂查询, 连接池, 方案选择
         Conditions: 需要选择数据库方案, 讨论数据库对比, 配置数据库连接, 评估性能
         Path: <workspace-path>/experience/decision_db_choice.md
```

**输出（自动补索引条目，权重 0.5，同一任务下分数被压低）：**
```text
Found 1 matching experience(s):

  [18%] lesson_db.md  (✓ exists, weight 0.5, auto)
         Keywords: 连接池, 配置, 优化, 方案, 评估, 项目, 缓存, 数据库, 选型, redis
         Conditions: 数据库选型
         Path: <workspace-path>/experience/lesson_db.md
```

**行动：** 读取 `experience/decision_db_choice.md` 的 Summary 和 Event Type，如果相关则继续读取 Full Description。自动条目仍会被返回，但分数更低、排在人工条目之后。
