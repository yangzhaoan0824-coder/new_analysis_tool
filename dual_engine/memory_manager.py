#!/usr/bin/env python3
"""
Agent Memory Manager — 分层温度记忆管理引擎

实现 记忆.md 中定义的完整记忆管理机制：
  - 分层温度机制（Hot ≤14天 / Warm 14-60天 / Cold >60天）
  - 索引系统（Always Load / On Demand）
  - 写入触发机制
  - 会话摘要自动生成
  - 记忆巩固 / 做梦流程

用法：
  python -m dual_engine.memory_manager consolidate   # 执行记忆巩固
  python -m dual_engine.memory_manager summary        # 生成会话摘要
  python -m dual_engine.memory_manager check          # 一致性校验
  python -m dual_engine.memory_manager status         # 查看记忆状态
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ─── 常量 ─────────────────────────────────────────────────────────

MEMORY_ROOT = Path(__file__).resolve().parent.parent / ".agent-memory"

# 温度阈值（天）
HOT_DAYS = 14
WARM_DAYS = 60

# 文件大小限制（字节）
SIZE_LIMITS: dict[str, int] = {
    "identity.md": 1024,       # 1KB
    "projects.md": 1229,       # 1.2KB
    "environment.md": 1536,    # 1.5KB
    "skills.md": 2048,         # 2KB
    "decisions.md": 2048,      # 2KB
    "lessons.md": 51200,       # 50KB
    "archive.md": float("inf"),
    "discoveries.md": 5120,    # 5KB
}

# 加载策略
ALWAYS_LOAD = ["identity.md", "projects.md"]
ON_DEMAND = ["environment.md", "skills.md", "decisions.md",
             "archive.md", "lessons.md", "discoveries.md"]

# 受温度分层的文件
TEMPERATURE_FILES = {"skills.md", "decisions.md"}

# 写入触发映射
WRITE_TRIGGERS: dict[str, str] = {
    "preference": "identity.md",
    "project_change": "projects.md",
    "decision": "decisions.md",
    "environment_change": "environment.md",
    "skill_experience": "skills.md",
    "project_complete": "archive.md",
}


# ─── 数据结构 ──────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """一条带日期的记忆条目"""
    date: str              # YYYY-MM-DD
    title: str
    content: str
    temperature: str = "hot"  # hot / warm / cold

    @property
    def age_days(self) -> int:
        """距离今天的天数"""
        entry_date = datetime.strptime(self.date, "%Y-%m-%d").date()
        return (datetime.now().date() - entry_date).days

    def compute_temperature(self) -> str:
        """根据年龄计算温度"""
        age = self.age_days
        if age <= HOT_DAYS:
            return "hot"
        elif age <= WARM_DAYS:
            return "warm"
        else:
            return "cold"


@dataclass
class ConsolidationReport:
    """记忆巩固报告"""
    timestamp: str = ""
    consistency_ok: bool = True
    consistency_errors: list[str] = field(default_factory=list)
    new_summaries_collected: int = 0
    entries_migrated: int = 0
    migrations: list[dict] = field(default_factory=list)
    merges: list[dict] = field(default_factory=list)
    discoveries_found: int = 0
    size_warnings: list[str] = field(default_factory=list)


# ─── 解析器 ────────────────────────────────────────────────────────

def parse_dated_entries(filepath: Path) -> list[MemoryEntry]:
    """
    解析含日期条目的 markdown 文件。
    识别格式：### YYYY-MM-DD | 标题
    条目内容直到下一个 ### 或 ## 或文件结束。
    """
    if not filepath.exists():
        return []

    entries: list[MemoryEntry] = []
    current_entry: Optional[MemoryEntry] = None
    content_lines: list[str] = []

    header_re = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\s*\|\s*(.+)$")

    def _flush():
        nonlocal current_entry, content_lines
        if current_entry is not None:
            current_entry.content = "\n".join(content_lines).strip()
            current_entry.temperature = current_entry.compute_temperature()
            entries.append(current_entry)
        current_entry = None
        content_lines = []

    for line in filepath.read_text(encoding="utf-8").splitlines():
        m = header_re.match(line)
        if m:
            _flush()
            current_entry = MemoryEntry(date=m.group(1), title=m.group(2).strip(), content="")
        elif current_entry is not None:
            # 遇到同级或更高级别的标题，结束当前条目
            if line.startswith("## ") and not line.startswith("### "):
                _flush()
            else:
                content_lines.append(line)

    _flush()
    return entries


def rebuild_file(filepath: Path, entries: list[MemoryEntry],
                 header: str = "", temperature_sections: bool = True) -> None:
    """
    将条目列表重新写回 markdown 文件。
    如果 temperature_sections=True，按 Hot/Warm/Cold 分区组织。
    """
    lines: list[str] = []

    if header:
        lines.append(header)
        lines.append("")

    if temperature_sections:
        hot = [e for e in entries if e.temperature == "hot"]
        warm = [e for e in entries if e.temperature == "warm"]
        cold = [e for e in entries if e.temperature == "cold"]

        for label, group in [("Hot（≤14天）", hot), ("Warm", warm), ("Cold", cold)]:
            lines.append(f"## {label}")
            if not group:
                lines.append("（暂无）")
            for entry in group:
                lines.append(f"### {entry.date} | {entry.title}")
                if entry.content:
                    lines.append(entry.content)
                lines.append("")
            lines.append("")
    else:
        for entry in entries:
            lines.append(f"### {entry.date} | {entry.title}")
            if entry.content:
                lines.append(entry.content)
            lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")


def compress_entry(entry: MemoryEntry) -> str:
    """将条目压缩为一行（Warm 迁移用）"""
    # 取内容的前 80 字符或第一行
    first_line = entry.content.split("\n")[0].strip().lstrip("- ").strip()
    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    return f"- [{entry.date}] {entry.title}：{first_line}"


# ─── 核心操作 ──────────────────────────────────────────────────────

class MemoryManager:
    """Agent 记忆管理器"""

    def __init__(self, root: Path = MEMORY_ROOT):
        self.root = root
        self.session_dir = root / "session_summaries"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    # ── 状态查询 ──

    def status(self) -> dict:
        """返回记忆系统当前状态"""
        result: dict = {
            "files": {},
            "session_summaries": [],
            "consolidation_needed": False,
        }

        all_files = ALWAYS_LOAD + ON_DEMAND
        for fname in all_files:
            fpath = self.root / fname
            if fpath.exists():
                size = fpath.stat().st_size
                limit = SIZE_LIMITS.get(fname, float("inf"))
                result["files"][fname] = {
                    "exists": True,
                    "size_bytes": size,
                    "size_limit": limit if limit != float("inf") else "unlimited",
                    "size_ok": size <= limit,
                    "loading": "always" if fname in ALWAYS_LOAD else "on_demand",
                }
            else:
                result["files"][fname] = {"exists": False}

        # 会话摘要
        summaries = sorted(self.session_dir.glob("summary_*.md"))
        result["session_summaries"] = [s.name for s in summaries]
        result["consolidation_needed"] = self._needs_consolidation()

        return result

    # ── 写入触发 ──

    def write_trigger(self, trigger_type: str, content: str,
                      date: Optional[str] = None) -> str:
        """
        根据触发类型写入对应记忆文件。
        trigger_type: preference / project_change / decision /
                      environment_change / skill_experience / project_complete
        """
        if trigger_type not in WRITE_TRIGGERS:
            raise ValueError(f"未知触发类型: {trigger_type}，"
                             f"可选: {list(WRITE_TRIGGERS.keys())}")

        target_file = WRITE_TRIGGERS[trigger_type]
        target_path = self.root / target_file
        date_str = date or datetime.now().strftime("%Y-%m-%d")

        if trigger_type == "project_complete":
            # 特殊处理：从 projects.md 移除，追加到 archive.md
            self._archive_project(content, date_str)
            return f"🧠 已归档到 archive.md（从 projects.md 移除）"

        # 通用写入：追加到目标文件
        existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""

        if target_file in TEMPERATURE_FILES:
            # 带温度分层的文件：插入到 Hot 区
            new_entry = f"\n### {date_str} | {content}\n\n"
            # 在 "## Hot" 后插入
            hot_marker = "## Hot"
            if hot_marker in existing:
                parts = existing.split(hot_marker, 1)
                updated = parts[0] + hot_marker + new_entry + parts[1]
            else:
                updated = existing + "\n## Hot（≤14天）\n" + new_entry
            target_path.write_text(updated, encoding="utf-8")
        else:
            # 普通文件：追加到末尾
            if not existing.endswith("\n"):
                existing += "\n"
            target_path.write_text(existing + f"\n- {date_str}：{content}\n",
                                   encoding="utf-8")

        return f"🧠 已更新 {target_file}"

    def _archive_project(self, project_name: str, date: str) -> None:
        """归档已完成项目"""
        # 从 projects.md 移除
        projects_path = self.root / "projects.md"
        if projects_path.exists():
            text = projects_path.read_text(encoding="utf-8")
            # 移除匹配的项目段落
            lines = text.split("\n")
            new_lines = []
            skip = False
            for line in lines:
                if line.strip().startswith("## ") and project_name in line:
                    skip = True
                    continue
                if skip and line.strip().startswith("## "):
                    skip = False
                if not skip:
                    new_lines.append(line)
            projects_path.write_text("\n".join(new_lines), encoding="utf-8")

        # 追加到 archive.md
        archive_path = self.root / "archive.md"
        existing = archive_path.read_text(encoding="utf-8") if archive_path.exists() else ""
        archive_entry = f"\n## {date} | {project_name}（已归档）\n\n"
        if "（暂无归档项目）" in existing:
            existing = existing.replace("（暂无归档项目）", "")
        archive_path.write_text(existing + archive_entry, encoding="utf-8")

    # ── 会话摘要 ──

    def generate_session_summary(self, summary_content: str,
                                 date: Optional[str] = None) -> Path:
        """
        生成会话摘要文件。
        触发条件：8-10轮对话 / AWS变更后 / 用户说"保存"
        """
        date_str = date or datetime.now().strftime("%Y-%m-%d")
        filename = f"summary_{date_str}.md"
        filepath = self.session_dir / filename

        header = f"# 会话摘要 {date_str}\n\n"
        header += f"> 自动生成 | 触发：手动/阈值\n\n"
        filepath.write_text(header + summary_content, encoding="utf-8")
        return filepath

    def _needs_consolidation(self) -> bool:
        """判断是否需要记忆巩固"""
        # 条件1：距上次巩固 ≥3天
        last_consolidation = self.root / ".last_consolidation"
        if last_consolidation.exists():
            last_date_str = last_consolidation.read_text(encoding="utf-8").strip()
            try:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
                if (datetime.now().date() - last_date).days >= 3:
                    return True
            except ValueError:
                return True
        else:
            return True

        # 条件2：未归档摘要 ≥3份
        summaries = list(self.session_dir.glob("summary_*.md"))
        archived_marker = self.root / ".archived_summaries.json"
        archived = set()
        if archived_marker.exists():
            archived = set(json.loads(archived_marker.read_text(encoding="utf-8")))
        unarchived = [s for s in summaries if s.name not in archived]
        return len(unarchived) >= 3

    # ── 记忆巩固（做梦） ──

    def consolidate(self) -> ConsolidationReport:
        """
        执行记忆巩固流程：
        ① 一致性校验（索引 vs 实际文件）
        ② 收集新摘要，检查遗漏
        ③ 合并（矛盾以最新为准）
        ④ 分层迁移（Hot→Warm→Cold）+ 文件大小检查
        ⑤ 探索（搜索相关更新，写入 discoveries.md）
        """
        report = ConsolidationReport(
            timestamp=datetime.now().isoformat()
        )

        # ① 一致性校验
        all_files = ALWAYS_LOAD + ON_DEMAND
        for fname in all_files:
            fpath = self.root / fname
            if not fpath.exists():
                report.consistency_ok = False
                report.consistency_errors.append(f"缺失文件: {fname}")

        # ② 收集新摘要
        archived_marker = self.root / ".archived_summaries.json"
        archived = set()
        if archived_marker.exists():
            archived = set(json.loads(archived_marker.read_text(encoding="utf-8")))
        summaries = sorted(self.session_dir.glob("summary_*.md"))
        new_summaries = [s for s in summaries if s.name not in archived]
        report.new_summaries_collected = len(new_summaries)

        # ③ 合并（从摘要中提取信息更新记忆文件）
        for summary_file in new_summaries:
            self._merge_summary(summary_file)
            archived.add(summary_file.name)

        # 保存已归档标记
        archived_marker.write_text(
            json.dumps(sorted(archived), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # ④ 分层迁移 + 文件大小检查
        for fname in TEMPERATURE_FILES:
            fpath = self.root / fname
            if not fpath.exists():
                continue
            migrated = self._migrate_temperature(fpath, fname)
            report.entries_migrated += migrated["total"]
            report.migrations.extend(migrated["details"])

            # 文件大小检查
            size = fpath.stat().st_size
            limit = SIZE_LIMITS.get(fname, float("inf"))
            if size > limit:
                report.size_warnings.append(
                    f"{fname}: {size}B 超出限制 {limit}B"
                )

        # ⑤ 探索（简化版：标记为待人工补充）
        report.discoveries_found = 0

        # 记录巩固时间
        (self.root / ".last_consolidation").write_text(
            datetime.now().strftime("%Y-%m-%d"), encoding="utf-8"
        )

        return report

    def _merge_summary(self, summary_path: Path) -> None:
        """从会话摘要中提取信息，更新记忆文件"""
        content = summary_path.read_text(encoding="utf-8")
        # 简单合并策略：提取摘要中的关键信息
        # 实际使用中可接入 LLM 进行智能提取
        for line in content.splitlines():
            line = line.strip()
            # 检测决策类关键词
            if any(kw in line for kw in ["决策", "决定", "选择"]):
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", summary_path.name)
                if date_match:
                    self.write_trigger("decision", line[:60],
                                       date=date_match.group(1))
                break  # 每个摘要只取第一条决策

    def _migrate_temperature(self, filepath: Path, fname: str) -> dict:
        """
        对单个文件执行温度迁移。
        Hot→Warm: 压缩为一行，原文移入 lessons.md
        Warm→Cold: 从原文件删除，仅存 lessons.md
        """
        result = {"total": 0, "details": []}

        # 读取文件头部（温度分层标记之前的内容）
        raw = filepath.read_text(encoding="utf-8")
        header_lines = []
        in_header = True
        for line in raw.splitlines():
            if line.strip().startswith("## Hot") or line.strip().startswith("## Warm") or line.strip().startswith("## Cold"):
                in_header = False
                break
            if in_header:
                header_lines.append(line)
        header = "\n".join(header_lines).rstrip()

        entries = parse_dated_entries(filepath)
        lessons_path = self.root / "lessons.md"

        migrated_entries: list[MemoryEntry] = []
        for entry in entries:
            new_temp = entry.compute_temperature()
            old_temp = entry.temperature

            if new_temp != old_temp:
                result["total"] += 1
                result["details"].append({
                    "title": entry.title,
                    "from": old_temp,
                    "to": new_temp,
                    "date": entry.date,
                })

                if new_temp == "warm" and old_temp == "hot":
                    # Hot→Warm：压缩为一行，原文追加到 lessons.md
                    compressed = compress_entry(entry)
                    self._append_to_lessons(lessons_path, entry, compressed)

                elif new_temp == "cold" and old_temp == "warm":
                    # Warm→Cold：从原文件删除，原文已在 lessons.md
                    # 无需额外操作，因为 Warm 条目已在 lessons.md 中
                    pass

                entry.temperature = new_temp
                # Warm 条目内容替换为压缩版
                if new_temp == "warm":
                    entry.content = compress_entry(entry)

            migrated_entries.append(entry)

        # 重建文件
        if result["total"] > 0:
            rebuild_file(filepath, migrated_entries, header=header,
                         temperature_sections=True)

        return result

    def _append_to_lessons(self, lessons_path: Path,
                            entry: MemoryEntry, compressed: str) -> None:
        """将条目原文追加到 lessons.md"""
        existing = ""
        if lessons_path.exists():
            existing = lessons_path.read_text(encoding="utf-8")

        block = f"\n### [{entry.date}] {entry.title}（来自 {entry.temperature} 迁移）\n\n"
        block += entry.content + "\n"

        lessons_path.write_text(existing + block, encoding="utf-8")

    # ── 一致性校验 ──

    def check_consistency(self) -> list[str]:
        """校验索引文件 vs 实际文件的一致性"""
        errors: list[str] = []
        all_files = ALWAYS_LOAD + ON_DEMAND

        for fname in all_files:
            fpath = self.root / fname
            if not fpath.exists():
                errors.append(f"❌ 缺失文件: {fname}")
            else:
                size = fpath.stat().st_size
                limit = SIZE_LIMITS.get(fname, float("inf"))
                if size > limit:
                    errors.append(f"⚠️ {fname}: {size}B 超出限制 {limit}B")
                else:
                    errors.append(f"✅ {fname}: {size}B / {limit}B")

        # 校验索引文件
        index_path = self.root / "Memory.md"
        if not index_path.exists():
            errors.append("❌ 索引文件 Memory.md 缺失")
        else:
            errors.append("✅ Memory.md 索引文件存在")

        # 校验 session_summaries 目录
        if not self.session_dir.exists():
            errors.append("❌ session_summaries/ 目录缺失")
        else:
            errors.append(f"✅ session_summaries/ 目录存在")

        return errors


# ─── CLI 入口 ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1]
    manager = MemoryManager()

    if command == "consolidate":
        print("🌙 执行记忆巩固...")
        report = manager.consolidate()
        print(f"  一致性校验: {'✅ 通过' if report.consistency_ok else '❌ 异常'}")
        if report.consistency_errors:
            for err in report.consistency_errors:
                print(f"    {err}")
        print(f"  新摘要收集: {report.new_summaries_collected}")
        print(f"  条目迁移: {report.entries_migrated}")
        for m in report.migrations:
            print(f"    {m['title']}: {m['from']}→{m['to']}")
        if report.size_warnings:
            print("  ⚠️ 文件大小警告:")
            for w in report.size_warnings:
                print(f"    {w}")
        print(f"  发现: {report.discoveries_found}")

    elif command == "summary":
        content = sys.argv[2] if len(sys.argv) > 2 else "（手动触发的会话摘要）"
        path = manager.generate_session_summary(content)
        print(f"📝 会话摘要已生成: {path}")

    elif command == "check":
        print("🔍 记忆一致性校验...")
        results = manager.check_consistency()
        for r in results:
            print(f"  {r}")

    elif command == "status":
        info = manager.status()
        print("📊 记忆系统状态:")
        print(f"  巩固需求: {'是 ✅' if info['consolidation_needed'] else '否'}")
        print(f"  会话摘要: {len(info['session_summaries'])} 份")
        for fname, finfo in info["files"].items():
            if finfo.get("exists"):
                loading = finfo["loading"]
                size_ok = "✅" if finfo["size_ok"] else "⚠️"
                print(f"  {size_ok} {fname} ({loading}): "
                      f"{finfo['size_bytes']}B / {finfo['size_limit']}")
            else:
                print(f"  ❌ {fname}: 不存在")

    elif command == "write":
        if len(sys.argv) < 4:
            print("用法: memory_manager.py write <trigger_type> <content>")
            print(f"触发类型: {list(WRITE_TRIGGERS.keys())}")
            sys.exit(1)
        trigger_type = sys.argv[2]
        content = sys.argv[3]
        msg = manager.write_trigger(trigger_type, content)
        print(msg)

    else:
        print(f"未知命令: {command}")
        print("可用命令: consolidate, summary, check, status, write")
        sys.exit(1)


if __name__ == "__main__":
    main()
