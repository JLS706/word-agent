# -*- coding: utf-8 -*-
"""
Tool: 文档格式检查器 — Agent 的"格式感知眼镜"

设计哲学（Tool-Skill 分离）：
  - **工具 = 纯能力引擎**：知道 HOW（如何读取和诊断格式），但不知道 WHAT（什么格式是对的）
  - **Skill = 领域知识**：提供具体的格式规范参数（字体、字号、行距等）
  - **没有 Skill 注入参数 → 工具无法执行**：天然防止用错误默认值"好心办坏事"
  - 只看格式，不输出内容：每段只显示前 20 字作为定位标记
  - 将 COM 接口的底层属性翻译为高信息密度的语义化报告

与其他工具的职责分离：
  - read_document    → 看内容（纯文本），不看格式
  - inspect_format   → 看格式（样式/字体/缩进），不看内容
  - analyze_document → 宏观统计（有多少图注/文献），不做逐段检查
"""

import os
from tools.base import Tool


# ────────────────────────────────────────────
# 🚫 硬编码格式规范已移除（Tool-Skill 分离架构）
#
# 工具本身不再包含任何领域知识（如"宋体""12pt"等具体值）。
# 所有格式规范参数必须通过 Skill 的 config 块注入。
#
# 提供参数的 Skill 文件：
#   - skills/format_rules_base.md    — 通用中文学术规范（宋体/小四号/1.5倍行距）
#   - skills/format_rules_sample.md  — 示例特化规范（仿宋/三号/1.25倍行距）
#   - 用户可自行创建 format_rules_xxx.md 支持任意学校规范
# ────────────────────────────────────────────

# 对齐方式常量映射
_ALIGNMENT_MAP = {
    0: "左对齐",
    1: "居中",
    2: "右对齐",
    3: "两端对齐",
    4: "分散对齐",
}

# 行距规则常量映射
_LINE_SPACING_RULE_MAP = {
    0: "多倍行距",    # wdLineSpaceMultiple
    1: "1.5倍行距",   # wdLineSpace1pt5
    2: "2倍行距",     # wdLineSpaceDouble
    3: "最小值",      # wdLineSpaceAtLeast
    4: "固定值",      # wdLineSpaceExactly
    5: "单倍行距",    # wdLineSpaceSingle
}


def _pt_to_cm(pt: float) -> float:
    """磅 → 厘米"""
    return round(pt / 28.35, 2)


def _categorize_style(style_name: str) -> str:
    """将样式名归类（用于匹配规范）"""
    if not style_name:
        return "未知"
    for keyword in ["标题 1", "Heading 1"]:
        if keyword in style_name:
            return "标题 1"
    for keyword in ["标题 2", "Heading 2"]:
        if keyword in style_name:
            return "标题 2"
    for keyword in ["标题", "Heading"]:
        if keyword in style_name:
            return "标题"
    for keyword in ["正文", "Normal", "Body"]:
        if keyword in style_name:
            return "正文"
    return style_name


def _diagnose_paragraph(info: dict, rules: dict) -> list[str]:
    """
    根据格式规范诊断段落的格式问题。

    Args:
        info: 段落格式信息字典
        rules: 对应样式类别的规范

    Returns:
        问题描述列表（空列表 = 正常）
    """
    issues = []
    if not rules:
        return issues

    # 字体检查
    if "font_cn" in rules and info.get("font_cn"):
        if rules["font_cn"] not in str(info["font_cn"]):
            issues.append(
                f"中文字体应为 {rules['font_cn']}，"
                f"实际为 {info['font_cn']}"
            )

    if "font_en" in rules and info.get("font_en"):
        if rules["font_en"] not in str(info["font_en"]):
            issues.append(
                f"西文字体应为 {rules['font_en']}，"
                f"实际为 {info['font_en']}"
            )

    # 字号检查
    if "font_size" in rules and info.get("font_size"):
        expected = rules["font_size"]
        actual = info["font_size"]
        if abs(actual - expected) > 0.5:
            issues.append(f"字号应为 {expected}pt，实际为 {actual}pt")

    if "font_size_min" in rules and info.get("font_size"):
        if info["font_size"] < rules["font_size_min"] - 0.5:
            issues.append(
                f"字号 {info['font_size']}pt 低于规范下限 {rules['font_size_min']}pt"
            )
    if "font_size_max" in rules and info.get("font_size"):
        if info["font_size"] > rules["font_size_max"] + 0.5:
            issues.append(
                f"字号 {info['font_size']}pt 超过规范上限 {rules['font_size_max']}pt"
            )

    # 加粗检查
    if "bold" in rules and info.get("bold") is not None:
        if rules["bold"] and not info["bold"]:
            issues.append("应为加粗，但实际未加粗")

    # 首行缩进检查（仅对正文类段落）
    if "first_indent_cm" in rules and info.get("first_indent_cm") is not None:
        expected_cm = rules["first_indent_cm"]
        actual_cm = info["first_indent_cm"]
        if abs(actual_cm - expected_cm) > 0.1:
            issues.append(
                f"首行缩进应为 {expected_cm}cm（约2字符），"
                f"实际为 {actual_cm}cm"
            )

    # 对齐检查
    if "alignment" in rules and info.get("alignment") is not None:
        expected_align = rules["alignment"]
        actual_align = info["alignment"]
        if actual_align != expected_align:
            expected_name = _ALIGNMENT_MAP.get(expected_align, str(expected_align))
            actual_name = _ALIGNMENT_MAP.get(actual_align, str(actual_align))
            issues.append(f"对齐应为{expected_name}，实际为{actual_name}")

    return issues


class InspectDocFormatTool(Tool):
    """
    文档格式检查工具 — 只看格式，不输出内容。

    将 COM 接口的底层格式属性翻译为语义化报告，
    让 LLM 能"看见"每段落的样式、字体、缩进、行距等信息。
    """

    name = "inspect_document_format"
    description = (
        "检查Word文档的格式和排版属性（样式、字体、缩进、行距、对齐等）。\n"
        "只查格式，不输出内容（节省token）。每段仅显示前20字作为定位标记。\n"
        "支持分页查看（每次20段），自动诊断格式异常。\n"
        "适用场景：\n"
        "- 检查论文格式是否符合学术规范\n"
        "- 在修复格式后验证修复效果（Diff对比）\n"
        "- Reviewer 审查排版结果\n"
        "这是一个只读工具，不会修改文档。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径",
            },
            "start_para": {
                "type": "integer",
                "description": "起始段落号（从1开始，默认1）",
            },
            "end_para": {
                "type": "integer",
                "description": (
                    "结束段落号（默认为 start_para+19，即每次查看20段）。"
                    "设为 -1 表示查到文档末尾。"
                ),
            },
            "check_tables": {
                "type": "boolean",
                "description": "是否检查范围内的表格结构（默认 true）",
            },
            "check_fields": {
                "type": "boolean",
                "description": (
                    "是否检查域代码和书签（默认 false，开启会增加输出量）"
                ),
            },
        },
        "required": ["file_path"],
    }

    def execute(
        self,
        file_path: str,
        start_para: int = 1,
        end_para: int = 0,
        check_tables: bool = True,
        check_fields: bool = False,
        format_rules: dict = None,
    ) -> str:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"文件不存在: {abs_path}"

        # ── 【物理断头台】format_rules 必须由 Skill 注入，无退路 ──
        # 不是 return 提示文本（LLM 会无视），而是直接 raise 异常，
        # 让 Agent 的错误分类器生成不可忽视的结构化修正指令。
        if not format_rules:
            raise ValueError(
                "缺少必须的排版规范参数 (format_rules)。"
                "请立刻停止盲目执行！你必须向用户追问需要使用的排版规范"
                "（例如：通用学术规范、张导师专属规范等），获取规范后再执行。"
            )
        active_rules = format_rules

        # 默认每次查看 20 段
        if end_para == 0:
            end_para = start_para + 19

        try:
            import win32com.client

            word = win32com.client.Dispatch("Word.Application")
            word.Visible = True

            # 复用已打开的文档
            doc = None
            try:
                for d in word.Documents:
                    if os.path.abspath(d.FullName).lower() == abs_path.lower():
                        doc = d
                        break
            except Exception:
                pass

            if doc is None:
                doc = word.Documents.Open(abs_path)
                opened_by_us = True
            else:
                opened_by_us = False

            total_paras = doc.Paragraphs.Count

            # 修正范围
            start_para = max(1, start_para)
            if end_para == -1:
                end_para = total_paras
            end_para = min(end_para, total_paras)

            report = []
            report.append(
                f"[文档格式检查报告 — 段落 {start_para}-{end_para}"
                f"/{total_paras}]"
            )
            report.append("")

            # ── 统计计数器 ──
            issue_severe = 0    # 🔴 严重
            issue_minor = 0     # 🟡 轻微
            issue_ok = 0        # 🟢 正常

            # ── 逐段落检查 ──
            for i in range(start_para, end_para + 1):
                para = doc.Paragraphs(i)
                rng = para.Range

                # 只取前 20 字作为定位标记
                raw_text = rng.Text.strip()
                if not raw_text:
                    continue  # 跳过空段落

                marker = raw_text[:20]
                if len(raw_text) > 20:
                    marker += "..."

                # ── 提取段落级格式 ──
                try:
                    style_name = str(para.Style.NameLocal)
                except Exception:
                    style_name = "(未知)"

                fmt = para.Format
                alignment = None
                first_indent_pt = None
                first_indent_cm = None
                line_spacing_rule = None
                line_spacing = None
                space_before = None
                space_after = None

                try:
                    alignment = int(fmt.Alignment)
                except Exception:
                    pass
                try:
                    first_indent_pt = float(fmt.FirstLineIndent)
                    first_indent_cm = _pt_to_cm(first_indent_pt)
                except Exception:
                    pass
                try:
                    line_spacing_rule = int(fmt.LineSpacingRule)
                except Exception:
                    pass
                try:
                    line_spacing = float(fmt.LineSpacing)
                except Exception:
                    pass
                try:
                    space_before = float(fmt.SpaceBefore)
                except Exception:
                    pass
                try:
                    space_after = float(fmt.SpaceAfter)
                except Exception:
                    pass

                # ── 提取字符级格式（段落首个 Run 的主导格式）──
                font = rng.Font
                font_cn = None
                font_en = None
                font_size = None
                bold = None
                italic = None

                try:
                    font_cn = font.NameFarEast
                except Exception:
                    pass
                try:
                    font_en = font.Name
                except Exception:
                    pass
                try:
                    font_size = float(font.Size)
                    # COM 返回 9999999 表示混合格式
                    if font_size > 500:
                        font_size = None
                except Exception:
                    pass
                try:
                    bold_val = font.Bold
                    # 0=False, -1=True, 9999999=混合
                    bold = bold_val != 0 if bold_val != 9999999 else None
                except Exception:
                    pass
                try:
                    italic_val = font.Italic
                    italic = italic_val != 0 if italic_val != 9999999 else None
                except Exception:
                    pass

                # ── 域代码检查（可选）──
                fields_info = []
                if check_fields:
                    try:
                        for fld in rng.Fields:
                            code = fld.Code.Text.strip()[:40]
                            result = fld.Result.Text.strip()[:20]
                            fields_info.append(f"{code} → {result}")
                    except Exception:
                        pass

                # ── 构建格式信息字典（供诊断用）──
                info = {
                    "style": style_name,
                    "font_cn": font_cn,
                    "font_en": font_en,
                    "font_size": font_size,
                    "bold": bold,
                    "italic": italic,
                    "alignment": alignment,
                    "first_indent_cm": first_indent_cm,
                    "line_spacing_rule": line_spacing_rule,
                    "line_spacing": line_spacing,
                }

                # ── 诊断格式问题（使用合并后的规范） ──
                category = _categorize_style(style_name)
                rules = active_rules.get(category, {})
                issues = _diagnose_paragraph(info, rules)

                # ── 输出段落报告 ──
                report.append(f"段落 {i}: \"{marker}\"")
                report.append(f"  - 样式: {style_name}")

                # 字体行
                font_parts = []
                if font_cn:
                    font_parts.append(font_cn)
                if font_en and font_en != font_cn:
                    font_parts.append(font_en)
                if font_size:
                    font_parts.append(f"{font_size}pt")
                if bold:
                    font_parts.append("加粗")
                if italic:
                    font_parts.append("斜体")
                if font_parts:
                    report.append(f"  - 字体: {', '.join(font_parts)}")

                # 段落格式行
                align_str = _ALIGNMENT_MAP.get(alignment, str(alignment))
                report.append(f"  - 对齐: {align_str}")

                if first_indent_cm is not None:
                    report.append(f"  - 首行缩进: {first_indent_cm}cm")

                # 行距
                if line_spacing_rule is not None:
                    rule_name = _LINE_SPACING_RULE_MAP.get(
                        line_spacing_rule, str(line_spacing_rule)
                    )
                    if line_spacing_rule == 0 and line_spacing:
                        # 多倍行距：值是倍数 × 12
                        ratio = round(line_spacing / 12, 1)
                        report.append(f"  - 行距: {ratio}倍行距")
                    elif line_spacing_rule in (3, 4) and line_spacing:
                        report.append(f"  - 行距: {rule_name} {line_spacing}磅")
                    else:
                        report.append(f"  - 行距: {rule_name}")

                # 段前段后
                spacing_parts = []
                if space_before and space_before > 0:
                    spacing_parts.append(f"段前{space_before}磅")
                if space_after and space_after > 0:
                    spacing_parts.append(f"段后{space_after}磅")
                if spacing_parts:
                    report.append(f"  - 间距: {', '.join(spacing_parts)}")

                # 域代码
                if fields_info:
                    for fi in fields_info:
                        report.append(f"  - 域: {fi}")

                # 诊断结果
                if issues:
                    for issue in issues:
                        report.append(f"  - 🔴 问题: {issue}")
                        issue_severe += 1
                else:
                    report.append(f"  - 🟢 诊断: 正常")
                    issue_ok += 1

                report.append("---------------------")

            # ── 表格检查（可选）──
            if check_tables:
                tables_in_range = []
                try:
                    for t_idx in range(1, doc.Tables.Count + 1):
                        table = doc.Tables(t_idx)
                        # 检查表格是否在段落范围内
                        table_para_start = table.Range.Paragraphs(1)
                        # 简单判断：检查表格起始位置
                        table_start_pos = table.Range.Start
                        range_start = doc.Paragraphs(start_para).Range.Start
                        range_end = doc.Paragraphs(end_para).Range.End
                        if range_start <= table_start_pos <= range_end:
                            rows = table.Rows.Count
                            cols = table.Columns.Count

                            # 检查表头格式
                            header_info = []
                            try:
                                header_row = table.Rows(1)
                                header_font = header_row.Range.Font
                                if header_font.Bold:
                                    header_info.append("加粗")
                                if header_font.Size and header_font.Size < 500:
                                    header_info.append(f"{header_font.Size}pt")
                            except Exception:
                                pass

                            tables_in_range.append({
                                "index": t_idx,
                                "rows": rows,
                                "cols": cols,
                                "header": header_info,
                            })
                except Exception:
                    pass

                if tables_in_range:
                    report.append("")
                    for tbl in tables_in_range:
                        report.append(
                            f"表格 {tbl['index']}: "
                            f"{tbl['rows']}行 × {tbl['cols']}列"
                        )
                        if tbl["header"]:
                            report.append(
                                f"  - 表头(第1行): "
                                f"{', '.join(tbl['header'])}"
                            )
                        report.append("---------------------")

            # ── 统计汇总 ──
            report.append("")
            total_checked = issue_severe + issue_minor + issue_ok
            report.append(
                f"[统计] 共检查 {total_checked} 个非空段落"
            )
            if issue_severe > 0:
                report.append(f"  🔴 格式问题: {issue_severe} 处")
            if issue_minor > 0:
                report.append(f"  🟡 轻微问题: {issue_minor} 处")
            report.append(f"  🟢 正常: {issue_ok} 处")

            # 分页提示
            if end_para < total_paras:
                report.append(
                    f"\n💡 提示: 还有 {total_paras - end_para} 段未检查。"
                    f"调用 inspect_document_format("
                    f"start_para={end_para + 1}) 查看下一页。"
                )

            if opened_by_us:
                doc.Close(SaveChanges=0)

            return "\n".join(report)

        except Exception as e:
            return f"格式检查出错: {e}"

    # _merge_format_rules() removed: Tool-Skill separation architecture.

    # format_rules is now fully provided by Skill config.

    # Multi-Skill merging is handled by SkillManager.get_active_config().

