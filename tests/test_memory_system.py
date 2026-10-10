#!/usr/bin/env python3
"""Regression tests for the memory-system skill scripts.

Every test here corresponds to a defect that was measured on the shipped
version, not guessed at. Run:

    python3 -m unittest discover -s tests -v

Each test names the failure it locks down in its docstring.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PY = sys.executable


def run(script, *args, seed=None, timeout=30, stdin=None):
    env = dict(os.environ)
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    return subprocess.run(
        [PY, str(SCRIPTS / script), *args],
        capture_output=True, text=True, timeout=timeout, env=env, input=stdin,
    )


def load(script):
    """Import a script as a module by path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(script[:-3], SCRIPTS / script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_experience(path, event_type, name, date, summary, body, design):
    path.write_text(
        f"Event Type: {event_type}\nEvent Name: {name}\nDate: {date}\n"
        f"Summary: {summary}\n\n"
        f"## Full Description / Complete Resolution Process\n{body}\n\n"
        f"## Design / Solution\n{design}\n",
        encoding="utf-8",
    )


class Workspace(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="memskill-"))
        (self.dir / "experience").mkdir()
        (self.dir / "memory").mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def index(self, *filenames, keywords=("Redis", "缓存"), conditions=("需要缓存",)):
        lines = ["# Memory Index", ""]
        for name in filenames:
            lines.append(f"- {name}")
            lines.append("  触发关键词：" + "、".join(f"“{k}”" for k in keywords))
            lines.append("  触发条件：" + "、".join(f"“{c}”" for c in conditions))
            lines.append("")
        (self.dir / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8")


class TestIndexParsing(Workspace):
    def test_two_bare_adjacent_entries_do_not_hang(self):
        """Measured: exit 124 under `timeout 8` — the scan never advanced.

        Two entries with no keyword/condition lines in between made the old
        hand-managed line index back up onto the previous entry forever.
        """
        for script in ("merge_experiences.py", "startup_scan.py"):
            with self.subTest(script=script):
                (self.dir / "MEMORY.md").write_text("# Memory\n\n- a.md\n- b.md\n",
                                                    encoding="utf-8")
                args = ["--workspace", str(self.dir)]
                if script == "startup_scan.py":
                    args += ["--task", "任意任务"]
                else:
                    args += ["--dry-run"]
                proc = run(script, *args, timeout=15)
                self.assertNotEqual(proc.returncode, 124,
                                    f"{script} hung on two adjacent bare entries")

    def test_entries_are_parsed_with_keywords_and_conditions(self):
        self.index("decision_a.md", "decision_b.md")
        scan = load("startup_scan.py")
        entries = scan.parse_memory_index((self.dir / "MEMORY.md").read_text(encoding="utf-8"))
        self.assertEqual([e["filename"] for e in entries],
                         ["decision_a.md", "decision_b.md"])
        self.assertEqual(entries[0]["keywords"], ["Redis", "缓存"])
        self.assertEqual(entries[0]["conditions"], ["需要缓存"])

    def test_entry_block_removal_covers_the_whole_block(self):
        """Measured: removing an entry left its trailing lines behind."""
        merge = load("merge_experiences.py")
        # line 2 = "- a.md", 3 = keywords, 4 = conditions, 5 = extra line, 6 = blank
        text = ("# Memory Index\n\n- a.md\n  触发关键词：“x”\n  触发条件：“y”\n"
                "  some other line\n\n- b.md\n  trailing line\n")
        entries = merge.parse_memory_index(text)
        self.assertEqual(len(entries), 2)
        # The extra indented line belongs to entry a, so end_line covers it.
        self.assertEqual(entries[0]["end_line"], 5)
        self.assertEqual(entries[1]["end_line"], 8, "a trailing line after the last entry counts too")


class TestMerging(Workspace):
    def _three_way(self):
        for n, tag, day in (("alpha", "ALPHA", "01"), ("beta", "BETA", "02"),
                            ("gamma", "GAMMA", "03")):
            make_experience(
                self.dir / "experience" / f"decision_{n}.md",
                "decision", n, f"2026-05-{day}",
                f"选择了 Redis 缓存方案 版本 {tag}",
                f"项目需要缓存层，评估后选择 Redis 版本 {tag}",
                "使用 Redis 连接池，maxmemory allkeys-lru，键前缀统一",
            )
        self.index("decision_alpha.md", "decision_beta.md", "decision_gamma.md")

    def test_three_way_similarity_keeps_every_member(self):
        """Measured: alpha's content was silently lost.

        The old code merged pair by pair from stale in-memory copies, so the
        second overwrite destroyed the first merge's output.
        """
        self._three_way()
        proc = run("merge_experiences.py", "--workspace", str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)

        merged = (self.dir / "experience" / "decision_gamma.md").read_text(encoding="utf-8")
        for tag in ("ALPHA", "BETA", "GAMMA"):
            self.assertIn(tag, merged, f"{tag} content was lost in the merge")

    def test_sources_are_archived_before_deletion(self):
        self._three_way()
        run("merge_experiences.py", "--workspace", str(self.dir))
        archives = list((self.dir / "experience" / ".archive").rglob("*.md"))
        self.assertEqual(len(archives), 3, "every source must be archived before deletion")
        self.assertEqual({p.name for p in archives},
                         {"decision_alpha.md", "decision_beta.md", "decision_gamma.md"})

    def test_merged_index_has_no_orphan_entry(self):
        """Measured: MEMORY.md kept an entry pointing at a deleted file.

        The rebuild checked `filename in line` against the pre-removal lines,
        so the freshly merged entry was skipped as "already present".
        """
        self._three_way()
        run("merge_experiences.py", "--workspace", str(self.dir))
        text = (self.dir / "MEMORY.md").read_text(encoding="utf-8")
        listed = sorted(
            line.strip()[2:] for line in text.splitlines()
            if line.strip().startswith("- ") and line.strip().endswith(".md")
        )
        present = sorted(p.name for p in (self.dir / "experience").glob("*.md"))
        self.assertEqual(listed, present, "MEMORY.md must list exactly the files on disk")

    def test_dry_run_changes_nothing(self):
        self._three_way()
        before = (self.dir / "MEMORY.md").read_text(encoding="utf-8")
        run("merge_experiences.py", "--workspace", str(self.dir), "--dry-run")
        self.assertEqual((self.dir / "MEMORY.md").read_text(encoding="utf-8"), before)
        self.assertEqual(len(list((self.dir / "experience").glob("*.md"))), 3)

    def test_force_merge_actually_merges(self):
        """Measured: --force-merge was declared but never referenced in the code.

        It exited 0 and did nothing.
        """
        make_experience(self.dir / "experience" / "note_one.md", "note", "one",
                        "2026-06-01", "关于一的记录", "内容一与其他文件毫无关联", "设计一")
        make_experience(self.dir / "experience" / "note_two.md", "note", "two",
                        "2026-06-02", "关于二的记录", "内容二与其他文件毫无关联", "设计二")
        self.index("note_one.md", "note_two.md", keywords=("一",), conditions=("情况一",))
        (self.dir / "MEMORY.md").write_text(
            "# Memory Index\n\n- note_one.md\n  触发关键词：“一”\n  触发条件：“情况一”\n\n"
            "- note_two.md\n  触发关键词：“二”\n  触发条件：“情况二”\n", encoding="utf-8")

        proc = run("merge_experiences.py", "--workspace", str(self.dir),
                   "--force-merge", "note_one.md,note_two.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("FORCED", proc.stdout)
        remaining = list((self.dir / "experience").glob("*.md"))
        self.assertEqual(len(remaining), 1, "the two forced files must become one")

    def test_force_merge_rejects_unknown_filenames(self):
        make_experience(self.dir / "experience" / "note_one.md", "note", "one",
                        "2026-06-01", "记录", "内容", "设计")
        self.index("note_one.md", keywords=("一",), conditions=("情况一",))
        proc = run("merge_experiences.py", "--workspace", str(self.dir),
                   "--force-merge", "note_one.md,nope.md")
        self.assertNotEqual(proc.returncode, 0)


class TestSimilarity(unittest.TestCase):
    # Two files that share a long tail and differ at the head. Both yield more
    # keywords than the sample size, so *which* keywords get sampled decides the
    # score. Measured on the shipped version across seeds 1-8:
    #   单机部署 / 多活容灾 → 0.0526, 0.0526, 0.0526, 0.0526, 0.0000, 0.0000, 0.0000, 0.0526
    #   同步调用 / 异步消息 → 0.0526, 0.0526, 0.0526, 0.0526, 0.0526, 0.0000, 0.0000, 0.0526
    VARIANTS = [("单机部署", "多活容灾"), ("同步调用", "异步消息"),
                ("Redis集群", "Kafka队列")]
    TAIL = "监控告警日志聚合灰度发布熔断降级限流重试幂等"

    def _sim_across_seeds(self, head_a, head_b):
        values = set()
        for seed in range(1, 9):
            env = dict(os.environ, PYTHONHASHSEED=str(seed))
            code = textwrap.dedent(f"""
                import importlib.util
                spec = importlib.util.spec_from_file_location("m", r"{SCRIPTS / 'merge_experiences.py'}")
                m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
                tail = {self.TAIL!r}
                a = {{"summary": {head_a!r}, "event_name": "a",
                      "description": {head_a!r} + "架构", "design": tail}}
                b = {{"summary": {head_b!r}, "event_name": "b",
                      "description": {head_b!r} + "架构", "design": tail}}
                print(repr(m.content_similarity(a, b)))
            """)
            out = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            values.add(out.stdout.strip())
        return values

    def test_content_similarity_is_stable_across_hash_seeds(self):
        """Measured: the "top 10 keywords" were sampled out of a `set`.

        Python randomises string hashing per process, so which ten were picked
        changed between runs. Merging is destructive, so the same inputs could
        merge on one run and not the next.
        """
        for head_a, head_b in self.VARIANTS:
            with self.subTest(a=head_a, b=head_b):
                values = self._sim_across_seeds(head_a, head_b)
                self.assertEqual(len(values), 1,
                                 f"{head_a}/{head_b} varied across seeds: {sorted(values)}")


class TestContentInjection(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="memskill-inj-"))
        self.marker = Path(tempfile.mkdtemp(prefix="memskill-marker-")) / "PWNED"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.marker.parent, ignore_errors=True)

    def test_argv_no_longer_accepts_turn_content(self):
        """Measured: `append_turn.py <ws> "u" "…$(touch /tmp/PWNED)…"` ran the payload.

        A reply quotes whatever the conversation contained — web pages, files,
        user text. Passing it as an argv string handed it to the shell. The fix
        removes that interface: content now comes from a file or stdin, so the
        old positional call is rejected outright.
        """
        proc = run("append_turn.py", str(self.dir), "user", "reply", timeout=15)
        self.assertNotEqual(proc.returncode, 0,
                            "positional content arguments must no longer be accepted")
        self.assertFalse(self.marker.exists(), "payload must not execute")

    def test_reply_with_shell_syntax_is_stored_verbatim(self):
        payload = "好的，参考内容：$(touch PWNED) `whoami` && echo done"
        (self.dir / "r.txt").write_text(payload, encoding="utf-8")
        (self.dir / "u.txt").write_text("用户消息", encoding="utf-8")
        proc = run("append_turn.py", "--workspace", str(self.dir),
                   "--user-file", str(self.dir / "u.txt"),
                   "--reply-file", str(self.dir / "r.txt"), "--verbose")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.marker.exists(), "payload must not execute")
        log = (self.dir / "memory").glob("*.md")
        written = "\n".join(p.read_text(encoding="utf-8") for p in log)
        self.assertIn("$(touch PWNED)", written, "content must be stored verbatim")

    def test_stdin_mode_splits_on_marker(self):
        proc = run("append_turn.py", "--workspace", str(self.dir), "--stdin", "--verbose",
                   stdin="用户消息\n%%REPLY%%\n助手回复\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        written = "\n".join(p.read_text(encoding="utf-8")
                            for p in (self.dir / "memory").glob("*.md"))
        self.assertIn("用户消息", written)
        self.assertIn("助手回复", written)

    def test_missing_content_source_fails_loudly(self):
        proc = run("append_turn.py", "--workspace", str(self.dir), "--strict")
        self.assertEqual(proc.returncode, 1, "no content source must be an error")

    def test_unwritable_workspace_never_crashes_the_turn(self):
        """The docstring promises 'fails silently, never crashes' — but the old
        version had no try/except at all."""
        proc = run("append_turn.py", "--workspace", str(self.dir / "does-not-exist"),
                   "--stdin", stdin="u\n%%REPLY%%\nr\n")
        self.assertEqual(proc.returncode, 0, "must stay silent by default")
        self.assertIn("[WARN]", proc.stderr)


class TestAddExperience(Workspace):
    def test_chinese_event_names_do_not_collide(self):
        """Measured: "数据库选型方案" → "" → decision_.md, and every later
        Chinese name collided with it and was refused as 'already exists'."""
        ae = load("add_experience.py")
        self.assertEqual(ae.sanitize_filename("数据库选型方案"), "数据库选型方案")
        self.assertEqual(ae.sanitize_filename("Redis缓存选型"), "redis缓存选型")
        self.assertNotEqual(ae.sanitize_filename("配置 Redis 缓存"),
                            ae.sanitize_filename("Redis 缓存配置"))

    def test_separators_are_still_stripped(self):
        ae = load("add_experience.py")
        for bad in ("../../etc/passwd", "a/b", "a\\b", "a:b", "a|b"):
            self.assertNotIn("/", ae.sanitize_filename(bad))
            self.assertNotIn("\\", ae.sanitize_filename(bad))

    def test_interactive_without_a_tty_gives_a_clear_error(self):
        """Measured: a bare EOFError traceback (exit 1) when an agent called it."""
        proc = run("add_experience.py", "--workspace", str(self.dir), "--interactive",
                   stdin="", timeout=20)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("terminal", proc.stderr)
        self.assertNotIn("EOFError", proc.stderr, "must not leak a raw traceback")

    def test_non_interactive_creates_file_and_index(self):
        proc = run("add_experience.py", "--workspace", str(self.dir),
                   "--event-type", "decision", "--event-name", "数据库选型",
                   "--summary", "选定 PostgreSQL", "--description", "对比后决定",
                   "--design", "使用连接池", "--keywords", "数据库,选型",
                   "--conditions", "需要选数据库")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.dir / "experience" / "decision_数据库选型.md").exists())
        self.assertIn("decision_数据库选型.md",
                      (self.dir / "MEMORY.md").read_text(encoding="utf-8"))


class TestDecompose(Workspace):
    def test_turn_count_is_not_double_reported(self):
        """Measured: a two-turn log was summarised as "共 4 轮对话"."""
        (self.dir / "memory" / "2026-05-10.md").write_text(
            "# Daily log for 2026-05-10\n\n"
            "## [09:15] User\n第一句话内容足够长以便被视为有效内容\n\n"
            "## [09:15] Assistant\n第二句话内容足够长以便被视为有效内容\n\n"
            "## [14:30] User\n第三句话内容足够长以便被视为有效内容\n\n"
            "## [14:30] Assistant\n第四句话内容足够长以便被视为有效内容\n",
            encoding="utf-8")
        proc = run("decompose.py", "--workspace", str(self.dir), "--date", "2026-05-10")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = (self.dir / "memory" / "2026-05-10-decompose.md").read_text(encoding="utf-8")
        self.assertIn("共 2 轮对话", out)

    def test_missing_log_is_reported_not_crashed(self):
        proc = run("decompose.py", "--workspace", str(self.dir), "--date", "2026-01-01")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("[ERROR]", proc.stderr)


if __name__ == "__main__":
    unittest.main()


class TestChineseExtraction(unittest.TestCase):
    def setUp(self):
        self.tu = load("_textutil.py")

    def test_no_mid_word_fragments(self):
        """Measured on the shipped version: the 2-4 char sliding window emitted
        `务架构`, `库连`, `接池`, `池配`, `置优` — fragments cut mid-word that
        match nothing useful and pollute the index."""
        text = "数据库连接池配置优化方案评估与同步调用改造"
        kws = self.tu.extract_keywords(text)
        fragments = {"务架构", "库连", "连接池配", "接池", "池配", "置优",
                     "优化方", "方案评", "同步调", "调用改"}
        for frag in fragments:
            self.assertNotIn(frag, kws, f"fragment '{frag}' must not appear in {kws}")

    def test_segmented_into_word_like_tokens(self):
        kws = self.tu.extract_keywords("数据库连接池配置优化方案评估")
        for w in ("数据库", "连接池", "配置", "优化", "方案", "评估"):
            self.assertIn(w, kws, f"expected word token '{w}' in {kws}")

    def test_mixed_chinese_and_english(self):
        kws = self.tu.extract_keywords("使用 Redis 连接池与 Kafka 队列做异步消息处理")
        self.assertIn("redis", [k.lower() for k in kws])
        self.assertIn("kafka", [k.lower() for k in kws])
        self.assertIn("连接池", kws)
        self.assertIn("队列", kws)
        self.assertIn("异步", kws)

    def test_domain_terms_segment(self):
        kws = self.tu.extract_keywords("智能体锐利度评分与多智能体会议协作系统")
        for w in ("智能体", "锐利", "评分", "协作", "会议", "系统"):
            self.assertIn(w, kws, f"expected domain token '{w}' in {kws}")


class TestWeightDemotion(Workspace):
    def _index(self, body):
        (self.dir / "MEMORY.md").write_text(body, encoding="utf-8")
        (self.dir / "experience").mkdir(exist_ok=True)
        for name in ("curated.md", "auto.md"):
            (self.dir / "experience" / name).write_text("x", encoding="utf-8")

    def test_weight_is_parsed_and_applied(self):
        """Auto-indexed entries carry `权重：0.5`; the final score must be
        multiplied by it so a weak auto match no longer out-ranks curated."""
        scan = load("startup_scan.py")
        mem = ("# Memory\n\n"
               "- curated.md\n  触发关键词：“数据库”\n  触发条件：“选型”\n\n"
               "- auto.md\n  触发关键词：“数据库”\n  触发条件：“选型”\n  权重：0.5\n")
        self._index(mem)
        entries = scan.parse_memory_index(mem)
        curated = next(e for e in entries if e["filename"] == "curated.md")
        auto = next(e for e in entries if e["filename"] == "auto.md")
        self.assertEqual(curated["weight"], 1.0)
        self.assertEqual(auto["weight"], 0.5)
        s_c, _ = scan.score_entry(curated, "数据库选型")
        s_a, _ = scan.score_entry(auto, "数据库选型")
        self.assertAlmostEqual(s_c, 1.0)
        self.assertAlmostEqual(s_a, 0.5)
        self.assertGreater(s_c, s_a)

    def test_auto_entry_still_surfaces_when_relevant(self):
        """A lower weight must not hide an auto entry that clearly matches."""
        scan = load("startup_scan.py")
        mem = ("# Memory\n\n"
               "- auto.md\n  触发关键词：“数据库”\n  触发条件：“选型”\n  权重：0.5\n")
        self._index(mem)
        proc = run("startup_scan.py", "--workspace", str(self.dir), "--task", "数据库选型")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("auto.md", proc.stdout)
        self.assertIn("weight 0.5", proc.stdout)
        self.assertIn("auto", proc.stdout)

    def test_missing_weight_defaults_to_trusted(self):
        scan = load("startup_scan.py")
        mem = ("# Memory\n\n"
               "- curated.md\n  触发关键词：“数据库”\n  触发条件：“选型”\n")
        self._index(mem)
        entries = scan.parse_memory_index(mem)
        self.assertEqual(entries[0]["weight"], 1.0)


class TestAutoIndex(Workspace):
    def test_unindexed_file_gets_weighted_entry(self):
        """Measured: auto-backfill used a placeholder condition `关于<name>的事项`
        and full-trust weight. Now it must use the event name as condition and
        carry `权重：0.5`."""
        make_experience(self.dir / "experience" / "lesson_db.md", "lesson", "数据库选型",
                        "2026-07-01", "Redis 连接池配置优化方案评估",
                        "项目需要缓存层", "使用 Redis 连接池")
        (self.dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
        proc = run("merge_experiences.py", "--workspace", str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        mem = (self.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("lesson_db.md", mem)
        self.assertIn("权重：0.5", mem, "auto-indexed entry must carry a lower weight")
        self.assertNotIn("关于", mem, "placeholder condition must be gone")
        self.assertNotIn("务架构", mem, "no mid-word fragment keywords")

    def test_merged_entry_inherits_auto_weight(self):
        """When a cluster mixes a curated and an auto (0.5) entry, the merged
        entry must stay lower-trust."""
        make_experience(self.dir / "experience" / "decision_a.md", "decision", "缓存方案",
                        "2026-05-01", "选择 Redis 缓存方案", "需要缓存", "使用 Redis 连接池")
        make_experience(self.dir / "experience" / "decision_b.md", "decision", "缓存方案B",
                        "2026-05-02", "选择 Redis 缓存方案 版本二", "需要缓存", "使用 Redis 连接池")
        (self.dir / "MEMORY.md").write_text(
            "# Memory Index\n\n"
            "- decision_a.md\n  触发关键词：“缓存”\n  触发条件：“需要缓存”\n\n"
            "- decision_b.md\n  触发关键词：“缓存”\n  触发条件：“需要缓存”\n  权重：0.5\n\n",
            encoding="utf-8")
        proc = run("merge_experiences.py", "--workspace", str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        mem = (self.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("权重：0.5", mem, "merged entry mixing an auto member must stay lower-trust")


class TestAutoMergeBehaviour(unittest.TestCase):
    """Locks down the auto-merge fixes (overlap metric, same-round linkage,
    observability, orphan-free index). Every case below was measured on the
    shipped version before being fixed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "experience").mkdir()
        (self.dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def _exp(self, name, event, date, summary, desc, design):
        (self.dir / "experience" / name).write_text(
            f"Event Type: lesson\nEvent Name: {event}\nDate: {date}\n"
            f"Summary: {summary}\n\n"
            f"## Full Description / Complete Resolution Process\n{desc}\n\n"
            f"## Design / Solution\n{design}\n", encoding="utf-8")

    def _files(self):
        return sorted(p.name for p in (self.dir / "experience").glob("*.md"))

    def _indexed(self):
        import re
        txt = (self.dir / "MEMORY.md").read_text(encoding="utf-8")
        return sorted(set(re.findall(r"^\s*-\s+([\w\-.]+\.md)\s*$", txt, re.M)))

    def test_auto_duplicates_merge_on_the_first_run(self):
        """Measured: trigger Jaccard was 7/9 = 0.7778 (<0.8) so these duplicates
        were skipped, AND even qualifying files needed a second invocation
        because Phase 2 read the index before Phase 1 indexed anything."""
        desc = "消费者组重平衡导致消费延迟抖动，调整分区数与拉取参数。"
        for name, design in (("k_a.md", "调优拉取批次与提交策略。"),
                             ("k_b.md", "增加消费者实例并错峰提交。")):
            self._exp(name, "Kafka消费者组调优",
                      "2026-04-0" + ("1" if name == "k_a.md" else "2"),
                      "Kafka 消费者组并发调优与重平衡优化", desc, design)
        proc = run("merge_experiences.py", "--workspace", str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = self._files()
        self.assertEqual(len(files), 1, f"expected one merged file, got {files}")
        self.assertEqual(self._indexed(), files,
                         "index must not reference files that were merged away")

    def test_rejections_are_printed_never_silent(self):
        """Measured: a trigger-threshold miss did `continue` with no output, so
        the run reported 'Nothing to do. ✓' while duplicates sat un-merged."""
        self._exp("u1.md", "Redis缓存穿透", "2026-05-01",
                  "Redis 缓存穿透用空值缓存与布隆过滤器解决",
                  "热点 key 失效导致数据库压力骤增，引入布隆过滤器拦截无效查询。",
                  "布隆过滤器加空值缓存。")
        self._exp("u2.md", "Redis连接池调优", "2026-05-02",
                  "Redis 连接池参数调优提升并发读取能力",
                  "连接池过小导致请求排队阻塞，调整最大连接与回收策略。",
                  "合理设置池大小与空闲回收时间。")
        proc = run("merge_experiences.py", "--workspace", str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue("[BELOW TRIGGERS]" in proc.stdout
                        or "[BELOW CONTENT]" in proc.stdout,
                        "a rejected pair must be reported, not skipped silently")
        self.assertEqual(len(self._files()), 2, "these two must not merge")

    def test_same_domain_different_topic_still_separate(self):
        """Guard: the looser auto threshold (0.5) must not merge files that
        merely share a domain. Measured overlap 0.222/0.250."""
        self._exp("s1.md", "Redis缓存穿透", "2026-05-01",
                  "Redis 缓存穿透用空值缓存与布隆过滤器解决",
                  "热点 key 失效导致数据库压力骤增，引入布隆过滤器拦截无效查询。",
                  "布隆过滤器加空值缓存，设置较短过期时间。")
        self._exp("s2.md", "Redis连接池调优", "2026-05-02",
                  "Redis 连接池参数调优提升并发读取能力",
                  "连接池过小导致请求排队阻塞，调整最大连接与回收策略。",
                  "合理设置池大小与空闲回收时间。")
        proc = run("merge_experiences.py", "--workspace", str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self._files()), 2, f"must stay separate: {self._files()}")

    def test_overlap_and_jaccard_differ_as_intended(self):
        """The two metrics must stay distinct: curated pairs keep strict
        Jaccard, auto pairs use the inclusion-based overlap."""
        mod = load("merge_experiences.py")
        small, big = {1, 2, 3}, {1, 2, 3, 4}
        self.assertAlmostEqual(mod.overlap_coefficient(small, big), 1.0)
        self.assertAlmostEqual(mod.jaccard_similarity(small, big), 0.75)
        self.assertEqual(mod.overlap_coefficient(set(), big), 0.0)
        self.assertGreaterEqual(mod.MIN_SHARED_TRIGGERS, 1)
        for name in ("AUTO_PAIR_KEYWORD_THRESHOLD", "AUTO_PAIR_CONTENT_THRESHOLD"):
            self.assertTrue(hasattr(mod, name), f"{name} should be configurable")
        self.assertLess(mod.AUTO_PAIR_KEYWORD_THRESHOLD,
                        mod.AUTO_MERGE_KEYWORD_THRESHOLD,
                        "auto threshold must be looser than the curated one")


if __name__ == "__main__":
    unittest.main()
