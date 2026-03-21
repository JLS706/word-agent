# -*- coding: utf-8 -*-
"""
Word 参考文献自动化排版与交叉引用生成脚本 (合并完美版 v5)
功能一：格式修复与排版（阶段A）
功能二：自动生成文献交叉引用（阶段B）
功能三：自动生成图注交叉引用（阶段C）
功能四：手写图注转 Word 题注（阶段D，在C之前执行）
支持通过图形界面选择任意执行阶段组合，处理完毕后可继续操作或退出。
"""

import win32com.client
import os
import re
import traceback
import hashlib
import sys

# ==============================================================================
# 第一部分：全局常量与工具函数
# ==============================================================================

FIG_CAPTION_PATTERN = re.compile(
    r'^\s*((?:图|Fig\.?|Figure)\s*\d+(?:[\.\-]\s*(?:\d+|[A-Za-z]+))?\s*(?:\([a-zA-Z]\))?)',
    re.IGNORECASE
)

# 匹配手写图注，提取章节号、图号、子图后缀、描述文字
# 例: "图 1.3 系统模型框图" → ("图", "1", "3", None, "系统模型框图")
# 例: "图 1.1(a) 发射端" → ("图", "1", "1", "(a)", "发射端")
# 例: "图 1.A ISAC系统" → ("图", "1", "A", None, "ISAC系统") ← 草稿占位符
FIG_HANDWRITTEN_PATTERN = re.compile(
    r'^\s*(图|Fig\.?|Figure)\s*(\d+)[.\-](\d+|[A-Za-z]+)\s*(\([a-zA-Z]\))?\s*(.*)',
    re.IGNORECASE
)

# 匹配缩写词：2个及以上大写字母（允许带数字，如 5G, 3GPP）
# 注意：不能使用 \b，因为 \b 在中文文本中不认中文字符边界，
# 导致 "采用MIMO技术" 中的 MIMO 无法被匹配。
# 改用 lookaround 断言：前后不是 ASCII 字母/数字即可视为边界。
# 分支1: 纯大写缩写 (ISAC, RCS, MIMO)
# 分支2: 混合大小写缩写 (LoS, NLoS, IoT, QoS)
ACRONYM_PATTERN = re.compile(
    r'(?<![A-Za-z0-9])'
    r'(?:'
    r'[A-Z][A-Z0-9]{1,}[A-Z]*'              # 纯大写: ISAC, RCS
    r'|'
    r'[A-Z][a-z]{0,2}[A-Z][a-z]?[A-Z]?'     # 混合: LoS, NLoS, IoT, QoS
    r')'
    r'(?![A-Za-z0-9])'
)

# 受保护的专有名词 / 缩写——不会被大小写规则改动
PROTECTED_WORDS = [
    "ISAC", "MIMO", "UAV", "QGNN", "DNN", "IEEE", "RIS", "ADMM", "GSM",
    "STAP", "SCNR", "SINR", "OFDM", "FMCW", "SNR", "LSSDNet", "AiDT",
    "mmWave", "3D", "4D", "6G", "TBD", "NOMA", "RSMA", "DFRC", "SISO",
    "MISO", "NLOS", "LOS", "AI", "ML", "DL", "GNN", "CNN", "RNN", "LSTM",
    "IoT", "QoS", "BER", "CRB", "DoA", "DoF", "RF", "IF", "AP", "BS",
    "UE", "CSI", "SIC", "AWGN", "PAPR", "EE", "SE",
    "FPGA", "DSP", "GPU", "CPU", "SoC", "PCB", "ASIC", "LTE", "NR", "5G",
    "FFT", "PSD", "ADC", "DAC", "CDMA", "TDMA", "SDMA", "HARQ", "ARQ",
]

_PROTECTED_UPPER_MAP = {p.upper(): p for p in PROTECTED_WORDS}

LOWERCASE_WORDS = {
    'a', 'an', 'the', 'and', 'but', 'or', 'for', 'nor',
    'on', 'at', 'to', 'from', 'by', 'of', 'in', 'with',
    'as', 'vs', 'via',
}

# ==============================================================================
# GUI 主控面板
# ==============================================================================

def show_main_dialog():
    """
    显示主控制面板 GUI（深色风格）。
    返回 (file_path, modify_in_place, stages, action)
      stages: {'A': bool, 'B': bool, 'C': bool}
      action: 'run' 或 'quit'
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        print("需要 tkinter 模块，请确认 Python 安装时勾选了 tcl/tk 选项")
        return None, False, {}, 'quit'

    result = {
        'action': 'quit', 'file': '',
        'in_place': False, 'stage_d': False,
        'stage_a': True, 'stage_b': True, 'stage_c': True,
    }

    root = tk.Tk()
    root.title("Word 文献自动化精灵")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    BG       = "#1e1e2e"
    CARD     = "#2a2a3e"
    ACCENT   = "#7c6af7"
    ACCENT_H = "#9b8dff"
    TEXT     = "#cdd6f4"
    MUTED    = "#a6adc8"
    SUCCESS  = "#a6e3a1"
    WARN     = "#f38ba8"

    root.configure(bg=BG)

    def lbl(parent, text, size=10, bold=False, color=TEXT, **kw):
        weight = "bold" if bold else "normal"
        return tk.Label(parent, text=text,
                        font=("Microsoft YaHei", size, weight),
                        bg=parent.cget('bg'), fg=color, **kw)

    def card_frame(parent):
        return tk.Frame(parent, bg=CARD, bd=0)

    # ── 标题栏 ─────────────────────────────────────────────
    header = tk.Frame(root, bg=ACCENT, pady=10)
    header.pack(fill='x')
    lbl(header, "  Word 文献自动化精灵", size=13, bold=True, color="white").pack(anchor='w', padx=16)
    lbl(header, "  格式修复 · 自动编号 · 文献引用 · 图注引用", size=9, color="#ddd6fe").pack(anchor='w', padx=16)

    body = tk.Frame(root, bg=BG, padx=20, pady=12)
    body.pack(fill='both', expand=True)

    # ── 文件选择 ──────────────────────────────────────────
    fc = card_frame(body)
    fc.pack(fill='x', pady=(0, 10))
    lbl(fc, " 目标文件", size=10, bold=True).pack(anchor='w', padx=12, pady=(10, 4))

    file_var = tk.StringVar(value="（尚未选择文件）")
    file_label = tk.Label(fc, textvariable=file_var,
                          font=("Microsoft YaHei", 9),
                          bg=CARD, fg=MUTED, wraplength=380, justify='left')
    file_label.pack(anchor='w', padx=12)

    def browse():
        p = filedialog.askopenfilename(
            title="选择要处理的 Word 文档",
            filetypes=[("Word 文档", "*.docx;*.doc"), ("所有文件", "*.*")],
            parent=root,
        )
        if p:
            result['file'] = p
            file_var.set("已选择: " + os.path.basename(p))
            file_label.config(fg=SUCCESS)

    tk.Button(fc, text="浏览...", command=browse,
              font=("Microsoft YaHei", 9), bg=ACCENT, fg="white",
              activebackground=ACCENT_H, activeforeground="white",
              relief='flat', bd=0, padx=14, pady=5, cursor='hand2'
              ).pack(anchor='w', padx=12, pady=(6, 10))

    # ── 保存策略 ─────────────────────────────────────────
    sc = card_frame(body)
    sc.pack(fill='x', pady=(0, 10))
    lbl(sc, " 保存策略", size=10, bold=True).pack(anchor='w', padx=12, pady=(10, 6))

    in_place_var = tk.BooleanVar(value=False)
    for val, txt, color in [
        (False, "另存副本（文件名加 _processed 后缀）", SUCCESS),
        (True,  "直接覆盖原文件（请先手动备份！）",    WARN),
    ]:
        tk.Radiobutton(sc, text=txt, variable=in_place_var, value=val,
                       font=("Microsoft YaHei", 9),
                       bg=CARD, fg=color, selectcolor=CARD,
                       activebackground=CARD, activeforeground=color,
                       bd=0, cursor='hand2').pack(anchor='w', padx=16, pady=2)
    tk.Frame(sc, bg=CARD, height=8).pack()

    # ── 阶段选择 ──────────────────────────────────────────
    stc = card_frame(body)
    stc.pack(fill='x', pady=(0, 10))
    lbl(stc, " 选择执行阶段（可多选）", size=10, bold=True).pack(anchor='w', padx=12, pady=(10, 6))

    stages_info = [
        ('stage_a', True,  "阶段 A：参考文献格式修复与排版",       "统一字体字号、Sentence Case 处理、期刊名斜体"),
        ('stage_d', False, "阶段 D：手写图注转 Word 题注",        "将手写图注转为 Word SEQ 域代码，支持自动编号"),
        ('stage_b', True,  "阶段 B：参考文献 [数字] 交叉引用生成", "将正文 [1][2] 替换为可跳转域代码"),
        ('stage_c', True,  "阶段 C：图注交叉引用生成",            "将正文图注引用替换为可跳转域代码"),
        # --- 下面是新增的一行 ---
        ('stage_e', True,  "阶段 E：缩写首次出现定义检测",        "检测专业缩写在第一次出现时是否写了全称"),
    ]
    stage_vars = {}
    for key, default, title, desc in stages_info:
        var = tk.BooleanVar(value=default)
        stage_vars[key] = var
        row = tk.Frame(stc, bg=CARD, cursor='hand2')
        row.pack(fill='x', padx=12, pady=2)

        # 用 ● / ○ 模拟与 Radiobutton 一致的圆点样式
        dot = tk.Label(row, text="●" if default else "○",
                       font=("Microsoft YaHei", 12),
                       bg=CARD, fg=SUCCESS if default else MUTED,
                       cursor='hand2')
        dot.pack(side='left', padx=(4, 2))

        info_f = tk.Frame(row, bg=CARD, cursor='hand2')
        info_f.pack(side='left', anchor='w')
        title_lbl = lbl(info_f, title, size=9, bold=True)
        title_lbl.pack(anchor='w')
        desc_lbl = lbl(info_f, desc, size=8, color=MUTED)
        desc_lbl.pack(anchor='w')

        # 点击切换逻辑（闭包捕获变量）
        def make_toggle(v, d):
            def toggle(event=None):
                v.set(not v.get())
                d.config(text="●" if v.get() else "○",
                         fg=SUCCESS if v.get() else MUTED)
            return toggle
        toggle_fn = make_toggle(var, dot)
        for widget in (row, dot, info_f, title_lbl, desc_lbl):
            widget.bind("<Button-1>", toggle_fn)
    tk.Frame(stc, bg=CARD, height=8).pack()

    # ── 按钮栏 ────────────────────────────────────────────
    btn_bar = tk.Frame(root, bg=BG, pady=12)
    btn_bar.pack(fill='x', padx=20)

    def on_run():
        import tkinter.messagebox as mb
        if not result['file']:
            mb.showwarning("未选择文件", "请先点击「浏览...」选择一个 Word 文档。", parent=root)
            return
        if not any(stage_vars[k].get() for k in stage_vars):
            mb.showwarning("未选择阶段", "请至少勾选一个执行阶段。", parent=root)
            return
        result['in_place'] = in_place_var.get()
        for key, _, _, _ in stages_info:
            result[key] = stage_vars[key].get()
        result['action'] = 'run'
        root.destroy()

    def on_quit():
        result['action'] = 'quit'
        root.destroy()

    btn_kw = dict(font=("Microsoft YaHei", 10, "bold"),
                  relief='flat', bd=0, padx=20, pady=8, cursor='hand2')
    tk.Button(btn_bar, text="▶  开始处理", command=on_run,
              bg=ACCENT, fg="white",
              activebackground=ACCENT_H, activeforeground="white",
              **btn_kw).pack(side='left', padx=(0, 10))
    tk.Button(btn_bar, text="×  退出程序", command=on_quit,
              bg="#313244", fg=WARN,
              activebackground="#45475a", activeforeground=WARN,
              **btn_kw).pack(side='left')

    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    root.protocol("WM_DELETE_WINDOW", on_quit)
    root.mainloop()

    stages = {
        'A': result['stage_a'], 'B': result['stage_b'],
        'C': result['stage_c'], 'D': result['stage_d'],
        'E': result.get('stage_e', False), # 新增
    }
    return result['file'], result['in_place'], stages, result['action']


# ==============================================================================
# 文本处理工具函数
# ==============================================================================

def _protect_word(word):
    """检查单个 word 中是否包含受保护的缩写。"""
    parts = word.split('-')
    result_parts = []
    for part in parts:
        prefix_punct = ""
        suffix_punct = ""
        core = part

        m_prefix = re.match(r'^([^\w\u4e00-\u9fff]*)', core)
        if m_prefix:
            prefix_punct = m_prefix.group(1)
            core = core[len(prefix_punct):]

        m_suffix = re.search(r'([^\w\u4e00-\u9fff]*)$', core)
        if m_suffix:
            suffix_punct = m_suffix.group(1)
            core = core[:len(core) - len(suffix_punct)]

        upper_core = core.upper()
        if upper_core in _PROTECTED_UPPER_MAP:
            result_parts.append(prefix_punct + _PROTECTED_UPPER_MAP[upper_core] + suffix_punct)
        else:
            result_parts.append(part)

    return '-'.join(result_parts)


def _is_protected(word):
    parts = word.split('-')
    for part in parts:
        core = re.sub(r'[^\w\u4e00-\u9fff]', '', part)
        if core.upper() in _PROTECTED_UPPER_MAP:
            return True
    return False


def to_sentence_case(title):
    """将英文标题转为 Sentence case，冒号后的首词也大写。"""
    words = title.strip().split()
    if not words:
        return ""
    result = []
    capitalize_next = True
    for w in words:
        if _is_protected(w):
            result.append(_protect_word(w))
        elif capitalize_next:
            result.append(w.capitalize())
        else:
            result.append(w.lower())
        capitalize_next = w.endswith(':')
    return " ".join(result)


_REF_TYPE_PATTERN = re.compile(
    r'^'
    r'((?:\[\d+\][ \t]*)?)'
    r'(.*?)'
    r'([ \t]*\.[ \t]*)'
    r'(.*?)'
    r'([ \t]*)'
    r'(\[[A-Z]{1,2}(?:/[A-Z]{1,2})?\])'
    r'(.*)$',
    re.IGNORECASE | re.DOTALL
)

_REF_TYPE_FALLBACK = re.compile(
    r'(\[[A-Z]{1,2}(?:/[A-Z]{1,2})?\])',
    re.IGNORECASE
)

_STOP_KEYWORDS = ["致谢", "附录", "基金项目", "作者简介", "Biography",
                   "Acknowledgment", "Appendix", "Foundation"]

# 阶段E检测缩写定义时，遇到这些关键词所在段落则停止扫描（它们标志着正文结束）
_ACRONYM_STOP_KEYWORDS = [
    "参考文献", "致谢", "附录", "基金项目", "作者简介",
    "项目负责人", "主要参与者", "研究基础", "工作条件",
    "代表性成果", "经费预算", "研究经历", "承担科研项目",
    "发表论文", "代表性论文", "主要论著", "获奖情况",
    "Biography", "Acknowledgment", "Appendix", "Foundation",
    "References", "REFERENCES",
]

# 匹配参考文献中类型标签后面的期刊名/会议名
# 例: [J]. IEEE Trans. Signal Processing, 2023
#     [C]// Proc. IEEE ICASSP. 2023
#     [J]. 电子学报, 2023
_JOURNAL_AFTER_TAG = re.compile(
    r'[\[【]'
    r'[A-Z]{1,2}(?:/[A-Z]{1,2})?'
    r'[\]】]'
    r'[./：:]*\s*(?://\s*)?'
    r'(.+?)'
    r'(?:,\s*\d{4}|，\s*\d{4}|\.\s*\d{4}|,\s*vol|,\s*Vol|$)',
    re.IGNORECASE
)


def _contains_chinese(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s))


def clean_and_format_gb7714(text):
    text = text.strip()
    match = _REF_TYPE_PATTERN.match(text)
    if match:
        prefix, authors, author_sep, title, title_space, tag, suffix = match.groups()
        if not title.strip():
            new_title = title
        elif _contains_chinese(title):
            new_title = title
        else:
            left_space = title[:len(title) - len(title.lstrip())]
            right_space = title[len(title.rstrip()):]
            core_title = title.strip()
            new_core = to_sentence_case(core_title)
            new_title = f"{left_space}{new_core}{right_space}"
        return f"{prefix}{authors}{author_sep}{new_title}{title_space}{tag}{suffix}"

    type_match = _REF_TYPE_FALLBACK.search(text)
    if type_match:
        tag = type_match.group(1)
        tag_start = type_match.start()
        after_tag = text[tag_start:]
        before_tag = text[:tag_start]
        dot_pos = before_tag.rfind('.')
        if dot_pos > 0:
            authors_part = before_tag[:dot_pos + 1]
            title_part = before_tag[dot_pos + 1:]
            left_space = title_part[:len(title_part) - len(title_part.lstrip())]
            right_space = title_part[len(title_part.rstrip()):]
            core_title = title_part.strip()
            if not core_title or _contains_chinese(core_title):
                new_title = title_part
            else:
                new_core = to_sentence_case(core_title)
                new_title = f"{left_space}{new_core}{right_space}"
            return f"{authors_part}{new_title}{after_tag}"

    return text


def _get_paragraph_text_safe(para):
    rng = para.Range
    text = rng.Text
    if text.endswith('\r'):
        text = text[:-1]
    
    # 清理域代码特殊字符：\x13(域开始) \x14(域分隔) \x15(域结束)
    # 保留域结果(\x14与\x15之间的部分)，去除域代码指令(\x13与\x14之间的部分)
    text = re.sub(r'\x13[^\x14\x15]*\x14([^\x15]*)\x15', r'\1', text)
    # 清除没有结果的残余域(如 \x13...\x15 之间没有\x14)
    text = re.sub(r'\x13[^\x15]*\x15', '', text)
    
    return text.strip()


def _is_document_open(word, filepath):
    abs_path = os.path.abspath(filepath).lower()
    try:
        for d in word.Documents:
            if os.path.abspath(d.FullName).lower() == abs_path:
                return d
    except Exception:
        pass
    return None


def _is_ref_start(para):
    try:
        list_str = para.Range.ListFormat.ListString
        if list_str and list_str.strip() and re.search(r'[0-9a-zA-Z]', list_str):
            return True
    except Exception:
        pass
    text = _get_paragraph_text_safe(para)
    if re.match(r'^([\[【]\d+[\]】]|\d+\.)', text.strip()):
        return True
    return False


def _is_stop_section(text):
    text_stripped = text.strip()
    if not text_stripped:
        return False
    if len(text_stripped) < 50:
        for kw in _STOP_KEYWORDS:
            if kw in text_stripped:
                return True
    return False


def _get_word_file_format(ext):
    ext_lower = ext.lower()
    if ext_lower == '.docx':
        return 16
    elif ext_lower == '.doc':
        return 0
    else:
        return 16


def _extract_ref_num_from_para(para, text):
    """
    从参考文献段落中提取编号（纯整数）。
    优先从 Word 列表格式取：
      - 纯数字（如 "1"）或数字+标点（如 "1." "[1]"）: 采信
      - 含字母（如 "R1"）: 跳过
    否则 fallback 到文本正则匹配。
    """
    try:
        list_str = para.Range.ListFormat.ListString
        if list_str:
            clean = list_str.strip()
            m = re.fullmatch(r'(\d+)[.\u3001\s]*', clean)
            if m:
                return m.group(1)
            m = re.fullmatch(r'[\[\u3010](\d+)[\]\u3011][.\u3001\s]*', clean)
            if m:
                return m.group(1)
    except Exception:
        pass

    m = re.match(r'^[\[\u3010](\d+)[\]\u3011]', text.strip())
    if m:
        return m.group(1)
    m = re.match(r'^(\d+)[.\u3001\s]', text.strip())
    if m:
        return m.group(1)

    return None


def _make_ref_bookmark_name(text):
    """根据参考文献内容生成稳定的书签名（不依赖编号）。
    同一条文献无论排在第几，书签名始终一致，避免重排后引用错位。
    """
    # 去掉开头的编号前缀（如 [1]、【1】、1. 等）
    clean = re.sub(r'^[\[\u3010]?\d+[\]\u3011]?[.\u3001]?\s*', '', text.strip())
    # 取前60个字符作为稳定特征（通常包含作者名和标题开头）
    clean = clean[:60].strip()
    if not clean:
        clean = text[:60]
    h = hashlib.md5(clean.encode('utf-8', errors='ignore')).hexdigest()[:10]
    return f"ARef_{h}"


# ==============================================================================
# 核心处理函数
# ==============================================================================

def check_acronym_definitions(doc):
    """检测缩写词在第一次出现时是否定义了全称（增强版）"""
    print("\n🔍 [阶段E] 正在检测专业术语缩写定义...")
    
    seen_acronyms = {} 
    issues = []
    prev_text = ""  # 保留前一段文本，有时定义跨段落
    body_ended = False  # 一旦遇到停止关键词，后续全部跳过

    for i in range(1, doc.Paragraphs.Count + 1):
        try:
            para = doc.Paragraphs(i)
            text = _get_paragraph_text_safe(para)
            
            if not text or len(text) < 3:
                continue
            
            stripped = text.strip()
            
            # 正文结束检测：遇到"参考文献""项目负责人"等标志性段落后，
            # 直接停止扫描，因为后续内容（文献列表、简历、专利）不需要缩写定义
            if not body_ended:
                if len(stripped) < 60:
                    for kw in _ACRONYM_STOP_KEYWORDS:
                        if kw in stripped:
                            body_ended = True
                            print(f"   📍 在第 {i} 段遇到 \"{kw}\"，停止缩写扫描")
                            break
            
            if body_ended:
                continue
            
            # 跳过很短的粗体标题行（章节标题）
            try:
                if len(text) < 30 and para.Range.Font.Bold:
                    prev_text = text
                    continue
            except Exception:
                pass

            # 3. 统一括号：将中文全角括号替换为英文半角，避免匹配遗漏
            text_norm = text.replace('\uFF08', '(').replace('\uFF09', ')')
            
            # 搜索当前段落中的缩写（在标准化文本上匹配）
            for match in ACRONYM_PATTERN.finditer(text_norm):
                acronym = match.group()
                
                # 排除太短的匹配（2个字符太容易误报，如 UE, ID, IP, CL, US）
                if len(acronym) < 3:
                    continue
                # 排除纯数字开头且只有数字+一个字母的情况（如 "5G"、"3D"）
                if re.fullmatch(r'\d+[A-Z]', acronym):
                    continue
                # 排除字母+长数字串：专利号、课题号等（如 ZL202510490604, MCM201302311）
                if re.fullmatch(r'[A-Z]{1,4}\d{4,}', acronym):
                    continue
                # 排除单字母+短数字的模型/变量名后缀（如 R15, R5, P720, B2, Q1）
                if re.fullmatch(r'[A-Z]\d+', acronym):
                    continue
                
                if acronym not in seen_acronyms:
                    start, end = match.span()
                    is_defined = False
                    
                    # 处理连字符复合缩写（如 "3D-GDFT"）：
                    # 将检测起点扩展到连字符前缀，这样括号检测范围能覆盖整个复合词
                    effective_start = start
                    if start > 0 and text_norm[start-1] == '-':
                        # 向前跳过连字符前缀（如 "3D-" → effective_start 指向 "3"）
                        prefix_start = start - 1
                        while prefix_start > 0 and text_norm[prefix_start-1] not in ' \t\r\n(，,':
                            prefix_start -= 1
                        effective_start = prefix_start
                    
                    # --- 核心检测逻辑：判断缩写是否伴随"全称定义" ---
                    # 英文缩写的全称定义必须包含英文全称（如 Integrated Sensing）
                    # 仅有中文译名（如"通感""雷达散射截面"）不算。
                    
                    def _is_fullname(s):
                        """判断字符串是否包含英文缩写的全称定义。
                        全称需要至少2个含小写字母的英文单词
                        （如 Integrated Sensing, Radar Cross Section）。
                        纯中文、纯数字、纯大写缩写堆叠都不算。"""
                        s = s.strip().rstrip('，,、;；')
                        if not s or len(s) < 2:
                            return False
                        # 纯数字/标点 → 不是全称
                        if re.fullmatch(r'[\d\s\.\-,]+', s):
                            return False
                        # 关键判据：至少2个含小写字母 且≥4字符 的英文单词
                        # 排除单位缩写如 km, dB, Hz, ms, GHz
                        # 排除纯大写缩写堆叠如 MIMO, OFDM
                        # 排除纯中文如 "通感""雷达散射截面"
                        eng_words = re.findall(r'[a-zA-Z]{4,}', s)
                        lowercase_words = [w for w in eng_words if not w.isupper()]
                        if len(lowercase_words) >= 2:
                            return True
                        return False
                    
                    # 模式A: "全称 (ABBR)" — 缩写本身被括号包裹
                    #   还需检查括号前面是否有像样的全称文本
                    before_context = text_norm[max(0, effective_start-3):effective_start]
                    after_context = text_norm[end:min(len(text_norm), end+3)]
                    if re.search(r'[(]\s*$', before_context) and re.search(r'^\s*[)]', after_context):
                        # 缩写在括号内 → 检查括号前面的文本是否有英文全称
                        pre_paren = text_norm[max(0, effective_start-60):max(0, effective_start-1)]
                        # 在句子边界处截断，只看当前从句（防止远处的英文词干扰）
                        boundary = re.search(r'[。；！？\.\!\?]', pre_paren)
                        if boundary:
                            pre_paren = pre_paren[boundary.end():]
                        if _is_fullname(pre_paren):
                            is_defined = True
                    
                    # 模式B: "ABBR（全称）" — 缩写后面紧跟括号内的全称
                    if not is_defined:
                        after_wide = text_norm[end:min(len(text_norm), end+120)]
                        m_paren = re.match(r'\s*[(]([^)]+)[)]', after_wide)
                        if m_paren:
                            paren_content = m_paren.group(1)
                            if _is_fullname(paren_content):
                                is_defined = True
                    
                    # 模式C: "（全称，ABBR）" — 缩写在括号内部，和全称一起
                    if not is_defined:
                        wide_before = text_norm[max(0, effective_start-100):effective_start]
                        wide_after = text_norm[end:min(len(text_norm), end+10)]
                        m_open = re.search(r'[(]([^)]{0,90})$', wide_before)
                        m_close = re.search(r'^([^(]{0,8})[)]', wide_after)
                        if m_open and m_close:
                            # 括号内、缩写前面的那段文本应当包含全称
                            inner_text = m_open.group(1)
                            if _is_fullname(inner_text):
                                is_defined = True
                    
                    # 模式D: 前一段落中定义了这个缩写（跨段落定义）
                    if not is_defined and prev_text:
                        # 在前一段中查找 "全称（ABBR）" 或 "ABBR（全称）" 的实际定义
                        d_pattern = re.search(
                            r'[(][^)]*' + re.escape(acronym) + r'[^)]*[)]',
                            prev_text
                        )
                        if d_pattern:
                            paren_full = d_pattern.group()
                            inner = paren_full[1:-1]  # 去掉括号本身
                            remainder = inner.replace(acronym, '').strip(' ,，、')
                            if _is_fullname(remainder):
                                is_defined = True
                    
                    # 构建显示名称：如果有连字符前缀，用完整形式（如 "3D-GDFT"）
                    display_name = text_norm[effective_start:end] if effective_start < start else acronym
                    
                    # 记录第一次出现的状态（用完整形式作为 key）
                    seen_acronyms[acronym] = is_defined
                    
                    if not is_defined:
                        # 改进显示逻辑：把 context 聚焦在缩写周围
                        display_start = max(0, effective_start - 30)
                        display_end = min(len(text), end + 30)
                        snippet = text[display_start:display_end].replace('\r', '').replace('\n', '')
                        
                        issues.append({
                            'acro': display_name,
                            'para': i,
                            'context': f"...{snippet}..."
                        })
            
            prev_text = text_norm
        except Exception:
            continue

    # 5. 输出报告
    if issues:
        print(f"\n" + "!"*50)
        print(f"⚠️  检测到以下缩写在首次出现时【可能】未包含在括号定义中：")
        print(f"   (如果该词是公认常识，可忽略或将其加入脚本的 STOP_WORDS 列表)")
        print("-" * 50)
        for item in issues:
            print(f"  • [第 {item['para']:3} 段] 缩写: {item['acro']:8}")
            print(f"    上下文: {item['context']}")
            print("-" * 50)
        print(f"共发现 {len(issues)} 处潜在问题。")
        print("!"*50 + "\n")
    else:
        print("   ✅ 缩写定义检测通过！未发现明显缺失。")

def process_document(input_file, modify_in_place=False, stages=None):
    """整合所有阶段的运行逻辑，stages 字典控制哪些阶段执行。"""
    if stages is None:
        stages = {'A': True, 'B': True, 'C': True, 'D': False}

    abs_input = os.path.abspath(input_file)
    if not os.path.exists(abs_input):
        print(f"❌ 找不到文件: {abs_input}")
        return

    base, ext = os.path.splitext(abs_input)
    file_format = _get_word_file_format(ext)

    if modify_in_place:
        output_file = abs_input
        print(f"🚀 启动自动化引擎，处理文件并【直接覆盖原文档】: {os.path.basename(abs_input)}")
    else:
        output_file = os.path.abspath(f"{base}_processed{ext}")
        print(f"🚀 启动自动化引擎，处理文件并【另存副本】: {os.path.basename(abs_input)}")

    word = None
    doc = None
    was_already_open = False

    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = True
        
        # 强制关闭域代码显示，否则 Find.Execute 可能无法跨域匹配文本
        try:
            word.ActiveWindow.View.ShowFieldCodes = False
        except Exception:
            pass

        existing_doc = _is_document_open(word, abs_input)
        if existing_doc:
            doc = existing_doc
            was_already_open = True
        else:
            doc = word.Documents.Open(abs_input)

        # =============================================================
        # 阶段 A：参考文献格式排版与修正
        # =============================================================
        if not stages.get('A', True):
            print("\n⏭️ [阶段A] 已跳过（用户未勾选）")
        else:
            print("\n🔧 [阶段A] 正在执行参考文献格式修复与排版...")
            total_paras = doc.Paragraphs.Count
            in_references_section = False
            ref_para_indices = []
            all_paras_cache = []

            for i in range(1, total_paras + 1):
                para = doc.Paragraphs(i)
                text = _get_paragraph_text_safe(para)
                all_paras_cache.append((i, para, text))

                if not in_references_section:
                    if "参考文献" in text and len(text) < 50:
                        in_references_section = True
                    continue

                if _is_stop_section(text):
                    break

                if text:
                    ref_para_indices.append(len(all_paras_cache) - 1)

            ref_entries = []
            if ref_para_indices:
                for cache_idx in ref_para_indices:
                    idx, para, text = all_paras_cache[cache_idx]
                    if _is_ref_start(para):
                        list_str = ""
                        try:
                            list_str = para.Range.ListFormat.ListString or ""
                        except Exception:
                            pass
                        full_text = (list_str + " " + text).strip() if list_str else text
                        ref_entries.append({'para_indices': [cache_idx], 'full_text': full_text})
                    else:
                        if ref_entries:
                            ref_entries[-1]['para_indices'].append(cache_idx)
                            ref_entries[-1]['full_text'] += " " + text
                        else:
                            ref_entries.append({'para_indices': [cache_idx], 'full_text': text})

                count = 0
                for entry in ref_entries:
                    full_text = entry['full_text']
                    first_para = all_paras_cache[entry['para_indices'][0]][1]
                    
                    # 取消严格标签限制，允许只有数字编号即可处理
                    if not _is_ref_start(first_para) and not re.search(r'[\[【][A-Z]{1,4}(?:/[A-Z]{1,2})?[\]】]', full_text):
                        continue
                        
                    count += 1
                    for cache_idx in entry['para_indices']:
                        _, para, _ = all_paras_cache[cache_idx]
                        try:
                            full_rng = para.Range
                            full_rng.Font.Size = 10.5
                            full_rng.Font.NameFarEast = "宋体"
                            full_rng.Font.Name = "Times New Roman"
                        except Exception:
                            pass

                    if len(entry['para_indices']) == 1:
                        cache_idx = entry['para_indices'][0]
                        _, para, original_text = all_paras_cache[cache_idx]
                        new_text = clean_and_format_gb7714(original_text)
                        if new_text != original_text:
                            try:
                                rng = para.Range
                                if rng.Text.endswith('\r'):
                                    rng.End = rng.End - 1
                                rng.Text = new_text
                            except Exception:
                                pass

                    # ── 期刊名/会议名 斜体处理 ──
                    for cache_idx in entry['para_indices']:
                        _, para, _ = all_paras_cache[cache_idx]
                        try:
                            para_rng = para.Range
                            para_text = para_rng.Text
                            if para_text.endswith('\r'):
                                para_text = para_text[:-1]
                            jm = _JOURNAL_AFTER_TAG.search(para_text)
                            if jm:
                                journal_name = jm.group(1).strip()
                                if journal_name:
                                    # 在段落 Range 中定位期刊名并设置斜体
                                    j_start = para_text.find(journal_name, jm.start())
                                    if j_start >= 0:
                                        italic_rng = para.Range.Duplicate
                                        italic_rng.SetRange(
                                            para_rng.Start + j_start,
                                            para_rng.Start + j_start + len(journal_name)
                                        )
                                        italic_rng.Font.Italic = True
                        except Exception:
                            pass

                print(f"   ✅ 格式修复完毕 (处理 {count} 条)")
            else:
                print("   ⚠️ 未在文档中发现可格式化的参考文献段落。")

        # =============================================================
        # 阶段 D：手写图注转 Word 题注 —— 必须在阶段C之前执行
        # =============================================================
        if not stages.get('D', False):
            draft_fig_map = {}  # 即使跳过D，也初始化空映射供C使用
            print("\n⏭️ [阶段D] 已跳过（用户未勾选）")
        else:
            # 草稿图号映射表（跨阶段共享：D→C）
            draft_fig_map = {}

            print("\n🏷️ [阶段D] 正在将手写图注转换为 Word 题注...")

            # ── 步骤1：扫描所有段落，识别手写图注 ──
            figure_entries = []
            total_paras_d = doc.Paragraphs.Count
            for i in range(1, total_paras_d + 1):
                para = doc.Paragraphs(i)
                text = _get_paragraph_text_safe(para)
                fig_match = FIG_HANDWRITTEN_PATTERN.match(text)
                if fig_match and len(text) <= 300:
                    # 排除非题注段落（正文中提到的"图X.Y"等引用）
                    try:
                        alignment = para.Format.Alignment
                        # wdAlignParagraphCenter = 1
                        if alignment != 1:
                            continue
                    except Exception:
                        pass
                    
                    if len(text) > 80:
                        continue

                    # 安全检查：跳过已含 SEQ 域代码的段落（避免重复转换）
                    has_seq = False
                    try:
                        for fld in para.Range.Fields:
                            if 'SEQ' in fld.Code.Text.upper():
                                has_seq = True
                                break
                    except Exception:
                        pass
                    if has_seq:
                        continue

                    prefix = fig_match.group(1)           # "图"
                    chapter_num = fig_match.group(2)      # "1"
                    fig_num = fig_match.group(3)          # "3"
                    subfig = fig_match.group(4) or ""     # "(a)" 或 ""
                    description = fig_match.group(5).strip()  # "系统模型框图"
                    figure_entries.append({
                        'para_index': i,
                        'prefix': prefix,
                        'chapter': chapter_num,
                        'fig_num': fig_num,
                        'subfig': subfig,
                        'description': description,
                    })

            if not figure_entries:
                print("   ⚠️ 未在文档中发现可转换的手写图注。")
            else:
                # ── 步骤2：为每个涉及的章节创建 Word 题注标签 ──
                chapters_used = sorted(set(fig['chapter'] for fig in figure_entries),
                                       key=int)
                print(f"   📍 发现 {len(figure_entries)} 个手写图注，"
                      f"涉及 {len(chapters_used)} 个章节")

                for ch in chapters_used:
                    label_name = f"图 {ch}."
                    try:
                        doc.CaptionLabels.Add(label_name)
                        print(f"   🏷️ 已创建题注标签: {label_name}")
                    except Exception:
                        print(f"   ℹ️  题注标签已存在: {label_name}")

                # ── 步骤3：按文档顺序逐条转换（保证 SEQ 编号正确）──
                converted = 0
                for fig in figure_entries:
                    try:
                        para = doc.Paragraphs(fig['para_index'])
                        rng = para.Range
                        alignment = para.Format.Alignment  # 保留居中等格式
                        
                        # 【关键修复】只有被打上底层“题注”样式(-35)的段落，
                        # 才会出现在 Word 的“交叉引用”手动插入弹窗列表中
                        try:
                            para.Style = -35
                        except Exception:
                            pass

                        # 去掉段落标记
                        if rng.Text.endswith('\r'):
                            rng.End = rng.End - 1

                        label_name = f"图 {fig['chapter']}."

                        # 清空段落内容，填入标签文本
                        # 【底层机制破解】Word 极度苛刻的底层要求：标题标签和 SEQ 域之间**必须有一个空格**！
                        # 否则该题注会被剔除出“交叉引用”弹窗的列表。
                        rng.Text = label_name + " "

                        # 在标签文本末尾紧跟插入 SEQ 域代码
                        seq_rng = doc.Range(rng.End, rng.End)
                        if fig['subfig']:
                            # 子图：使用 \c 重复上一个值，不递增
                            field_code = f'SEQ "图 {fig["chapter"]}." \\c'
                        else:
                            # 主图：正常递增
                            field_code = f'SEQ "图 {fig["chapter"]}."'
                        field = doc.Fields.Add(seq_rng, -1, field_code, False)

                        # 在域代码之后追加子图后缀和描述文字
                        after_text = ""
                        if fig['subfig']:
                            after_text += fig['subfig']      # "(a)"
                        if fig['description']:
                            after_text += " " + fig['description']  # " 描述"

                        if after_text:
                            # 获取段落的最新 Range，在段落标记之前插入
                            para_rng = para.Range
                            insert_pos = para_rng.End - 1  # 段落标记之前
                            end_rng = doc.Range(insert_pos, insert_pos)
                            end_rng.InsertBefore(after_text)

                        # 恢复格式
                        full_rng = para.Range
                        if full_rng.Text.endswith('\r'):
                            full_rng.End = full_rng.End - 1
                        full_rng.Font.NameFarEast = "宋体"
                        full_rng.Font.Name = "Times New Roman"
                        full_rng.Font.Size = 10.5
                        para.Format.Alignment = alignment

                        fig_display = (f"图 {fig['chapter']}."
                                       f"{fig['fig_num']}{fig['subfig']}")
                        print(f"   ✅ {fig_display} → Word 题注"
                              f" (标签: {label_name})")
                        converted += 1
                    except Exception as e:
                        print(f"   ⚠️ 转换失败 (段落{fig['para_index']}): {e}")

                # ── 步骤4：刷新所有域代码 ──
                print("   ⏳ 正在刷新所有域代码...")
                doc.Fields.Update()

                # ── 步骤5：构建草稿编号→正式编号映射表 ──
                # 读取转换后的题注，提取 SEQ 生成的新编号，
                # 建立 "1.A" → "1.2" 这样的映射，供阶段C使用。
                draft_fig_map = {}
                for fig in figure_entries:
                    old_core = f"{fig['chapter']}.{fig['fig_num']}"
                    # 如果 fig_num 本身就是纯数字且较小，可能不是草稿占位符，跳过
                    # 只对非纯数字或大编号（可能是占位符如99）建立映射
                    is_draft = not fig['fig_num'].isdigit()
                    try:
                        para = doc.Paragraphs(fig['para_index'])
                        new_text = _get_paragraph_text_safe(para)
                        new_match = FIG_CAPTION_PATTERN.match(new_text)
                        if new_match:
                            new_label = new_match.group(1)
                            new_core_m = re.search(
                                r'\d+(?:\s*[\.\-]\s*(?:\d+|[A-Za-z]+))?',
                                new_label
                            )
                            if new_core_m:
                                new_core = re.sub(r'\s+', '', new_core_m.group())
                                if old_core != new_core:
                                    draft_fig_map[old_core] = new_core
                                    is_draft = True
                        if is_draft and old_core not in draft_fig_map:
                            # 如果无法读取新编号，至少记录下来，后续尝试匹配
                            draft_fig_map[old_core] = None
                    except Exception:
                        if is_draft:
                            draft_fig_map[old_core] = None

                if draft_fig_map:
                    print(f"   📋 记录了 {len(draft_fig_map)} 个草稿编号待映射")
                    for old_c, new_c in draft_fig_map.items():
                        if new_c:
                            print(f"      {old_c} → {new_c}")

                print(f"   🎉 手写图注转换完成，共转换 {converted} 个题注")

        # =============================================================
        # 阶段 B：构建书签并生成文献交叉引用
        # =============================================================
        if not stages.get('B', True):
            print("\n⏭️ [阶段B] 已跳过（用户未勾选）")
        else:
            print("\n🔗 [阶段B] 正在执行动态交叉引用生成...")

            # ── 第1步：预扫描参考文献列表，计算所有有效的哈希书签名 ──
            # 在动任何域代码之前，先确定哪些 ARef_{hash} 是当前有效的。
            # 这样才能区分"论文还在(保留域)" vs "论文已删(清理域)"。
            boundary_rng = None
            for para in doc.Paragraphs:
                text = para.Range.Text.strip()
                if "参考文献" in text and len(text) < 50:
                    boundary_rng = para.Range
                    break

            if not boundary_rng:
                print("   ⚠️ 未找到【参考文献】红线，无法生成交叉引用，强制跳过。")
            else:
                # 预扫描：收集当前参考文献列表中所有论文的哈希书签名
                initial_boundary_pos = boundary_rng.Start
                valid_hashes = set()     # 当前有效的 ARef_{hash} 集合
                bookmark_map = {}        # "[N]" -> (bkmk_name, "\\n")
                ref_para_data = []       # (para, text, num, bkmk_name) 待创建书签

                for para in doc.Paragraphs:
                    if para.Range.Start >= initial_boundary_pos:
                        text = _get_paragraph_text_safe(para)
                        if not text or len(text.strip()) < 5:
                            continue
                        num = _extract_ref_num_from_para(para, text)
                        if num is None:
                            continue
                        bkmk_name = _make_ref_bookmark_name(text)
                        valid_hashes.add(bkmk_name)
                        target_text = f"[{num}]"
                        if target_text not in bookmark_map:
                            bookmark_map[target_text] = (bkmk_name, "\\n")
                            ref_para_data.append((para, text, num, bkmk_name))

                print(f"   📋 预扫描完毕：当前参考文献 {len(valid_hashes)} 条")

                # ── 第2步：智能清理旧域代码 ──
                # 核心策略：
                #   - Auto_Ref_N 域：一律 Unlink（旧命名，需要迁移）
                #   - ARef_{hash} 且 hash 在 valid_hashes 中：保留！
                #     它们通过 \n 开关在 Fields.Update() 时自动更新显示编号，
                #     这正是内容哈希书签的设计目的——论文增删后编号自动跟随。
                #   - ARef_{hash} 且 hash 不在 valid_hashes 中：Unlink（论文已删除）
                migrated_count = 0
                orphan_count = 0
                kept_count = 0
                try:
                    for fi in range(doc.Fields.Count, 0, -1):
                        try:
                            fld = doc.Fields(fi)
                            code_text = fld.Code.Text
                            m = re.search(r'(ARef_[a-f0-9]+|Auto_Ref_\d+)', code_text)
                            if not m:
                                continue
                            field_bkmk = m.group()

                            if field_bkmk.startswith('Auto_Ref_'):
                                # 旧命名：一律迁移
                                fld.Unlink()
                                migrated_count += 1
                            elif field_bkmk in valid_hashes:
                                # 哈希匹配：论文还在，保留域代码
                                kept_count += 1
                            else:
                                # 哈希不匹配：论文已被删除
                                fld.Unlink()
                                orphan_count += 1
                        except Exception:
                            continue
                except Exception:
                    pass

                if migrated_count:
                    print(f"   🔄 已迁移 {migrated_count} 个旧版 Auto_Ref 域为纯文本")
                if orphan_count:
                    print(f"   ⚠️ 清理了 {orphan_count} 个孤儿域（对应文献已被删除）")
                if kept_count:
                    print(f"   ✅ 保留了 {kept_count} 个有效的哈希引用域（编号将自动更新）")

                # ── 第3步：删除旧书签并重新创建 ──
                try:
                    for bi in range(doc.Bookmarks.Count, 0, -1):
                        try:
                            bkmk = doc.Bookmarks(bi)
                            if (bkmk.Name.startswith('ARef_') or
                                bkmk.Name.startswith('Auto_Ref_') or
                                bkmk.Name == 'Ref_Boundary_Marker'):
                                bkmk.Delete()
                        except Exception:
                            continue
                except Exception:
                    pass

                # 创建边界标记书签
                doc.Bookmarks.Add("Ref_Boundary_Marker", boundary_rng)

                # 创建参考文献书签
                for para, text, num, bkmk_name in ref_para_data:
                    target_text = f"[{num}]"
                    try:
                        doc.Bookmarks.Add(bkmk_name, para.Range)
                        print(f"   📌 登记书签: {target_text} -> {bkmk_name}")
                    except Exception as e:
                        print(f"   ⚠️ 添加书签失败 ({bkmk_name}): {e}")

                if bookmark_map:
                    print(f"   ✅ 提取 {len(bookmark_map)} 条书签，开始正文替换...")
                    
                    # ====== 核心修复：采用最简单直白的规则判断是否设为上标 ======
                    # 规则1：如果前面紧挨着“文献”两个字（如：文献[x]），绝对不上标。
                    # 规则2：如果后面跟着符号“-”、“~”、“,”等且带左括号（如：[25]-[28]），作为组合连续引用，不上标。
                    # 规则3：其他所有情况，全部上标。
                    def _check_superscript(match_start, match_end):
                        try:
                            # 1. 检查前面是不是“文献”
                            # 取前面3个字符（容忍中间可能有的1个空格）
                            before_chars = doc.Range(max(0, match_start - 3), match_start).Text
                            if before_chars and "文献" in before_chars:
                                return False
                            
                            # 1.5 检查前面是不是引用组合的后半部分 (如 "[25]-[28]" 中的 [28])
                            # 如果前面紧挨着 ]-、]~、]–、]— 等连接符，说明是复合引用的尾部，不上标
                            before_wide = doc.Range(max(0, match_start - 10), match_start).Text
                            if before_wide:
                                stripped_before = before_wide.rstrip(' \t\r\n')
                                if stripped_before:
                                    if re.search(r'[\]】\u3011][\-~～,，、\u2013\u2014]+$', stripped_before):
                                        return False
                                
                            # 2. 检查后面是不是引用组合的连接符 (如 "[25]-[28]")
                            after_chars = doc.Range(match_end, min(doc.Content.End, match_end + 10)).Text
                            if after_chars:
                                stripped_after = after_chars.lstrip(' \t\r\n')
                                if stripped_after:
                                    # 如果后面跟着连续的连接符再加上左括号，说明它是引用组合的前半部分，不设上标
                                    # 涵盖 Word 自动转换的 En-dash (\u2013) 和 Em-dash (\u2014)
                                    if re.match(r'^[\-~～,，、\u2013\u2014]+[\[【\u3010]', stripped_after):
                                        return False
                            
                            # 3. 其余所有情况，均上标
                            return True
                        except Exception:
                            return True

                    # ====== 新增：提取并处理复合交叉引用（如 [13-15], [1, 2]） ======
                    compound_targets = set()
                    for para in doc.Paragraphs:
                        text = para.Range.Text
                        # 匹配类似 [13-15], 【1, 2】, [1,2,3-5]
                        # 注意：Word 会将 - 自动转换为 en-dash(\u2013) 或 em-dash(\u2014)
                        for match in re.finditer(r'[\[【]\s*(\d+(?:\s*[,，\-~～\u2013\u2014]\s*\d+)+)\s*[\]】]', text):
                            compound_targets.add(match.group(0))
                            
                    if compound_targets:
                        print(f"   🔍 发现并处理 {len(compound_targets)} 种组合引用，如 {list(compound_targets)[:3]}...")
                        # 按照长度从长到短排序，避免短的字符串包含在长的字符串内部
                        for comp_target in sorted(compound_targets, key=len, reverse=True):
                            word.Selection.HomeKey(Unit=6)
                            find = word.Selection.Find
                            find.ClearFormatting()
                            find.Text = comp_target
                            find.MatchWholeWord = False
                            find.MatchWildcards = False
                            while find.Execute():
                                rng = word.Selection.Range
                                current_boundary = (
                                    doc.Bookmarks("Ref_Boundary_Marker").Range.Start
                                    if doc.Bookmarks.Exists("Ref_Boundary_Marker")
                                    else doc.Content.End
                                )
                                if rng.Start >= current_boundary:
                                    break
                                
                                # Skip if match is inside an existing field
                                is_in_field = False
                                try:
                                    for fld in rng.Paragraphs(1).Range.Fields:
                                        try:
                                            if rng.Start >= fld.Code.Start - 1 and rng.End <= fld.Result.End + 1:
                                                is_in_field = True
                                                break
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                if is_in_field:
                                    word.Selection.Collapse(Direction=0)
                                    continue
                                
                                # 智能判断复合引用是否作为主语/宾语
                                is_super = _check_superscript(rng.Start, rng.End)
                                
                                try:
                                    rng.Select()
                                    word.Selection.Text = ""
                                    
                                    # ====== 修改：根据语境决定是否设为上标 ======
                                    if is_super:
                                        word.Selection.Font.Superscript = True
                                        
                                    # 提取括号内部的内容 e.g. "13-15"
                                    inner_text = re.search(r'[\[【]\s*(.*?)\s*[\]】]', comp_target).group(1)
                                    
                                    needs_outer_brackets = True
                                    word.Selection.TypeText("[")
                                    # 记录左括号位置，若自带括号则最后删除
                                    bracket_pos = word.Selection.Start - 1
                                    
                                    # 按数字和非数字分割
                                    # 先将 en-dash/em-dash 统一替换为普通连字符，保证后续处理一致
                                    inner_text = inner_text.replace('\u2013', '-').replace('\u2014', '-')
                                    tokens = re.split(r'(\d+)', inner_text)
                                    for token in tokens:
                                        if not token:
                                            continue
                                        if token.isdigit():
                                            bkmk_key = f"[{token}]"
                                            if bkmk_key in bookmark_map:
                                                bkmk_name, field_switch = bookmark_map[bkmk_key]
                                                # 使用 \# "0" 数字开关，强制剔除源列表带有中括号生成的 [13]，使其纯净变为 13
                                                field_code = f"REF {bkmk_name} {field_switch} \\h \\# \"0\"".strip()
                                                
                                                field = doc.Fields.Add(word.Selection.Range, -1, field_code, True)
                                                
                                                res_txt = field.Result.Text
                                                # 如果依然有中括号 (有些版本Word可能不吃 \# "0")，那我们就记录下来
                                                if "[" in res_txt or "【" in res_txt:
                                                    needs_outer_brackets = False
                                                    
                                                # ==== 核心修复 ====
                                                # 从整个域代码(包括暗箱标记)中彻底跳出，防止后续字符被填入 Result 被 Update() 抹去。
                                                field.Select()
                                                word.Selection.Collapse(Direction=0)
                                            else:
                                                word.Selection.TypeText(token)
                                        else:
                                            word.Selection.TypeText(token)
                                            
                                    if needs_outer_brackets:
                                        word.Selection.TypeText("]")
                                    else:
                                        # 如果本身自带括号，就删开头的占位左括号 "["
                                        doc.Range(bracket_pos, bracket_pos + 1).Text = ""
                                        
                                    # 恢复为非上标状态，以免影响后续正文
                                    if is_super:
                                        word.Selection.Font.Superscript = False
                                        
                                except Exception as e:
                                    print(f"      ⚠️ 组合引用替换失败 ({comp_target}): {e}")
                                word.Selection.Collapse(Direction=0)

                    # ====== 处理单一引用 [X] ======
                    sorted_targets = sorted(bookmark_map.keys(), key=len, reverse=True)
                    for target_text in sorted_targets:
                        bkmk_name, field_switch = bookmark_map[target_text]
                        word.Selection.HomeKey(Unit=6)
                        find = word.Selection.Find
                        find.ClearFormatting()
                        find.Text = target_text
                        find.MatchWholeWord = False
                        find.MatchWildcards = False
                        while find.Execute():
                            rng = word.Selection.Range
                            current_boundary = (
                                doc.Bookmarks("Ref_Boundary_Marker").Range.Start
                                if doc.Bookmarks.Exists("Ref_Boundary_Marker")
                                else doc.Content.End
                            )
                            if rng.Start >= current_boundary:
                                break
                            
                            # Skip if match is inside an existing field
                            is_in_field = False
                            try:
                                for fld in rng.Paragraphs(1).Range.Fields:
                                    try:
                                        if rng.Start >= fld.Code.Start - 1 and rng.End <= fld.Result.End + 1:
                                            is_in_field = True
                                            break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            if is_in_field:
                                word.Selection.Collapse(Direction=0)
                                continue
                            
                            # ====== 安全防护：跳过实际属于复合引用的匹配 ======
                            # 检查 [X] 是否实际嵌入在 [4-5] 或 [1,2,3] 这类复合引用中
                            # 向前看：如果前面紧接着 数字+连接符（如 "4-"），说明是复合尾部
                            # 向后看：如果后面紧接着 连接符+数字（如 "-6"），说明是复合头部
                            is_compound_part = False
                            try:
                                before_ctx = doc.Range(max(0, rng.Start - 5), rng.Start).Text
                                after_ctx = doc.Range(rng.End, min(doc.Content.End, rng.End + 5)).Text
                                # 前面以 数字+连接符+[ 或 数字+连接符 结尾？(如 "4-[" 或在 [4-5] 中 "[4-" 部分)
                                if re.search(r'[\[【]?\d+\s*[\-~～,，\u2013\u2014]\s*$', before_ctx):
                                    is_compound_part = True
                                # 后面以 连接符+数字 开头？(如 "-6]")
                                if re.search(r'^\s*[\-~～,，\u2013\u2014]\s*\d+', after_ctx):
                                    is_compound_part = True
                            except Exception:
                                pass
                            if is_compound_part:
                                word.Selection.Collapse(Direction=0)
                                continue
                            
                            # 根据括号后第一个字符预判是否该上标
                            is_super = _check_superscript(rng.Start, rng.End)
                            
                            try:
                                rng.Text = ""
                                rng.Select()
                                
                                # ====== 修改：根据语境决定单一文献是否设为上标 ======
                                if is_super:
                                    word.Selection.Font.Superscript = True
                                
                                field_code = f"REF {bkmk_name} {field_switch} \\h".strip()
                                doc.Fields.Add(word.Selection.Range, -1, field_code, True)
                                
                                # ====== 恢复：如果刚才设了上标，需恢复成普通文本以免影响后续输入 ======
                                word.Selection.Collapse(Direction=0)
                                if is_super:
                                    word.Selection.Font.Superscript = False
                                
                            except Exception as e:
                                print(f"      ⚠️ 域代码替换失败 ({target_text}): {e}")
                            word.Selection.Collapse(Direction=0)
                    print("   ⏳ 正在全篇刷新文献动态交叉引用...")
                    doc.Fields.Update()
                    
                    # ── 验证：检查所有文献交叉引用是否指向正确的目标 ──
                    print("   🔍 正在验证文献交叉引用的正确性...")
                    ref_issues = []
                    current_boundary = (
                        doc.Bookmarks("Ref_Boundary_Marker").Range.Start
                        if doc.Bookmarks.Exists("Ref_Boundary_Marker")
                        else doc.Content.End
                    )
                    for fi in range(1, doc.Fields.Count + 1):
                        try:
                            fld = doc.Fields(fi)
                            if fld.Code.Start >= current_boundary:
                                continue
                            code_text = fld.Code.Text.strip()
                            result_text = fld.Result.Text.strip()
                            
                            if not code_text.upper().startswith('REF '):
                                continue
                            
                            result_nums = re.findall(r'\d+', result_text)
                            if not result_nums:
                                continue
                            is_ref_citation = bool(re.search(r'[\[\u3010]?\d+[\]\u3011]?', result_text))
                            if not is_ref_citation:
                                continue
                            
                            bkmk_match = re.search(r'REF\s+(\S+)', code_text, re.IGNORECASE)
                            if not bkmk_match:
                                continue
                            bkmk_name = bkmk_match.group(1)
                            
                            # Get paragraph number and surrounding context for locating
                            try:
                                fld_para = fld.Code.Paragraphs(1)
                                para_text = fld_para.Range.Text.strip()
                                # Find field position in paragraph for context
                                fld_pos = fld.Result.Start - fld_para.Range.Start
                                ctx_start = max(0, fld_pos - 30)
                                ctx_end = min(len(para_text), fld_pos + 30)
                                context = para_text[ctx_start:ctx_end].replace('\r', '').replace('\n', '')
                                # Count paragraph index
                                para_num = 0
                                for pi in range(1, doc.Paragraphs.Count + 1):
                                    if doc.Paragraphs(pi).Range.Start == fld_para.Range.Start:
                                        para_num = pi
                                        break
                            except Exception:
                                context = ""
                                para_num = 0
                            
                            loc_str = f"[{para_num}]" if para_num else ""
                            
                            if not doc.Bookmarks.Exists(bkmk_name):
                                ref_issues.append(
                                    f'  \u2022 {loc_str} \u663e\u793a"{result_text}" \u2192 \u4e66\u7b7e"{bkmk_name}"\u4e0d\u5b58\u5728\uff08\u5f15\u7528\u6e90\u4e22\u5931\uff09'
                                    f'\n    \u5b9a\u4f4d: ...{context}...'
                                )
                                continue
                            
                            # \u5bf9\u4e8e\u975e\u672c\u811a\u672c\u521b\u5efa\u7684\u4ea4\u53c9\u5f15\u7528\uff0c\u68c0\u67e5\u4e66\u7b7e\u662f\u5426\u6307\u5411\u53c2\u8003\u6587\u732e\u533a\u57df
                            is_script_ref = bkmk_name.startswith('ARef_') or bkmk_name.startswith('Auto_Ref_')
                            if not is_script_ref:
                                try:
                                    bkmk_rng = doc.Bookmarks(bkmk_name).Range
                                    if bkmk_rng.Start < current_boundary:
                                        bkmk_para_text = _get_paragraph_text_safe(bkmk_rng.Paragraphs(1))
                                        ref_issues.append(
                                            f'  \u2022 {loc_str} \u663e\u793a"{result_text}" \u2192 \u975e\u811a\u672c\u4e66\u7b7e"{bkmk_name}"\uff0c\u6307\u5411\u6b63\u6587\u800c\u975e\u53c2\u8003\u6587\u732e\u533a'
                                            f'\n    \u5b9a\u4f4d: ...{context}...'
                                            f'\n    \u4e66\u7b7e\u6307\u5411: {bkmk_para_text[:50]}...'
                                        )
                                except Exception:
                                    pass
                        except Exception:
                            continue
                    
                    if ref_issues:
                        print(f"\n   {'!'*50}")
                        print(f"   \u26a0\ufe0f  \u68c0\u6d4b\u5230 {len(ref_issues)} \u4e2a\u6587\u732e\u4ea4\u53c9\u5f15\u7528\u5f02\u5e38\uff1a")
                        print(f"   {'-'*50}")
                        for issue in ref_issues:
                            print(f"   {issue}")
                        print(f"   {'-'*50}")
                        print(f"   \u63d0\u793a\uff1a\u8bf7\u624b\u52a8\u68c0\u67e5\u4e0a\u8ff0\u5f15\u7528\uff0c\u53ef\u80fd\u662f\u539f\u6587\u4e2d\u5df2\u6709\u7684\u9519\u8bef\u4ea4\u53c9\u5f15\u7528")
                        print(f"   {'!'*50}")
                    else:
                        print("   \u2705 \u6587\u732e\u4ea4\u53c9\u5f15\u7528\u9a8c\u8bc1\u901a\u8fc7\uff0c\u672a\u53d1\u73b0\u5f02\u5e38")
                else:
                    print("   ⚠️ 未成功建立任何文献书签，已跳过替换。")

        # =============================================================
        # 阶段 C：构建图注书签并生成交叉引用
        # =============================================================
        if not stages.get('C', True):
            print("\n⏭️ [阶段C] 已跳过（用户未勾选）")
        else:
            print("\n🖼️ [阶段C] 正在执行图注(Figure)动态交叉引用生成...")

            # ── 预处理：清理上一次运行残留的图注 REF 域和书签 ──
            print("   🧹 正在清理旧的图注交叉引用域代码...")
            fig_unlinked = 0
            try:
                for fi in range(doc.Fields.Count, 0, -1):
                    try:
                        fld = doc.Fields(fi)
                        code_text = fld.Code.Text.strip()
                        if code_text.startswith('REF _RefAutoFig_'):
                            fld.Unlink()
                            fig_unlinked += 1
                    except Exception:
                        continue
            except Exception:
                pass

            fig_bkmk_del = 0
            try:
                for bi in range(doc.Bookmarks.Count, 0, -1):
                    try:
                        bkmk = doc.Bookmarks(bi)
                        if bkmk.Name.startswith('_RefAutoFig_'):
                            bkmk.Delete()
                            fig_bkmk_del += 1
                    except Exception:
                        continue
            except Exception:
                pass

            if fig_unlinked or fig_bkmk_del:
                print(f"   ✅ 清理完毕：还原 {fig_unlinked} 个域代码，删除 {fig_bkmk_del} 个旧书签")
            else:
                print("   ℹ️  未发现旧的图注交叉引用，无需清理")

            fig_bookmark_map = {}

            for para in doc.Paragraphs:
                # 严密防线：只收集真正被打上“题注”标签的段落，防止误杀正文独立成段的引用（比如“图2.4是一个重要的...”）
                try:
                    style_id = getattr(para.Style, 'NameLocal', '')
                    # Word 的题注样式ID是 -35，如果不确认也可以通过字面判断
                except:
                    style_id = ''
                    
                text = para.Range.Text.strip()
                match = FIG_CAPTION_PATTERN.match(text)
                
                # 判断是否为合法题注：不仅要正则匹配，还必须是被标记的题注样式或者是含有 SEQ 域的段落
                is_valid_caption = False
                has_seq_chk = False
                try:
                    for fld in para.Range.Fields:
                        if 'SEQ' in fld.Code.Text.upper():
                            has_seq_chk = True
                            break
                except: pass
                
                if match and len(text) <= 200 and (has_seq_chk or '-35' in str(para.Style) or '题注' in style_id or 'Caption' in style_id):
                    is_valid_caption = True

                if is_valid_caption:
                    orig_fig_label = match.group(1).strip()
                    # 关键修复：加入 \s* 允许容忍跨越空格的数字提取（例如 "2. 13"）
                    core_num_match = re.search(r'\d+(?:\s*[\.\-]\s*(?:\d+|[A-Za-z]+))?(?:\s*\([a-zA-Z]\))?', orig_fig_label)
                    core_num_raw = core_num_match.group(0) if core_num_match else orig_fig_label
                    # 彻底净化：抹除所有空格，确保 core_num 严格变为 "2.13"，防范 Word Find 越界或短路
                    core_num = re.sub(r'\s+', '', core_num_raw)
                    
                    ascii_label = re.sub(r'[^a-zA-Z0-9]', '_', core_num)
                    safe_name = f"_RefAutoFig_Tu_{ascii_label}"
                    safe_name = re.sub(r'_+', '_', safe_name)
                    safe_name = safe_name.rstrip('_')
                    if len(safe_name) > 40:
                        safe_name = safe_name[:40]
                    try:
                        rng = para.Range
                        
                        has_seq_field = False
                        seq_field_obj = None
                        try:
                            for fld in rng.Fields:
                                if 'SEQ' in fld.Code.Text.upper():
                                    has_seq_field = True
                                    seq_field_obj = fld
                                    break
                        except Exception:
                            pass
                        
                        bookmark_added = False
                        if has_seq_field and seq_field_obj:
                            para_start = rng.Start
                            # 回退 +1 错误，原生的 Result.End 准确锚定在 field 闭合端。
                            label_end = seq_field_obj.Result.End
                            bkmk_rng = doc.Range(para_start, label_end)
                            m_sub = re.search(r'\([a-zA-Z]\)\s*$', orig_fig_label)
                            if m_sub:
                                bkmk_rng.End = label_end + len(m_sub.group())
                            doc.Bookmarks.Add(safe_name, bkmk_rng)
                            bookmark_added = True
                        else:
                            word.Selection.HomeKey(Unit=6)
                            find = rng.Find
                            find.ClearFormatting()
                            # 降级匹配时使用原本带空格的原文，保证能命中题注本身
                            find.Text = orig_fig_label
                            find.MatchWholeWord = False
                            if find.Execute():
                                doc.Bookmarks.Add(safe_name, rng)
                                bookmark_added = True
                            else:
                                rng = para.Range
                                start_offset = text.find(orig_fig_label)
                                if start_offset != -1:
                                    rng.SetRange(rng.Start + start_offset,
                                                 rng.Start + start_offset + len(orig_fig_label))
                                    doc.Bookmarks.Add(safe_name, rng)
                                    bookmark_added = True
                                    
                        if bookmark_added:
                            fig_bookmark_map[core_num] = safe_name
                            print(f"   📍 锁定图注: {orig_fig_label} -> 建立暗桩: {safe_name}")
                    except Exception as e:
                        print(f"      ⚠️ 添加图注书签因系统异常失败: {e}")

            # ── 合并草稿编号映射：将 Stage D 记录的旧草稿编号加入搜索目标 ──
            if draft_fig_map:
                merged = 0
                for old_core, new_core in draft_fig_map.items():
                    if new_core and new_core in fig_bookmark_map and old_core not in fig_bookmark_map:
                        fig_bookmark_map[old_core] = fig_bookmark_map[new_core]
                        merged += 1
                        print(f"   🔄 草稿引用映射: 图{old_core} → 图{new_core} (共享书签 {fig_bookmark_map[new_core]})")
                if merged:
                    print(f"   ✅ 共合并 {merged} 个草稿编号到搜索目标")

            if fig_bookmark_map:
                print(f"   ✅ 共提取 {len(fig_bookmark_map)} 个合法图注段落，准备正文安全替换...")

                # 收集所有图注书签名列表，用于全局防护盾
                all_caption_bkmk_names = list(fig_bookmark_map.values())

                sorted_fig_targets = sorted(fig_bookmark_map.keys(), key=len, reverse=True)
                for core_num in sorted_fig_targets:
                    bkmk_name = fig_bookmark_map[core_num]
                    word.Selection.HomeKey(Unit=6)
                    find = word.Selection.Find
                    find.ClearFormatting()
                    
                    # 只搜索核心数字，避免 Word 通配符 * 的贪婪匹配吃掉整段文字
                    find.Text = core_num
                    find.MatchWildcards = False
                    
                    while find.Execute():
                        rng = word.Selection.Range

                        # Guard: skip if match is inside an existing field (REF domain)
                        # This prevents infinite loop when Find matches text within
                        # a field result that was just created or already exists
                        is_in_field = False
                        try:
                            para_fields = rng.Paragraphs(1).Range.Fields
                            for fld in para_fields:
                                try:
                                    fld_start = fld.Code.Start - 1
                                    fld_end = fld.Result.End + 1
                                    if rng.Start >= fld_start and rng.End <= fld_end:
                                        is_in_field = True
                                        break
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        if is_in_field:
                            word.Selection.Collapse(Direction=0)
                            continue

                        # ✅ 全局动态锚点防护盾
                        is_in_any_caption = False
                        for chk_bkmk in all_caption_bkmk_names:
                            try:
                                if doc.Bookmarks.Exists(chk_bkmk):
                                    chk_rng = doc.Bookmarks(chk_bkmk).Range
                                    chk_para = chk_rng.Paragraphs(1).Range
                                    if rng.Start >= chk_para.Start and rng.End <= chk_para.End:
                                        is_in_any_caption = True
                                        break
                            except Exception:
                                pass

                        if is_in_any_caption:
                            word.Selection.Collapse(Direction=0)
                            continue

                        # 💥 向后边界检查防误伤：确保当前匹配的（如 "2.2"）并不是 "2.2.1" 或 "2.24" 的一部分
                        next_char_rng = doc.Range(rng.End, min(doc.Range().End, rng.End + 1))
                        # 仅排除数字、点和横杠。如果后面紧跟括号 '(' 或字母 'a'（如 2.2(a) 或 2.2a），我们**不拦截**！
                        # 这样 "图 2.2" 依然能被成功交叉引用，后缀 "(a)" 会作为普通文本保留。
                        if re.match(r'[\.\-\d]', next_char_rng.Text):
                            word.Selection.Collapse(Direction=0)
                            continue

                        # 向前探测前缀（"图 ", "Fig. " 等）
                        probe_rng = doc.Range(max(0, rng.Start - 15), rng.Start)
                        probe_text = probe_rng.Text
                        
                        # 查找紧挨着数字的前缀：使用硬空格符匹配，彻底杜绝跨越 \r, \n, \x0b 匹配到了上一行的 "图"
                        m_prefix = re.search(r'(图|Fig\.?|Figure)[ \t\xA0\u3000]*$', probe_text, re.IGNORECASE)
                        if m_prefix:
                            # 扩展 rng 包含前缀
                            rng.Start = probe_rng.Start + m_prefix.start()
                            try:
                                # 直接替换 rng（而不是先清空）可以让 Word 原生继承周边的字重、颜色
                                field_code = f"REF {bkmk_name} \\h".strip()
                                field = doc.Fields.Add(rng, -1, field_code, True)
                                # 域代码自动继承周围正文的字体、字号，不再强制设置
                                
                                # Advance cursor past the newly created field
                                # to prevent Find from re-matching within field result
                                try:
                                    field.Select()
                                    word.Selection.Collapse(Direction=0)
                                except Exception:
                                    pass
                            except Exception as e:
                                print(f"      ⚠️ 图注域代码替换失败: {e}")
                        
                        word.Selection.Collapse(Direction=0)
                print("   ⏳ 正在全篇刷新图注动态交叉引用...")
                doc.Fields.Update()
            else:
                print("   ⚠️ 未在文档中识别出合法的独立图注段落。")


        # =============================================================
        # 阶段 E：检测缩写词定义
        # =============================================================
        if not stages.get('E', True):
            print("\n⏭️ [阶段E] 已跳过（用户未勾选）")
        else:
            check_acronym_definitions(doc)

        # =============================================================
        # 保存（仅当执行了修改文档的阶段时才保存）
        # =============================================================
        modifying_stages = any(stages.get(s) for s in ['A', 'B', 'C', 'D'])
        if modifying_stages:
            doc.SaveAs2(output_file, FileFormat=file_format)
            print(f"\n🎉 处理完成！已保存为: {os.path.basename(output_file)}")
        else:
            print(f"\n🎉 检查完成！（本次仅执行检测，未修改文档，无需保存）")

    except Exception as e:
        print(f"❌ 剧本执行崩溃: {e}")
        traceback.print_exc()
    finally:
        try:
            if doc is not None and not was_already_open:
                doc.Close(SaveChanges=0)
        except Exception:
            pass


class RedirectLogger:
    """这是一个拦截器窗口，专门用来接收 print() 的输出"""
    def __init__(self):
        import tkinter as tk
        self.win = tk.Tk()
        self.win.title("🤖 运行日志 - 正在处理中...")
        self.win.geometry("700x500")
        self.win.configure(bg="#1e1e2e")
        self.win.attributes("-topmost", True) # 保持窗口在最前
        
        # 创建一个深色主题的文本框
        self.text = tk.Text(self.win, bg="#1e1e2e", fg="#a6e3a1", font=("Microsoft YaHei", 10), bd=0)
        self.text.pack(fill="both", expand=True, padx=15, pady=15)
        
        # 魔法开始：把系统标准的输出通道替换成自己
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        sys.stdout = self
        sys.stderr = self

    def write(self, msg):
        # 每次程序调用 print()，都会触发这里，把文字插进文本框
        self.text.insert("end", msg)
        self.text.see("end") # 自动滚动到最底部
        self.win.update()    # 强制立刻刷新界面

    def flush(self):
        pass

    def restore_and_wait(self):
        import tkinter as tk
        # 处理完后，把输出通道还给系统
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr
        # 弹出一个关闭按钮，防止处理完瞬间闪退，让用户能看清结果
        btn = tk.Button(self.win, text="处理完成，点击关闭并返回主界面", 
                        command=self.win.destroy, 
                        bg="#7c6af7", fg="white", font=("Microsoft YaHei", 10, "bold"),
                        relief='flat', padx=20, pady=8, cursor='hand2')
        btn.pack(pady=10)
        self.win.mainloop()

if __name__ == "__main__":
    while True:
        # 1. 弹出原来的主控制面板
        file_path, modify_in_place, stages, action = show_main_dialog()
        if action == 'quit' or not file_path:
            break
        
        # 2. 用户点击开始后，弹出一个专门的日志窗口拦截 print()
        logger = RedirectLogger()
        
        # 3. 开始执行核心逻辑（此时所有的 print 都会跑到 logger 窗口里）
        process_document(file_path, modify_in_place, stages)
        print("\n" + "─" * 50)
        print("✅ 本次处理完成！请检查上方日志。")
        
        # 4. 暂停在这里等待用户点击“关闭按钮”
        logger.restore_and_wait()