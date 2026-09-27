#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
行旅识景 · 桌面版 GUI（PySide6 + 内嵌 Chromium）

一个窗口里同时装下两套东西：

  1. 🌍 网页版原貌 —— templates/index.html 与 templates/app.html **原封不动**，
     由内嵌的 QtWebEngine(Chromium) 加载本进程内启动的原版 Flask 服务。
     Three.js 3D 地球、OrbitControls 旋转缩放、点击光点打卡、点击聚焦动画、
     粒子星空、浮动聊天窗、置信度进度条……全部按原样运行，HTML/JS/CSS 一行未改。

  2. 原生视图 —— 网页版没有的能力，沿用桌面版实现：
     地标图鉴（地图打卡 + 离线浏览知识库）、AI 问答、图像识别、环境体检。

后端只有一个实例：原生「AI 问答」「图像识别」直接调用内嵌 Flask 的
/api/chat、/api/recognize，避免重复加载句向量模型（约 1.5GB）。

运行：
    cd Application/TripScape
    python gui_qt.py

自检：
    python selftest_qt.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    os.chdir(ROOT)
except OSError:
    pass
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView  # 必须在 QApplication 之前导入
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplashScreen,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import gui as core  # 复用与界面无关的逻辑：数据层 / BM25 / 问答后端 / 预加载 / 打卡存储

APP_TITLE = "行旅识景 · 地标智能识别（桌面版 · 内嵌原版网页）"
FONT = "Microsoft YaHei UI"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

NAV_ITEMS = [
    "🌍  网页版原貌",
    "🗺  地标图鉴",
    "💬  AI 问答",
    "📷  图像识别",
    "⚙️  环境体检",
]

QSS = """
QWidget { background: #0f172a; color: #d6e2f5;
          font-family: "Microsoft YaHei UI"; font-size: 13px; }
QListWidget#nav { background: #0b1324; border: none; padding: 10px 8px; font-size: 14px; }
QListWidget#nav::item { height: 40px; padding-left: 10px; border-radius: 8px; color: #c9d6ea; }
QListWidget#nav::item:selected { background: #2f6fed; color: #ffffff; }
QListWidget#nav::item:hover { background: #1b2947; }
QPushButton { background: #1d2b4a; border: none; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #27395f; }
QPushButton:disabled { color: #5b6b86; }
QPushButton#primary { background: #2f6fed; color: #ffffff; font-weight: bold; }
QPushButton#primary:hover { background: #1f5bd0; }
QPushButton#checkin { background: #2f6fed; color: #ffffff; font-weight: bold; }
QPushButton#checked { background: #7a5a12; color: #ffffff; font-weight: bold; }
QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget#plain {
    background: #16213a; border: 1px solid #22314f; border-radius: 8px; padding: 4px; }
QProgressBar { background: #16213a; border: none; border-radius: 5px; max-height: 10px; }
QProgressBar::chunk { background: #f39c12; border-radius: 5px; }
QStatusBar { background: #0b1324; color: #8ba0bf; }
QLabel#muted { color: #8ba0bf; }
QLabel#title { font-size: 19px; font-weight: bold; }
QLabel#map { background: #0a1122; border: 1px solid #22314f; border-radius: 10px; }
QSplitter::handle { background: #16213a; }
"""


# ----------------------------------------------------------------------------
# 进程内 Flask 后端
# ----------------------------------------------------------------------------
def _pick_port(preferred: int = 5000) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
        except OSError:
            continue
    return preferred


class HttpApi:
    """在后台线程里跑原版 Flask（app.py），并封装原生视图要用的 HTTP 调用。"""

    def __init__(self, preferred_port: int = 5000):
        self.preferred_port = preferred_port
        self.port = None
        self.error = None
        self.loaded = False
        self.server = None

    # -- 生命周期 ------------------------------------------------------------
    def start(self):
        self.port = _pick_port(self.preferred_port)
        threading.Thread(target=self._serve, daemon=True, name="tripscape-flask").start()

    def _serve(self):
        try:
            # 不能 `import app`：同目录的 app/ 包会优先匹配，拿到的是包而不是 app.py
            spec = importlib.util.spec_from_file_location("tripscape_web", ROOT / "app.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # 这里加载 QASystem + ImageRecognizer
            self.loaded = True
            # 用 make_server 而不是 app.run：拿到 server 对象才能在关窗时优雅停掉，
            # 也顺带避开 debug 重载器（它会再 fork 一份模型，约 1.5GB）
            from werkzeug.serving import make_server

            self.server = make_server(
                "127.0.0.1", self.port, module.app, threaded=True
            )
            self.server.serve_forever()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def stop(self):
        """优雅关闭内嵌 Flask。"""
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}" if self.port else ""

    def wait_ready(self, timeout: float = 1.0) -> bool:
        """轮询直到 HTTP 可访问；timeout 到点返回 False。"""
        if self.error or not self.port:
            return False
        end = time.time() + timeout
        while True:
            try:
                with urllib.request.urlopen(self.url + "/", timeout=1.5) as resp:
                    return resp.status == 200
            except Exception:
                if time.time() >= end:
                    return False
                time.sleep(0.25)

    # -- HTTP 封装 -----------------------------------------------------------
    def chat(self, question: str, timeout: float = 120) -> dict:
        payload = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url + "/api/chat", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def recognize(self, image_path: str, timeout: float = 600) -> dict:
        content = Path(image_path).read_bytes()
        boundary = "----TripScapeQtBoundary"
        filename = Path(image_path).name
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            self.url + "/api/recognize", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8", "replace"))


class ApiTask(QThread):
    """把耗时调用（HTTP / 特征提取）丢到线程里，避免卡住界面。"""

    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.ok.emit(self.fn())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------------
# 视图 1：网页版原貌（内嵌 Chromium）
# ----------------------------------------------------------------------------
class WebPage(QWidget):
    """直接用 QWebEngineView 打开原版页面，HTML/JS/CSS 不做任何改动。"""

    def __init__(self, api: HttpApi, parent=None):
        super().__init__(parent)
        self.api = api

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self.home_btn = QPushButton("入口页  /")
        self.app_btn = QPushButton("应用页  /app")
        self.reload_btn = QPushButton("刷新")
        self.external_btn = QPushButton("在系统浏览器打开")
        self.url_label = QLabel("等待后端…")
        self.url_label.setObjectName("muted")
        for widget in (self.home_btn, self.app_btn, self.reload_btn, self.external_btn):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.url_label)
        layout.addLayout(bar)

        self.placeholder = QLabel(
            "正在启动内嵌 Flask 后端…\n\n"
            "启动时会加载问答引擎与 bge-small-zh 句向量模型（首次约 15 秒）。\n"
            "页面本身不做任何改动：Three.js 3D 地球、点击光点打卡、\n"
            "点击聚焦、粒子星空、浮动聊天窗都按原样运行。"
        )
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("muted")

        self.view = QWebEngineView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.view)
        layout.addWidget(self.stack, 1)

        self.home_btn.clicked.connect(self.load_home)
        self.app_btn.clicked.connect(self.load_app)
        self.reload_btn.clicked.connect(self.view.reload)
        self.external_btn.clicked.connect(self._open_external)
        self._reload = True

    def load_home(self):
        if not self.api.url:
            return
        self.stack.setCurrentWidget(self.view)
        self.view.load(QUrl(self.api.url + "/"))

    def load_app(self):
        if not self.api.url:
            return
        self.stack.setCurrentWidget(self.view)
        self.view.load(QUrl(self.api.url + "/app"))

    def reload(self):
        self.view.reload()

    def _open_external(self):
        if self.api.url:
            os.startfile(self.api.url)  # noqa: S606 - Windows 专用

    def set_ready(self, url: str, loaded: bool):
        self.url_label.setText(url if not loaded else url + "  ·  来自 app.py")
        if self.stack.currentWidget() is self.placeholder:
            self.load_home()

    def probe(self, callback):
        """探测原版页面里的关键元素是否就位（自检用）。"""
        script = """
        (() => {
            const canvas = document.querySelector('canvas');
            const gl = canvas && (canvas.getContext('webgl2') || canvas.getContext('webgl'));
            return JSON.stringify({
                title: document.title,
                canvas: !!canvas,
                webgl: !!gl,
                chatFloat: !!document.getElementById('chatFloat'),
                spotCard: !!document.getElementById('spotCard'),
                markers: document.querySelectorAll('div[data-index]').length,
                cards: document.querySelectorAll('.col').length,
                csrf: typeof window.targetCamPos
            });
        })()
        """
        self.view.page().runJavaScript(script, callback)


# ----------------------------------------------------------------------------
# 视图 2：地标图鉴
# ----------------------------------------------------------------------------
class MapCanvas(QWidget):
    """把 earth.png 按等距圆柱投影铺开，并按经纬度打点；点击选中地标。"""

    spotClicked = Signal(str)

    def __init__(self, store, checked: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.checked = checked
        self.selected = None
        self.markers = []  # [(target_id, x, y)]
        self.pixmap = QPixmap(str(core.EARTH_PNG)) if Path(core.EARTH_PNG).exists() else QPixmap()
        self.setMinimumSize(430, 280)
        self.setMouseTracking(True)

    def _project(self):
        """返回 (图像区域, 宽, 高, 偏移x, 偏移y)。"""
        if self.pixmap.isNull() or self.width() <= 1 or self.height() <= 1:
            return None
        scaled = self.pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        ox = (self.width() - scaled.width()) // 2
        oy = (self.height() - scaled.height()) // 2
        return scaled, scaled.width(), scaled.height(), ox, oy

    def _layout_markers(self, width: int, height: int, ox: int, oy: int):
        """按经纬度等距圆柱投影算出每个点位的像素坐标（与绘制解耦，便于自检）。"""
        self.markers = []
        for item in self.store.landmarks:
            if item["lat"] is None or item["lon"] is None:
                continue
            x = ox + (item["lon"] + 180.0) / 360.0 * width
            y = oy + (90.0 - item["lat"]) / 180.0 * height
            self.markers.append((item["target_id"], x, y))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#0a1122"))
            projected = self._project()
            if projected is None:
                painter.setPen(QColor("#8ba0bf"))
                painter.drawText(self.rect(), Qt.AlignCenter, "缺少 static/earth.png 或尺寸过小")
                return

            scaled, width, height, ox, oy = projected
            painter.drawPixmap(ox, oy, scaled)
            self._layout_markers(width, height, ox, oy)

            for target_id, x, y in self.markers:
                is_checked = target_id in self.checked
                color = QColor("#f39c12" if is_checked else "#2f6fed")
                radius = 6 if is_checked else 5
                painter.setPen(QPen(color, 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPoint(int(x), int(y)), radius + 4, radius + 4)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(QPoint(int(x), int(y)), radius, radius)

            if self.selected:
                item = self.store.by_target.get(self.selected)
                point = next((m for m in self.markers if m[0] == self.selected), None)
                if item is not None and point is not None:
                    x, y = point[1], point[2]
                    painter.setPen(QPen(QColor("#ffffff"), 2))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawEllipse(QPoint(int(x), int(y)), 13, 13)
                    painter.setPen(QColor("#ffffff"))
                    painter.setFont(QFont(FONT, 9, QFont.Bold))
                    painter.drawText(
                        QRect(int(x) - 60, int(y) - 34, 120, 18),
                        Qt.AlignCenter,
                        item["name"],
                    )
        finally:
            painter.end()

    def _nearest(self, x: float, y: float, radius: float = 16.0):
        best, best_distance = None, radius
        for target_id, mx, my in self.markers:
            distance = ((mx - x) ** 2 + (my - y) ** 2) ** 0.5
            if distance <= best_distance:
                best, best_distance = target_id, distance
        return best

    def mousePressEvent(self, event):
        target_id = self._nearest(event.position().x(), event.position().y())
        if target_id:
            self.spotClicked.emit(target_id)

    def mouseMoveEvent(self, event):
        hovering = self._nearest(event.position().x(), event.position().y()) is not None
        self.setCursor(Qt.PointingHandCursor if hovering else Qt.ArrowCursor)

    def refresh(self):
        """重算点位并重绘（隐藏状态下 update() 不触发 paintEvent，这里顺手补齐）。"""
        projected = self._project()
        if projected is not None:
            _, width, height, ox, oy = projected
            self._layout_markers(width, height, ox, oy)
        self.update()


class GalleryPage(QWidget):
    """原生图鉴：地图打卡 + 离线浏览知识库（网页版没有的能力）。"""

    def __init__(self, store, checked: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.checked = checked
        self.selected = None
        self.filter_text = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # 左：进度 + 搜索 + 列表
        left = QWidget()
        left.setFixedWidth(258)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("🌍 地标图鉴")
        title.setObjectName("title")
        left_layout.addWidget(title)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress_label = QLabel("已打卡 0/0")
        self.progress_label.setObjectName("muted")
        left_layout.addWidget(self.progress_label)
        left_layout.addWidget(self.progress)
        reset_btn = QPushButton("重置全部打卡")
        reset_btn.clicked.connect(self.reset_checkins)
        left_layout.addWidget(reset_btn)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索地标…")
        self.search.textChanged.connect(self.on_search)
        left_layout.addWidget(self.search)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("plain")
        self.list_widget.currentItemChanged.connect(self._on_list_changed)
        left_layout.addWidget(self.list_widget, 1)
        layout.addWidget(left)

        # 中：地图
        self.map = MapCanvas(store, checked)
        self.map.spotClicked.connect(self.select_target)
        layout.addWidget(self.map, 1)

        # 右：详情
        right = QWidget()
        right.setFixedWidth(392)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_name = QLabel("未选择地标")
        self.detail_name.setObjectName("title")
        self.detail_name.setWordWrap(True)
        self.detail_meta = QLabel("请在地图或左侧列表中选择一个地标")
        self.detail_meta.setObjectName("muted")
        self.detail_meta.setWordWrap(True)
        self.checkin_btn = QPushButton("📍 打卡")
        self.checkin_btn.setObjectName("checkin")
        self.checkin_btn.setEnabled(False)
        self.checkin_btn.clicked.connect(self.toggle_checkin)
        right_layout.addWidget(self.detail_name)
        right_layout.addWidget(self.detail_meta)
        right_layout.addWidget(self.checkin_btn)
        right_layout.addWidget(QLabel("知识库问答条目（点击查看答案）"))
        self.question_list = QListWidget()
        self.question_list.setObjectName("plain")
        self.question_list.setMaximumHeight(170)
        self.question_list.currentRowChanged.connect(self.show_answer)
        right_layout.addWidget(self.question_list)
        self.answer = QTextBrowser()
        self.answer.setPlainText("选择上方问题即可查看知识库原文。")
        right_layout.addWidget(self.answer, 1)
        layout.addWidget(right)

        self.refresh_list()
        self.update_progress()

    # -- 列表 / 进度 ---------------------------------------------------------
    def refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for item in self.store.landmarks:
            if self.filter_text and self.filter_text not in item["name"] \
                    and self.filter_text.lower() not in item["target_id"].lower():
                continue
            mark = "✅" if item["target_id"] in self.checked else "📍"
            entry = QListWidgetItem(f"{mark} {item['name']}")
            entry.setData(Qt.UserRole, item["target_id"])
            self.list_widget.addItem(entry)
        self.list_widget.blockSignals(False)
        if self.selected:
            self._highlight_selected()

    def _highlight_selected(self):
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.UserRole) == self.selected:
                self.list_widget.setCurrentRow(row)
                return

    def _on_list_changed(self, current, _previous):
        if current is not None:
            self.select_target(current.data(Qt.UserRole))

    def on_search(self, text):
        self.filter_text = text.strip()
        self.refresh_list()

    def update_progress(self):
        total = len(self.store.landmarks)
        done = sum(1 for item in self.store.landmarks if item["target_id"] in self.checked)
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.progress_label.setText(
            f"已打卡 {done}/{total}（{done / total * 100:.0f}%）" if total else "无数据"
        )

    # -- 详情 ---------------------------------------------------------------
    def select_target(self, target_id: str):
        item = self.store.by_target.get(target_id)
        if not item:
            return
        self.selected = target_id
        self.map.selected = target_id
        self.map.refresh()
        self._highlight_selected()

        checked = target_id in self.checked
        lat = f"{item['lat']:.3f}" if item["lat"] is not None else "无"
        lon = f"{item['lon']:.3f}" if item["lon"] is not None else "无"
        self.detail_name.setText(item["name"])
        stamp = self.checked.get(target_id, "")
        self.detail_meta.setText(
            f"标识：{item['target_id']}\n坐标：纬度 {lat} · 经度 {lon}\n"
            f"知识条目：{len(item['entries'])} 条\n"
            f"状态：{'✅ 已打卡 · ' + stamp[:19] if checked else '📍 未打卡'}"
        )
        self.checkin_btn.setEnabled(True)
        self.checkin_btn.setText("↩️ 取消打卡" if checked else "📍 打卡")
        self.checkin_btn.setObjectName("checked" if checked else "checkin")
        self.checkin_btn.setStyleSheet(
            "background:#7a5a12;color:#fff;font-weight:bold;border-radius:8px;padding:7px 14px;"
            if checked else
            "background:#2f6fed;color:#fff;font-weight:bold;border-radius:8px;padding:7px 14px;"
        )

        self.question_list.clear()
        for entry in item["entries"]:
            self.question_list.addItem(entry["question"])
        if not item["entries"]:
            self.question_list.addItem("（该地标暂无知识库条目）")
        self.answer.setPlainText("选择上方问题即可查看知识库原文。")

    def show_answer(self, row: int):
        if not self.selected or row < 0:
            return
        item = self.store.by_target[self.selected]
        if row >= len(item["entries"]):
            return
        entry = item["entries"][row]
        self.answer.setPlainText(
            f"问题：{entry['question']}\n关键词：{entry['keywords']}\n\n{entry['answer']}"
        )

    # -- 打卡 ---------------------------------------------------------------
    def toggle_checkin(self):
        if not self.selected:
            return
        if self.selected in self.checked:
            del self.checked[self.selected]
        else:
            self.checked[self.selected] = time.strftime("%Y-%m-%dT%H:%M:%S")
        core.save_checkins(self.checked)
        self.refresh_list()
        self.update_progress()
        self.map.refresh()
        self.select_target(self.selected)

    def reset_checkins(self):
        if not self.checked:
            return
        if QMessageBox.question(self, "重置打卡", "确定清空全部打卡记录吗？") != QMessageBox.Yes:
            return
        self.checked.clear()
        core.save_checkins(self.checked)
        self.refresh_list()
        self.update_progress()
        self.map.refresh()
        if self.selected:
            self.select_target(self.selected)


# ----------------------------------------------------------------------------
# 视图 3：AI 问答（走内嵌 Flask 的 /api/chat，与网页版共用同一个模型实例）
# ----------------------------------------------------------------------------
class ChatPage(QWidget):
    BUBBLE_CSS = """
    <style>
      body { background:#0f172a; color:#d6e2f5; font-family:"Microsoft YaHei UI"; font-size:13px; }
      .row { margin:6px 0; }
      .bubble { display:inline-block; padding:10px 14px; border-radius:12px;
                max-width:640px; white-space:pre-wrap; }
      .user { text-align:right; }
      .user .bubble { background:#2f6fed; color:#ffffff; }
      .bot .bubble { background:#1d2b4a; }
      .error .bubble { background:#5c1f1f; }
    </style>
    """

    def __init__(self, api: HttpApi, parent=None):
        super().__init__(parent)
        self.api = api
        self.messages = []
        self.busy = False
        self._task = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("💬 AI 问答")
        title.setObjectName("title")
        self.backend_label = QLabel("检索后端：等待内嵌 Flask 就绪…")
        self.backend_label.setObjectName("muted")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(self.backend_label)
        head.addStretch(1)
        layout.addLayout(head)

        self.view = QTextBrowser()
        layout.addWidget(self.view, 1)

        chips = QHBoxLayout()
        for text in ("介绍一下天坛", "埃菲尔铁塔在哪里？", "长城是世界遗产吗？"):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, t=text: self.quick_ask(t))
            chips.addWidget(button)
        chips.addStretch(1)
        layout.addLayout(chips)

        bottom = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setFixedHeight(78)
        self.input.setPlaceholderText("输入问题（Ctrl+Enter 发送）")
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("primary")
        self.send_btn.setFixedWidth(96)
        self.send_btn.setFixedHeight(78)
        self.send_btn.clicked.connect(self.send)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_btn)
        layout.addLayout(bottom)

        self.messages.append(
            ("bot", "你好，我是行旅识景的本地知识库助手。可以问我 26 座世界地标的位置、"
                    "高度、建造者、历史意义等问题。")
        )
        self._render()

    # -- 渲染 ---------------------------------------------------------------
    def _render(self):
        rows = []
        for role, text in self.messages:
            cls = "user" if role == "user" else ("error" if role == "error" else "bot")
            rows.append(
                f'<div class="row {cls}"><div class="bubble">{escape(text).replace(chr(10), "<br>")}</div></div>'
            )
        self.view.setHtml(self.BUBBLE_CSS + "".join(rows))
        scrollbar = self.view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # -- 交互 ---------------------------------------------------------------
    def quick_ask(self, text: str):
        self.input.setPlainText(text)
        self.send()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.send()
            return
        super().keyPressEvent(event)

    def send(self):
        if self.busy:
            return
        question = self.input.toPlainText().strip()
        if not question:
            return
        self.input.clear()
        self.messages.append(("user", question))
        self.messages.append(("bot", "正在检索本地知识库…"))
        self._render()
        self.busy = True
        self.send_btn.setEnabled(False)

        started = time.time()
        task = ApiTask(lambda: self.api.chat(question), self)
        task.ok.connect(lambda payload: self._on_answer(payload, time.time() - started))
        task.failed.connect(self._on_error)
        task.start()
        self._task = task

    def _on_answer(self, payload, elapsed):
        answer = payload.get("answer") or payload.get("error") or "未收到有效回答"
        miss = "暂时没有找到" in answer
        self.messages[-1] = (
            "error" if miss else "bot",
            f"{answer}\n\n— 内嵌 Flask /api/chat · {elapsed:.2f}s",
        )
        self.busy = False
        self.send_btn.setEnabled(True)
        self._render()

    def _on_error(self, message):
        self.messages[-1] = ("error", f"请求失败：{message}")
        self.busy = False
        self.send_btn.setEnabled(True)
        self._render()

    def set_ready(self, ready: bool):
        self.backend_label.setText(
            "检索后端：RAG 向量 + BM25 混合检索（内嵌 Flask，与网页版同一实例）"
            if ready else "检索后端：等待内嵌 Flask 就绪…"
        )


# ----------------------------------------------------------------------------
# 视图 4：图像识别
# ----------------------------------------------------------------------------
class RecognizePage(QWidget):
    def __init__(self, api: HttpApi, parent=None):
        super().__init__(parent)
        self.api = api
        self.image_path = None
        self.busy = False
        self._task = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        left = QWidget()
        left.setFixedWidth(430)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("📷 图像识别")
        title.setObjectName("title")
        left_layout.addWidget(title)
        subtitle = QLabel("SIFT+VLAD / HOG / 轮廓 / 颜色矩 / GLCM + 多尺度贝叶斯滑窗")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        left_layout.addWidget(subtitle)

        pick_btn = QPushButton("选择图片（PNG / JPG）")
        pick_btn.setObjectName("primary")
        pick_btn.clicked.connect(self.pick_image)
        left_layout.addWidget(pick_btn)

        self.preview = QLabel("尚未选择图片")
        self.preview.setObjectName("map")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(400, 260)
        left_layout.addWidget(self.preview, 1)

        self.path_label = QLabel("")
        self.path_label.setObjectName("muted")
        self.path_label.setWordWrap(True)
        left_layout.addWidget(self.path_label)

        self.recognize_btn = QPushButton("开始识别（经 /api/recognize）")
        self.recognize_btn.setObjectName("primary")
        self.recognize_btn.setEnabled(False)
        self.recognize_btn.clicked.connect(self.recognize)
        left_layout.addWidget(self.recognize_btn)

        self.feature_btn = QPushButton("特征提取自检（不需模型）")
        self.feature_btn.setEnabled(False)
        self.feature_btn.clicked.connect(self.feature_probe)
        left_layout.addWidget(self.feature_btn)
        layout.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("模型状态"))
        self.model_label = QLabel("")
        self.model_label.setObjectName("muted")
        self.model_label.setWordWrap(True)
        right_layout.addWidget(self.model_label)
        right_layout.addWidget(QLabel("结果"))
        self.result = QTextBrowser()
        self.result.setPlainText("选择图片后可执行识别；模型缺失时可先做特征提取自检。")
        right_layout.addWidget(self.result, 1)
        layout.addWidget(right, 1)

        self.refresh_model_status()

    def missing_models(self):
        return [name for name in core.MODEL_NAMES if not (ROOT / "models" / name).exists()]

    def refresh_model_status(self):
        lines = []
        for name in core.MODEL_NAMES:
            exists = (ROOT / "models" / name).exists()
            lines.append(f"{'✅' if exists else '❌'}  models/{name}")
        missing = self.missing_models()
        self.model_label.setText("\n".join(lines))
        self.recognize_btn.setEnabled(bool(self.image_path) and not missing)
        if missing:
            self.result.setPlainText(
                "⚠️ 识别模型缺失，无法执行图像识别。\n\n"
                "仓库 .gitignore 排除了 models/，需要自行训练：\n"
                "  1) Kaggle 地标数据集放到 data/train、data/valid（26 个类别）\n"
                "  2) python app/train.py\n"
                "  3) 回到本页点「刷新」\n\n"
                f"缺失文件：{', '.join(missing)}"
            )

    def pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择地标图片", str(ROOT), "图片 (*.jpg *.jpeg *.png);;所有文件 (*)"
        )
        if not path:
            return
        self.image_path = path
        size_kb = os.path.getsize(path) / 1024
        self.path_label.setText(f"{path}\n（{size_kb:.0f} KB）")
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview.setText("预览失败")
        else:
            self.preview.setPixmap(
                pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self.feature_btn.setEnabled(True)
        self.refresh_model_status()

    def recognize(self):
        if self.busy or not self.image_path:
            return
        self.busy = True
        self.recognize_btn.setEnabled(False)
        self.result.setPlainText("正在请求内嵌 Flask 的 /api/recognize …")
        path = self.image_path
        started = time.time()
        task = ApiTask(lambda: self.api.recognize(path), self)
        task.ok.connect(lambda payload: self._on_recognized(payload, time.time() - started))
        task.failed.connect(lambda message: self._finish(f"❌ 请求失败：{message}"))
        task.start()
        self._task = task

    def _on_recognized(self, payload, elapsed):
        if payload.get("success"):
            text = (
                f"✅ 识别成功：{payload.get('name')}\n"
                f"标识：{payload.get('target_id')}\n"
                f"置信度：{payload.get('confidence_percent')}\n"
                f"耗时：{elapsed:.2f}s"
            )
        else:
            text = f"❌ 识别失败：{payload.get('message') or payload.get('error')}"
        self._finish(text)

    def _finish(self, text):
        self.busy = False
        self.refresh_model_status()
        self.result.setPlainText(text)

    def feature_probe(self):
        if not self.image_path:
            return
        self.result.setPlainText("正在提取特征…")
        path = self.image_path

        def work():
            if not core.module_available("skimage"):
                raise RuntimeError("缺少 scikit-image，请到「环境体检」安装")
            cv2 = core.ensure_import("cv2")
            core.ensure_import("numpy")
            module = core.ensure_import("app.image_recognizer")
            image = cv2.imread(path)
            if image is None:
                raise RuntimeError("OpenCV 无法读取该图片")
            started = time.time()
            hog = module.extract_spm_hog(image, 256)
            profile = module.extract_profile(image, 20)
            color = module.extract_color_moments(image)
            glcm = module.extract_glcm(image)
            elapsed = time.time() - started
            vlad_dim = 64 * 128
            total = vlad_dim + hog.shape[0] + profile.shape[0] + color.shape[0] + glcm.shape[0]
            return (
                f"图片尺寸：{image.shape[1]}×{image.shape[0]}\n"
                f"HOG（Letterbox 256）：{hog.shape[0]} 维\n"
                f"轴向轮廓：{profile.shape[0]} 维\n"
                f"Lab 颜色矩：{color.shape[0]} 维\n"
                f"GLCM 纹理：{glcm.shape[0]} 维\n"
                f"—— 不含 PCA 的原始总维度：约 {total} 维\n"
                f"HOG PCA(512) 后总维度：约 {vlad_dim + 512 + 20 + 9 + 4} 维\n"
                f"特征提取耗时：{elapsed:.2f}s"
            )

        task = ApiTask(work, self)
        task.ok.connect(lambda text: self.result.setPlainText(text))
        task.failed.connect(lambda message: self.result.setPlainText(f"❌ 特征自检失败：{message}"))
        task.start()
        self._task = task

    def reload_models(self):
        self.refresh_model_status()


# ----------------------------------------------------------------------------
# 视图 5：环境体检
# ----------------------------------------------------------------------------
class EnvPage(QWidget):
    def __init__(self, api: HttpApi, store, checked: dict, parent=None):
        super().__init__(parent)
        self.api = api
        self.store = store
        self.checked = checked
        self.process = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("⚙️ 环境体检")
        title.setObjectName("title")
        layout.addWidget(title)

        buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.clicked.connect(self.refresh)
        self.install_btn = QPushButton("安装缺失依赖（清华源）")
        self.install_btn.clicked.connect(self.install_deps)
        self.model_btn = QPushButton("下载向量模型 bge-small-zh")
        self.model_btn.clicked.connect(self.download_embedding)
        self.open_btn = QPushButton("打开项目目录")
        self.open_btn.clicked.connect(lambda: os.startfile(str(ROOT)))  # noqa: S606
        for button in (self.refresh_btn, self.install_btn, self.model_btn, self.open_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        splitter = QSplitter(Qt.Horizontal)
        self.status = QTextBrowser()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("pip / 下载日志输出…")
        splitter.addWidget(self.status)
        splitter.addWidget(self.log)
        splitter.setSizes([720, 520])
        layout.addWidget(splitter, 1)

        self.refresh()

    def refresh(self):
        lines = ["【依赖】"]
        for module, description in core.OPTIONAL_DEPS:
            ok = core.module_available(module)
            lines.append(
                f"{'✅' if ok else '❌'}  {module}  "
                f"{core.module_version(module) if ok else '未安装'}   — {description}"
            )
        lines.append("")
        lines.append("【模型文件】")
        for name in core.MODEL_NAMES:
            exists = (ROOT / "models" / name).exists()
            lines.append(f"{'✅' if exists else '❌'}  models/{name}")
        embed = Path(core.EMBED_DIR)
        embed_ok = embed.exists() and any(embed.glob("*.json"))
        lines.append(f"{'✅' if embed_ok else '❌'}  models/bge-small-zh（向量模型）")
        lines.append("")
        lines.append("【数据】")
        lines.append(f"地标：{len(self.store.landmarks)} 个")
        lines.append(
            f"知识条目：{self.store.kb_total} 条（已按地标归组 {self.store.grouped_total} 条，"
            f"未归组 {len(self.store.unmatched)} 条）"
        )
        lines.append(f"已打卡：{len(self.checked)} 个")
        lines.append("")
        lines.append("【服务】")
        lines.append(f"内嵌 Flask：{self.api.url or '未启动'}"
                     f"{'（已就绪）' if self.api.loaded else '（加载中）'}")
        lines.append(f"错误：{self.api.error or '无'}")
        lines.append("")
        lines.append("【说明】")
        lines.append("· 网页版原貌由内嵌 Chromium 加载内嵌 Flask 提供，页面文件未做任何修改。")
        lines.append("· 识别需 scikit-image 与 models/ 下 5 个 pkl（被 .gitignore 排除，需 app/train.py 生成）。")
        self.status.setPlainText("\n".join(lines))

    def _append(self, text: str):
        self.log.appendPlainText(text.rstrip())

    def install_deps(self):
        if self.process is not None:
            return
        self._append(f"$ pip install -i {PIP_MIRROR} jieba faiss-cpu sentence-transformers scikit-image")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "-i", PIP_MIRROR,
             "jieba", "faiss-cpu", "sentence-transformers", "scikit-image"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def pump():
            assert self.process is not None
            for line in self.process.stdout:
                self.log.appendPlainText(line.rstrip())
            self.process.wait()
            self.log.appendPlainText(f"[安装结束] 退出码 {self.process.returncode}")
            self.process = None
            QTimer.singleShot(0, self.refresh)

        threading.Thread(target=pump, daemon=True).start()

    def download_embedding(self):
        self._append("$ 下载 BAAI/bge-small-zh-v1.5 → models/bge-small-zh")

        def work():
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            snapshot_download = core.ensure_import("huggingface_hub").snapshot_download
            return snapshot_download("BAAI/bge-small-zh-v1.5", local_dir=str(core.EMBED_DIR))

        task = ApiTask(work, self)
        task.ok.connect(lambda path: (self._append(f"[完成] {path}"), self.refresh()))
        task.failed.connect(lambda message: self._append(f"[下载失败] {message}"))
        task.start()
        self._task = task


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    backend_ready = Signal(bool)

    def __init__(self, api: HttpApi, store=None, checked=None, parent=None):
        super().__init__(parent)
        self.api = api
        self.store = store or core.DataStore().load()
        self.checked = checked if checked is not None else core.load_checkins()

        self.setWindowTitle(APP_TITLE)
        self.resize(1460, 920)
        self.setStyleSheet(QSS)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(196)
        for label in NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 44))
            self.nav.addItem(item)

        self.web_page = WebPage(api)
        self.gallery = GalleryPage(self.store, self.checked)
        self.chat = ChatPage(api)
        self.recognize = RecognizePage(api)
        self.env = EnvPage(api, self.store, self.checked)

        self.stack = QStackedWidget()
        for page in (self.web_page, self.gallery, self.chat, self.recognize, self.env):
            self.stack.addWidget(page)

        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("正在启动内嵌 Flask 后端（加载问答引擎与向量模型）…")
        self.backend_ready.connect(self._on_backend_ready)
        QTimer.singleShot(400, self._poll_backend)
        self._polls = 0

    def _poll_backend(self):
        self._polls += 1
        if self.api.error:
            self.statusBar().showMessage(f"内嵌 Flask 启动失败：{self.api.error}")
            self.web_page.placeholder.setText(f"内嵌 Flask 启动失败：\n\n{self.api.error}")
            return
        if self.api.wait_ready(0.8):
            self.backend_ready.emit(True)
            return
        elapsed = self._polls * 0.8
        self.statusBar().showMessage(
            f"正在启动内嵌 Flask 后端…已等待 {elapsed:.0f}s（首次需加载向量模型）"
        )
        QTimer.singleShot(800, self._poll_backend)

    def _on_backend_ready(self, ready: bool):
        if not ready:
            return
        self.web_page.set_ready(self.api.url, self.api.loaded)
        self.chat.set_ready(True)
        self.statusBar().showMessage(
            f"内嵌 Flask 已就绪：{self.api.url}   ·   地标 {len(self.store.landmarks)} 个 / "
            f"知识条目 {self.store.kb_total} 条   ·   网页版原貌未做任何修改"
        )


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def _make_splash() -> QSplashScreen | None:
    try:
        pixmap = QPixmap(520, 170)
        pixmap.fill(QColor("#0f172a"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont(FONT, 17, QFont.Bold))
        painter.drawText(30, 60, "🌍 行旅识景")
        painter.setPen(QColor("#8ba0bf"))
        painter.setFont(QFont(FONT, 10))
        painter.drawText(30, 92, "内嵌原版网页（Three.js 3D 地球）+ 原生视图")
        painter.end()
        splash = QSplashScreen(pixmap)
        splash.show()
        return splash
    except Exception:
        return None


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TripScape")

    splash = _make_splash()

    def progress(text: str):
        if splash is not None:
            splash.showMessage(
                text, Qt.AlignBottom | Qt.AlignHCenter, QColor("#8ba0bf")
            )
        app.processEvents()

    progress("正在预加载依赖（torch / transformers / faiss …）")
    core.preload_heavy_imports(progress=progress)
    progress("正在启动内嵌 Flask 后端…")

    api = HttpApi()
    api.start()
    app.aboutToQuit.connect(api.stop)  # 关窗时优雅停掉内嵌 Flask

    window = MainWindow(api)
    window.show()
    if splash is not None:
        splash.finish(window)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
