"""
项目名称: 数治乡音：村级民生诉求分流系统
创建时间: 2026/08/20
"""

import sqlite3
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh


class Database:
    """工单数据库的读写封装"""

    @classmethod
    def _conn(cls):
        """获取数据库连接"""
        conn = sqlite3.connect("workorders.db")
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def init(cls):
        """建表（幂等）"""
        conn = cls._conn()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_no TEXT NOT NULL UNIQUE,
                reporter TEXT NOT NULL,
                phone TEXT,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '待处理',
                handler TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    @classmethod
    def get_all(cls, status=None, category=None):
        """查询工单，支持按状态和类别筛选"""
        conn = cls._conn()
        query = "SELECT * FROM tickets WHERE 1=1"
        params = []
        if status and status != "全部":
            query += " AND status = ?"
            params.append(status)
        if category and category != "全部":
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC"
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        return df

    @classmethod
    def get_by_no(cls, ticket_no):
        """按编号查询单条工单"""
        conn = cls._conn()
        df = pd.read_sql_query(
            "SELECT * FROM tickets WHERE ticket_no = ?", conn, params=[ticket_no]
        )
        conn.close()
        return df.iloc[0] if not df.empty else None

    @classmethod
    def add(cls, reporter, phone, category, description):
        """新增工单，返回生成的编号"""
        conn = cls._conn()
        cursor = conn.cursor()
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        prefix = f"T{date_str}"
        cursor.execute(
            "SELECT MAX(CAST(SUBSTR(ticket_no, 9) AS INTEGER)) "
            "FROM tickets WHERE ticket_no LIKE ?",
            (f"{prefix}%",),
        )
        max_seq = cursor.fetchone()[0] or 0
        new_seq = str(max_seq + 1).zfill(3)
        ticket_no = f"{prefix}{new_seq}"
        time_str = now.strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            """INSERT INTO tickets 
               (ticket_no, reporter, phone, category, description, status, handler, created_at, updated_at) 
               VALUES (?, ?, ?, ?, ?, '待处理', '', ?, ?)""",
            (ticket_no, reporter, phone, category, description, time_str, time_str),
        )
        conn.commit()
        conn.close()
        return ticket_no

    @classmethod
    def update_status(cls, ticket_no, new_status, handler=""):
        """更新工单状态"""
        conn = cls._conn()
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "UPDATE tickets SET status = ?, handler = ?, updated_at = ? "
            "WHERE ticket_no = ?",
            (new_status, handler, now, ticket_no),
        )
        conn.commit()
        conn.close()

    @classmethod
    def delete(cls, ticket_no):
        """删除工单"""
        conn = cls._conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tickets WHERE ticket_no = ?", (ticket_no,))
        conn.commit()
        conn.close()

    @classmethod
    def count_by_status(cls):
        """统计各状态数量"""
        conn = cls._conn()
        df = pd.read_sql_query(
            "SELECT status, COUNT(*) as count FROM tickets GROUP BY status", conn
        )
        conn.close()
        result = {"待处理": 0, "处理中": 0, "已办结": 0}
        for _, row in df.iterrows():
            result[row["status"]] = row["count"]
        return result

    @classmethod
    def count_by_category(cls):
        """统计各类别未办结数量"""
        conn = cls._conn()
        df = pd.read_sql_query(
            "SELECT category, COUNT(*) as count FROM tickets "
            "WHERE status != '已办结' GROUP BY category",
            conn,
        )
        conn.close()
        return df

    @classmethod
    def get_recent(cls, limit=5):
        """最新 N 条工单"""
        conn = cls._conn()
        df = pd.read_sql_query(
            "SELECT ticket_no, reporter, category, description, status, created_at "
            "FROM tickets ORDER BY created_at DESC LIMIT ?",
            conn,
            params=[limit],
        )
        conn.close()
        return df


class Styles:
    """全局 CSS 与可复用 HTML 片段"""

    CSS = """
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    .stApp {
        background: #f4f6fa;
    }
    h1, h2, h3, h4 {
        color: #0f172a;
        letter-spacing: 0.2px;
    }
    #MainMenu, footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #14243d 0%, #1e3a5f 60%, #2b5c8a 100%);
        box-shadow: 4px 0 18px rgba(15, 23, 42, 0.08);
    }
    section[data-testid="stSidebar"] * {
        color: #cbd5e1 !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.08) !important;
    }
    section[data-testid="stSidebar"] .stRadio > label {
        display: none;
    }
    section[data-testid="stSidebar"] .stRadio > div {
        gap: 6px;
    }
    section[data-testid="stSidebar"] .stRadio label {
        display: flex;
        align-items: center;
        padding: 10px 14px !important;
        border-radius: 10px;
        transition: all 0.18s ease;
        cursor: pointer;
        border: 1px solid transparent;
    }
    section[data-testid="stSidebar"] .stRadio label:hover {
        background: rgba(255,255,255,0.06);
        border-color: rgba(255,255,255,0.08);
    }
    section[data-testid="stSidebar"] .stRadio label[data-checked="true"],
    section[data-testid="stSidebar"] .stRadio input:checked + div {
        background: rgba(59,130,246,0.18);
    }
    .hero {
        background: linear-gradient(120deg, #1e3a5f 0%, #2b5c8a 50%, #3b82f6 100%);
        border-radius: 16px;
        padding: 26px 32px;
        margin-bottom: 22px;
        color: #fff;
        box-shadow: 0 8px 24px rgba(30, 58, 95, 0.18);
        position: relative;
        overflow: hidden;
    }
    .hero::after {
        content: "";
        position: absolute;
        top: -40%;
        right: -10%;
        width: 260px;
        height: 260px;
        background: radial-gradient(circle, rgba(255,255,255,0.10) 0%, transparent 70%);
        border-radius: 50%;
    }
    .hero .hero-title {
        font-size: 24px;
        font-weight: 700;
        letter-spacing: 1px;
        margin: 0 0 6px 0;
    }
    .hero .hero-sub {
        font-size: 13px;
        opacity: 0.85;
        letter-spacing: 0.5px;
    }
    .stat-card {
        background: #ffffff;
        border-radius: 14px;
        padding: 20px 24px;
        box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
        border-left: 5px solid #cbd5e1;
        transition: transform 0.2s, box-shadow 0.2s;
        position: relative;
        overflow: hidden;
    }
    .stat-card::after {
        content: "";
        position: absolute;
        bottom: -20px;
        right: -20px;
        width: 80px;
        height: 80px;
        border-radius: 50%;
        opacity: 0.08;
    }
    .stat-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.10);
    }
    .stat-card .label {
        font-size: 13px;
        color: #64748b;
        margin-bottom: 8px;
        font-weight: 500;
        letter-spacing: 0.5px;
    }
    .stat-card .value {
        font-size: 34px;
        font-weight: 700;
        color: #0f172a;
        line-height: 1;
    }
    .stat-card .unit {
        font-size: 13px;
        color: #94a3b8;
        margin-left: 6px;
        font-weight: 400;
    }
    .stat-card.pending { border-left-color: #f59e0b; }
    .stat-card.pending::after { background: #f59e0b; }
    .stat-card.progress { border-left-color: #3b82f6; }
    .stat-card.progress::after { background: #3b82f6; }
    .stat-card.done { border-left-color: #10b981; }
    .stat-card.done::after { background: #10b981; }
    .ticket-card {
        background: #ffffff;
        border-radius: 12px;
        padding: 18px 22px;
        margin-bottom: 12px;
        box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04);
        border: 1px solid #e9eef5;
        transition: box-shadow 0.2s, border-color 0.2s;
    }
    .ticket-card:hover {
        box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
        border-color: #dbeafe;
    }
    .ticket-card .head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
    }
    .ticket-card .ticket-no {
        font-family: "SF Mono", Consolas, monospace;
        font-size: 15px;
        font-weight: 600;
        color: #1e3a5f;
        letter-spacing: 0.5px;
    }
    .ticket-card .body {
        font-size: 14px;
        color: #475569;
        line-height: 1.65;
    }
    .ticket-card .meta {
        font-size: 12px;
        color: #94a3b8;
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px dashed #e9eef5;
    }
    .badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0.4px;
    }
    .badge.pending { background: #fef3c7; color: #b45309; }
    .badge.progress { background: #dbeafe; color: #1d4ed8; }
    .badge.done { background: #d1fae5; color: #047857; }
    .category-tag {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        background: #eef2f7;
        color: #475569;
        font-size: 12px;
        font-weight: 500;
        margin-right: 8px;
        border: 1px solid #e2e8f0;
    }
    .empty-state {
        background: #ffffff;
        border: 1px dashed #dbe1ea;
        border-radius: 14px;
        padding: 48px 24px;
        text-align: center;
        color: #94a3b8;
        margin: 16px 0;
    }
    .empty-state .icon {
        font-size: 36px;
        margin-bottom: 10px;
        opacity: 0.4;
    }
    .empty-state .text {
        font-size: 14px;
        color: #64748b;
    }
    [data-testid="stForm"] {
        background: #ffffff;
        border-radius: 14px;
        padding: 24px 26px 8px 26px;
        box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
        border: 1px solid #e9eef5;
        margin-bottom: 18px;
    }
    .success-banner {
        background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%);
        border-radius: 12px;
        padding: 20px 24px;
        border-left: 5px solid #10b981;
        margin-bottom: 20px;
    }
    .success-banner .title {
        font-size: 15px;
        font-weight: 600;
        color: #065f46;
        margin-bottom: 8px;
    }
    .success-banner .body {
        font-size: 14px;
        color: #047857;
    }
    .success-banner .hint {
        font-size: 12px;
        color: #059669;
        margin-top: 6px;
    }
    .success-banner code {
        background: rgba(255,255,255,0.6);
        padding: 3px 10px;
        border-radius: 6px;
        font-family: "SF Mono", Consolas, monospace;
        font-size: 14px;
        color: #065f46;
        font-weight: 600;
    }
    .login-wrap {
        max-width: 400px;
        margin: 60px auto;
    }
    .login-card {
        background: #ffffff;
        border-radius: 16px;
        padding: 36px 32px 24px 32px;
        box-shadow: 0 10px 40px rgba(15, 23, 42, 0.08);
        border: 1px solid #e9eef5;
    }
    .login-card .login-title {
        font-size: 20px;
        font-weight: 700;
        color: #0f172a;
        text-align: center;
        margin-bottom: 6px;
    }
    .login-card .login-sub {
        font-size: 13px;
        color: #94a3b8;
        text-align: center;
        margin-bottom: 24px;
    }
    .stButton > button {
        border-radius: 9px;
        font-weight: 500;
        border: 1px solid #e2e8f0;
        transition: all 0.18s;
        padding: 8px 16px;
    }
    .stButton > button:hover {
        border-color: #cbd5e1;
        transform: translateY(-1px);
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
        box-shadow: 0 2px 8px rgba(59, 130, 246, 0.28);
        border: none;
        color: #fff;
    }
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 6px 16px rgba(59, 130, 246, 0.42);
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
    }
    .stTextInput input, .stTextArea textarea,
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 9px !important;
        border-color: #e2e8f0 !important;
        transition: border-color 0.18s, box-shadow 0.18s;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.12) !important;
    }
    [data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid #e9eef5;
        box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04);
    }
    hr {
        border-color: #e9eef5;
        margin: 1.6rem 0;
    }
    .app-footer {
        text-align: center;
        padding: 24px 0 8px 0;
        color: #94a3b8;
        font-size: 12px;
        letter-spacing: 0.5px;
    }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
    </style>
    """

    @classmethod
    def inject(cls):
        """把 CSS 注入当前页面"""
        st.markdown(cls.CSS, unsafe_allow_html=True)

    @staticmethod
    def hero(title, subtitle):
        """页面顶部 Hero 横幅"""
        return f"""
        <div class="hero">
            <div class="hero-title">{title}</div>
            <div class="hero-sub">{subtitle}</div>
        </div>
        """

    @staticmethod
    def status_badge(status):
        """状态徽章"""
        cls_map = {"待处理": "pending", "处理中": "progress", "已办结": "done"}
        cls = cls_map.get(status, "pending")
        return f'<span class="badge {cls}">{status}</span>'

    @staticmethod
    def stat_card(label, value, kind):
        """单个统计卡片"""
        return f"""
        <div class="stat-card {kind}">
            <div class="label">{label}</div>
            <div class="value">{value}<span class="unit">件</span></div>
        </div>
        """

    @staticmethod
    def render_stat_row(counts):
        """一行三个统计卡片"""
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                Styles.stat_card("待处理", counts["待处理"], "pending"),
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                Styles.stat_card("处理中", counts["处理中"], "progress"),
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                Styles.stat_card("已办结", counts["已办结"], "done"),
                unsafe_allow_html=True,
            )

    @staticmethod
    def empty_state(text, icon="○"):
        """空状态卡片"""
        return f"""
        <div class="empty-state">
            <div class="icon">{icon}</div>
            <div class="text">{text}</div>
        </div>
        """

    @staticmethod
    def footer():
        """页脚"""
        return """
        <div class="app-footer">
            数治乡音 · 智汇乡村　｜　2026 年暑期社会实践
        </div>
        """


class UserPage:
    """用户提交诉求 + 查询进度"""

    @classmethod
    def render(cls):
        st.markdown(
            Styles.hero("提交诉求", "请填写以下信息，我们会在第一时间为您处理"),
            unsafe_allow_html=True,
        )

        cls._render_success_banner()
        cls._render_form()
        cls._render_query_section()
        st.markdown(Styles.footer(), unsafe_allow_html=True)

    @classmethod
    def _render_success_banner(cls):
        """提交成功后的提示条"""
        if not st.session_state.get("submit_success", False):
            return
        ticket_no = st.session_state.get("last_ticket_no", "")
        st.markdown(
            f"""
            <div class="success-banner">
                <div class="title">提交成功</div>
                <div class="body">
                    工单编号：<code>{ticket_no}</code>
                </div>
                <div class="hint">请保存此编号，以便查询办理进度</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("继续提交"):
            st.session_state.submit_success = False
            st.rerun()

    @classmethod
    def _render_form(cls):
        """提交表单"""
        with st.form(key="submit_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                reporter = st.text_input("您的姓名", placeholder="请输入真实姓名")
            with col2:
                phone = st.text_input("联系方式", placeholder="手机号（选填）")

            category = st.selectbox(
                "问题类别", ["道路", "水利", "网络", "医疗", "其他"]
            )
            description = st.text_area(
                "问题描述", placeholder="请详细描述您遇到的问题", height=120
            )

            submitted = st.form_submit_button(
                "提交诉求", type="primary", use_container_width=True
            )

            if submitted:
                cls._handle_submit(reporter, phone, category, description)

    @classmethod
    def _handle_submit(cls, reporter, phone, category, description):
        """校验并写入数据库"""
        if not reporter.strip():
            st.error("请填写您的姓名")
            return
        if not description.strip():
            st.error("请填写问题描述")
            return
        ticket_no = Database.add(
            reporter.strip(), phone.strip(), category, description.strip()
        )
        st.session_state.submit_success = True
        st.session_state.last_ticket_no = ticket_no
        st.rerun()

    @classmethod
    def _render_query_section(cls):
        """查询办理进度"""
        st.markdown("---")
        st.markdown("### 查询办理进度")

        col1, col2 = st.columns([3, 1])
        with col1:
            query_no = st.text_input(
                "请输入工单编号",
                placeholder="如 T20260801001",
                label_visibility="collapsed",
            )
        with col2:
            query_btn = st.button("查询", use_container_width=True, type="primary")

        if not query_btn:
            return
        if not query_no.strip():
            st.warning("请输入工单编号")
            return

        ticket = Database.get_by_no(query_no.strip())
        if ticket is None:
            st.markdown(
                Styles.empty_state("未找到该工单，请检查编号是否正确"),
                unsafe_allow_html=True,
            )
            return

        handler_line = (
            f"<div style='margin-top:6px;'><b>处理人：</b>{ticket['handler']}</div>"
            if ticket["handler"]
            else ""
        )
        st.markdown(
            f"""
            <div class="ticket-card">
                <div class="head">
                    <span class="ticket-no">{ticket['ticket_no']}</span>
                    {Styles.status_badge(ticket['status'])}
                </div>
                <div class="body">
                    <div><span class="category-tag">{ticket['category']}</span></div>
                    <div style="margin-top:8px;">{ticket['description']}</div>
                    <div class="meta">提交时间：{ticket['created_at']}{handler_line}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


class AdminPage:
    """管理后台：登录、筛选、统计、派单/办结/删除"""

    PASSWORD = "admin"

    @classmethod
    def render(cls):
        cls._ensure_login_state()

        if not st.session_state.admin_logged_in:
            cls._render_login()
            st.stop()

        cls._render_header()
        cls._render_stats()
        df = cls._render_filters()
        st.markdown("---")
        cls._render_ticket_list(df)
        st.markdown(Styles.footer(), unsafe_allow_html=True)

    @classmethod
    def _ensure_login_state(cls):
        """初始化登录状态"""
        if "admin_logged_in" not in st.session_state:
            st.session_state.admin_logged_in = False

    @classmethod
    def _render_login(cls):
        """登录界面"""
        st.markdown(
            """
            <div class="login-wrap">
                <div class="login-card">
                    <div class="login-title">管理后台</div>
                    <div class="login-sub">请输入管理密码以继续</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        col1, col2, col3 = st.columns([1, 1.2, 1])
        with col2:
            pwd = st.text_input(
                "管理密码",
                type="password",
                label_visibility="collapsed",
                placeholder="请输入管理密码",
            )
            if st.button("登录", use_container_width=True, type="primary"):
                if pwd == cls.PASSWORD:
                    st.session_state.admin_logged_in = True
                    st.rerun()
                else:
                    st.error("密码错误")

    @classmethod
    def _render_header(cls):
        """Hero + 退出按钮"""
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(
                Styles.hero("管理后台", "工单派发 · 状态跟踪 · 记录管理"),
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown("<div style='height:22px;'></div>", unsafe_allow_html=True)
            if st.button("退出管理", use_container_width=True):
                st.session_state.admin_logged_in = False
                st.rerun()

    @classmethod
    def _render_stats(cls):
        """统计卡片"""
        counts = Database.count_by_status()
        Styles.render_stat_row(counts)
        st.markdown("")

    @classmethod
    def _render_filters(cls):
        """筛选栏"""
        col1, col2 = st.columns(2)
        with col1:
            status_filter = st.selectbox(
                "按状态筛选", ["全部", "待处理", "处理中", "已办结"]
            )
        with col2:
            category_filter = st.selectbox(
                "按类别筛选", ["全部", "道路", "水利", "网络", "医疗", "其他"]
            )
        return Database.get_all(status_filter, category_filter)

    @classmethod
    def _render_ticket_list(cls, df):
        """工单卡片列表"""
        if df.empty:
            st.markdown(
                Styles.empty_state("暂无工单记录"),
                unsafe_allow_html=True,
            )
            return

        for _, row in df.iterrows():
            cls._render_single_ticket(row)

    @classmethod
    def _render_single_ticket(cls, row):
        """渲染单张工单卡片和操作按钮"""
        ticket_no = row["ticket_no"]
        status = row["status"]
        phone = row["phone"] or "-"

        st.markdown(
            f"""
            <div class="ticket-card">
                <div class="head">
                    <span class="ticket-no">{ticket_no}</span>
                    {Styles.status_badge(status)}
                </div>
                <div class="body">
                    <div style="margin-bottom:8px;">
                        <span class="category-tag">{row['category']}</span>
                        <b>{row['reporter']}</b> · {phone}
                    </div>
                    <div>{row['description']}</div>
                    <div class="meta">提交：{row['created_at']} ｜ 更新：{row['updated_at']}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        col1, col2, col3, col4 = st.columns([5, 1, 1, 1])
        with col4:
            cls._render_action_button(ticket_no, status)

    @classmethod
    def _render_action_button(cls, ticket_no, status):
        """根据状态渲染对应操作按钮"""
        if status == "待处理":
            if st.button(
                "派单",
                key=f"assign_{ticket_no}",
                use_container_width=True,
                type="primary",
            ):
                Database.update_status(ticket_no, "处理中", "管理员")
                st.rerun()
        elif status == "处理中":
            if st.button(
                "办结",
                key=f"complete_{ticket_no}",
                use_container_width=True,
                type="primary",
            ):
                Database.update_status(ticket_no, "已办结", "管理员")
                st.rerun()
        else:
            if st.button("删除", key=f"delete_{ticket_no}", use_container_width=True):
                Database.delete(ticket_no)
                st.rerun()


class DashboardPage:
    """数据大屏：统计、图表、最新动态、全部工单"""

    REFRESH_SECONDS = 15

    CATEGORY_COLORS = {
        "道路": "#3b82f6",
        "水利": "#06b6d4",
        "网络": "#8b5cf6",
        "医疗": "#ef4444",
        "其他": "#94a3b8",
    }

    @classmethod
    def render(cls):
        st_autorefresh(
            interval=cls.REFRESH_SECONDS * 1000,
            key="dashboard_autorefresh",
        )
        cls._render_header()
        cls._render_stats()
        st.markdown("---")
        cls._render_main_section()
        st.markdown("---")
        cls._render_all_tickets()
        st.markdown(Styles.footer(), unsafe_allow_html=True)

    @classmethod
    def _render_header(cls):
        """Hero + 刷新按钮"""
        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(
                Styles.hero(
                    "村民生诉求实时看板",
                    f"数据每 {cls.REFRESH_SECONDS} 秒自动刷新",
                ),
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown("<div style='height:22px;'></div>", unsafe_allow_html=True)
            if st.button("刷新", use_container_width=True):
                st.rerun()

    @classmethod
    def _render_stats(cls):
        """统计卡片"""
        counts = Database.count_by_status()
        Styles.render_stat_row(counts)

    @classmethod
    def _render_main_section(cls):
        """左侧图表 + 右侧最新诉求"""
        col1, col2 = st.columns([2, 1])
        with col1:
            cls._render_category_chart()
        with col2:
            cls._render_recent_list()

    @classmethod
    def _render_category_chart(cls):
        """类别分布图"""
        st.markdown("##### 各类别未办结问题分布")
        df_cat = Database.count_by_category()
        if df_cat.empty:
            st.markdown(
                Styles.empty_state("所有问题均已办结", icon="✓"),
                unsafe_allow_html=True,
            )
            return

        fig = px.bar(
            df_cat,
            x="category",
            y="count",
            color="category",
            text="count",
            color_discrete_map=cls.CATEGORY_COLORS,
        )
        fig.update_traces(textposition="outside", marker_line_width=0)
        fig.update_layout(
            showlegend=False,
            height=340,
            margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis_title="",
            yaxis_title="",
            font=dict(family="-apple-system, PingFang SC, sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True)

    @classmethod
    def _render_recent_list(cls):
        """右侧最新诉求"""
        st.markdown("##### 最新诉求")
        recent = Database.get_recent(5)
        if recent.empty:
            st.markdown(
                Styles.empty_state("暂无工单"),
                unsafe_allow_html=True,
            )
            return

        for _, row in recent.iterrows():
            st.markdown(
                f"""
                <div style="background:#fff;border-radius:10px;padding:12px 14px;
                            margin-bottom:10px;border-left:3px solid #3b82f6;
                            box-shadow:0 1px 4px rgba(15,23,42,0.05);">
                    <div style="display:flex;justify-content:space-between;
                                align-items:center;margin-bottom:6px;">
                        <span style="font-family:monospace;font-weight:600;
                                     color:#1e3a5f;font-size:13px;">{row['ticket_no']}</span>
                        {Styles.status_badge(row['status'])}
                    </div>
                    <div style="font-size:12px;color:#64748b;">
                        {row['category']} · {row['reporter']}
                    </div>
                    <div style="font-size:11px;color:#94a3b8;margin-top:4px;">
                        {row['created_at'][5:16]}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    @classmethod
    def _render_all_tickets(cls):
        """底部全部工单一览表"""
        st.markdown("##### 全部工单状态一览")
        df_all = Database.get_all()
        if df_all.empty:
            st.markdown(
                Styles.empty_state("暂无工单记录"),
                unsafe_allow_html=True,
            )
            return

        df_display = df_all[
            ["ticket_no", "category", "description", "status", "updated_at"]
        ].head(10)
        df_display.columns = ["工单编号", "类别", "描述", "状态", "更新时间"]
        st.dataframe(df_display, use_container_width=True, hide_index=True)


class App:
    """Streamlit 应用主入口"""

    PAGE_TITLE = "数治乡音 · 民生诉求分流系统"

    PAGES = {
        "提交诉求": UserPage,
        "管理后台": AdminPage,
        "数据看板": DashboardPage,
    }

    @classmethod
    def run(cls):
        """主流程：初始化 → 侧边栏 → 分发页面"""
        cls._setup_page()
        Styles.inject()
        Database.init()
        cls._render_sidebar()
        cls._dispatch()

    @classmethod
    def _setup_page(cls):
        """页面基础配置"""
        st.set_page_config(
            page_title=cls.PAGE_TITLE,
            layout="wide",
        )

    @classmethod
    def _render_sidebar(cls):
        """侧边栏品牌信息 + 导航"""
        st.sidebar.markdown(
            """
            <div style="padding:14px 4px 22px 4px;border-bottom:1px solid rgba(255,255,255,0.08);
                        margin-bottom:18px;">
                <div style="font-size:21px;font-weight:700;color:#fff;
                            letter-spacing:2px;">数治乡音</div>
                <div style="font-size:11px;color:#94a3b8;margin-top:6px;
                            letter-spacing:1px;">VILLAGE GOVERNANCE</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        cls.current_page = st.sidebar.radio(
            "导航",
            list(cls.PAGES.keys()),
            label_visibility="collapsed",
        )

        st.sidebar.markdown(
            """
            <div style="position:fixed;bottom:20px;left:20px;
                        font-size:11px;color:#64748b;line-height:1.7;">
                <b style="color:#94a3b8;">团队信息</b><br>
                数治乡音 · 智汇乡村<br>
                2026 年暑期社会实践
            </div>
            """,
            unsafe_allow_html=True,
        )

    @classmethod
    def _dispatch(cls):
        """按导航选项调用对应页面"""
        page_class = cls.PAGES.get(cls.current_page)
        if page_class:
            page_class.render()


if __name__ == "__main__":
    App.run()
