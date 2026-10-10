#!/usr/bin/env python3
"""Shared Chinese/English keyword extraction for the memory-system skill.

Why a shared module
-------------------
`decompose.py` and `merge_experiences.py` both extract keywords from Chinese
text. The previous per-script copy used an unrestricted sliding window (every
2-4 character substring), which emitted fragments cut mid-word — `务架构`,
`库连`, `接池` — that match nothing useful and pollute the index. This module
centralises one segmenter so the behaviour is identical everywhere and can be
tested once.

Segmentation strategy (no hard dependency)
-----------------------------------------
1. If `jieba` is importable, use it — the de-facto standard pure-Python Chinese
   segmenter, highest quality. (`pip install jieba` is recommended; it is
   optional, not required to run the skill.)
2. Otherwise fall back to a dictionary longest-match segmenter plus a bigram
   fallback. The embedded dictionary covers everyday and common technical
   vocabulary; runs not covered by the dictionary are split into dictionary
   bigrams and any leftover single character is dropped (a lone CJK char is
   almost never a useful index keyword). This is clearly better than the old
   sliding window and needs no third-party package.
"""

import re

try:
    import jieba
    jieba.setLogLevel("ERROR")
    _HAS_JIEBA = True
except Exception:  # pragma: no cover - depends on the host environment
    _HAS_JIEBA = False


# Common Chinese words: 2-char, plus a few longer technical terms. Longest-match
# segmentation prefers the longest dictionary entry, so longer terms are listed
# alongside shorter ones and the matcher picks them first.
_CJK_DICT = set(
    """
数据 数据库 数据源 数据集 数据仓库 数据流 数据层 数据表 字段 索引 查询
连接 连接池 链接 池化 配置 配置项 配置中心 优化 架构 业务 业务方 业务线
设计 接口 服务 服务化 服务端 客户端 缓存 线程 线程池 内存 文件 文件系统
系统 系统级 用户 用户名 密码 权限 部署 测试 日志 错误 异常 请求 响应
协议 模型 训练 推理 预测 分类 聚类 回归 损失 梯度 学习率 性能 安全
网络安全 端口 进程 锁 死锁 队列 消息 事件 状态 流程 模块 组件 组件化
参数 变量 函数 方法 类 对象 实例 版本 分支 合并 冲突 提交 构建 编译
依赖 依赖包 包 库 框架 容器 镜像 集群 节点 负载 网关 路由 域名 证书
加密 密钥 令牌 令牌桶 会话 分区 副本 同步 异步 并发 调度 任务 作业
管道 脚本 命令 环境 环境变量 监控 告警 灰度 发布 熔断 降级 限流 重试
幂等 单机 多活 容灾 容错 扩容 缩容 弹性 稳定性 可用性 一致性 复制
选型 方案 评估 对比 决策 总结 经验 事项 问题 需求 缺陷 故障 根因
排查 定位 修复 验证 上线 回滚 预案 演练 文档 注释 规范 约定 命名
风格 评审 协作 沟通 对齐 闭环 会议 调用 改造 处理 生成 指标 度量 统计 趋势 前端 后端 全栈
平台 中台 引擎 编译器 解释器 运行时 虚拟机 操作系统 字符集 编码
序列化 反序列化 压缩 解压 流式 批量 实时 离线 增量 全量 预热 穿透
雪崩 击穿 事务 死锁 活锁 饥饿 竞争 临界 原子 可见 有序 屏障 信号量
互斥 读写 乐观 悲观 版本号 时间戳 序列号 雪花 自增 哈希 散列 取模
虚拟 机房 可用区 地域 跨区 同城 异地 单元 寻址 注册 发现 心跳 探活
均衡 轮询 随机 权重 最小 最少 响应 时间 半开 关闭 兜底 静态 动态
推送 监听 变更 生效 热点 慢查询 全表 扫描 覆盖 回表 执行 计划 慢日志
对象池 泄漏 溢出 积压 解耦 削峰 填谷 复盘 排期 估算 预算 成本 成本核算
归档 审计 秘书 经理 总监 团队 项目 产品 客户 市场 技术 工程 开发 运维
运营 公司 智能体 锐利 评分 风控 策略 质量 效率 风险 收益 投入 产出
周期 里程碑 迭代 冲刺 看板 工单 阈值 采样 抽样 聚合 趋势 同比 环比
漏斗 转化 留存 活跃 流失 标签 画像 特征 样本 标注 增强 蒸馏 微调 量化
"""
    .split()
)

# 2-char dictionary words, used as a fallback when a longer match is missing so
# that common words not listed above still segment correctly.
_DICT_BIGRAMS = {w for w in _CJK_DICT if len(w) == 2}

_EN_STOP = {
    "the", "and", "for", "this", "that", "with", "from", "what",
    "how", "why", "not", "are", "was", "has", "had", "can", "will",
    "all", "any", "but", "did", "get", "got", "its", "may", "per",
    "see", "set", "two", "use", "way", "you", "your", "our", "when",
    "where", "which", "who", "into", "than", "then", "they", "them",
    "their", "there", "here", "over", "also", "after", "before",
}

_CJK_RUN = re.compile(r"[一-鿿]+")
_EN_WORD = re.compile(r"\b[a-zA-Z_][a-zA-Z_]{2,}\b")


def _segment_run(run: str) -> list[str]:
    """Segment a single run of CJK characters into word-like tokens."""
    if _HAS_JIEBA:  # pragma: no cover - exercised only when jieba is present
        toks = [t for t in jieba.cut(run) if t.strip()]
        return [t for t in toks if len(t) >= 2]

    tokens: list[str] = []
    i = 0
    n = len(run)
    while i < n:
        # Longest dictionary match starting at i (4, then 3, then 2 chars).
        matched = None
        for length in (4, 3, 2):
            cand = run[i:i + length]
            if cand in _CJK_DICT:
                matched = cand
                break
        if matched:
            tokens.append(matched)
            i += len(matched)
            continue
        # Not in the dictionary: try a known bigram (covers many 2-char words).
        if i + 1 < n and run[i:i + 2] in _DICT_BIGRAMS:
            tokens.append(run[i:i + 2])
            i += 2
            continue
        # Skip the lone character — single CJK chars are noise as keywords.
        i += 1
    return tokens


def segment_cjk(text: str) -> list[str]:
    """Return word-like CJK tokens from arbitrary text (order preserved, de-duped)."""
    out: list[str] = []
    for run in _CJK_RUN.findall(text):
        out.extend(_segment_run(run))
    seen: set[str] = set()
    result: list[str] = []
    for tok in out:
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def extract_keywords(text: str, limit: int = 15) -> list[str]:
    """Extract meaningful keywords from text (Chinese words + English words).

    Chinese runs are segmented into word-like tokens (see `segment_cjk`);
    English words of 3+ characters are kept after stop-word filtering. The
    result is de-duplicated while preserving order and trimmed to `limit`.
    """
    words: list[str] = segment_cjk(text)
    words.extend(
        w.lower() for w in _EN_WORD.findall(text) if w.lower() not in _EN_STOP
    )
    words = [w for w in words if w.lower() not in _EN_STOP]
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result[:limit]
