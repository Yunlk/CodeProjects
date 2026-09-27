#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
行旅识景 Qt 版自检

验证两件事：
  1. 内嵌 Chromium 里的**原版网页**确实按原样跑起来了 —— 用 runJavaScript 探测
     canvas / WebGL / CSS2D 点位数量 / 打卡卡片 / 浮动聊天窗等真实 DOM 元素。
  2. 原生视图可用 —— 图鉴列表与打卡持久化、AI 问答（走 /api/chat）、
     识别页模型状态与特征自检、环境体检面板。

运行：
    cd Application/TripScape
    python selftest_qt.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import gui_qt

FAILS: list[str] = []
T0 = time.time()


def log(message: str):
    print(f"[{time.time() - T0:6.1f}s] {message}", flush=True)


def check(cond, label):
    print(f"{'PASS' if cond else 'FAIL'}  {label}", flush=True)
    if not cond:
        FAILS.append(label)


def pump(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def pump_until(predicate, timeout_s: float, step_ms: int = 200) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if predicate():
            return True
        pump(step_ms)
    return predicate()


def main() -> int:
    app = QApplication(sys.argv)

    log("预加载重依赖 …")
    gui_qt.core.preload_heavy_imports()

    log("启动内嵌 Flask（原版 app.py，进程内）…")
    api = gui_qt.HttpApi()
    api.start()

    window = gui_qt.MainWindow(api)
    window.show()

    log("等待内嵌 Flask 就绪 …")
    ready = pump_until(lambda: api.loaded and api.wait_ready(0.3), 240)
    log(f"后端：ready={ready} url={api.url} error={api.error}")
    check(ready, f"内嵌 Flask 就绪：{api.url}")
    check(window.statusBar().currentMessage().startswith("内嵌 Flask 已就绪"),
          f"状态栏：{window.statusBar().currentMessage()[:48]}")

    print("\n" + "-" * 70)
    print("原生视图")
    print("-" * 70)
    check(window.nav.count() == len(gui_qt.NAV_ITEMS), f"侧边栏 {window.nav.count()} 个页签")
    check(window.stack.count() == len(gui_qt.NAV_ITEMS), f"页面堆栈 {window.stack.count()} 个")

    gallery = window.gallery
    window.nav.setCurrentRow(1)  # 先切到图鉴页，隐藏的页面不会触发 paintEvent
    pump(300)
    check(gallery.list_widget.count() == 26, f"地标列表 {gallery.list_widget.count()} 项")
    check("0/26" in gallery.progress_label.text(), f"进度：{gallery.progress_label.text()}")

    gallery.search.setText("塔")
    pump(150)
    filtered = gallery.list_widget.count()
    check(0 < filtered < 26, f"搜索过滤（'塔' → {filtered} 项）")
    gallery.search.setText("")
    pump(150)
    check(gallery.list_widget.count() == 26, "清空搜索恢复 26 项")

    gallery.map.refresh()
    pump(300)
    check(len(gallery.map.markers) == 26, f"地图点位 {len(gallery.map.markers)} 个")

    gallery.select_target("TajMahal")
    pump(120)
    check("泰姬陵" in gallery.detail_name.text(), f"详情：{gallery.detail_name.text()}")
    expected = len(gallery.store.by_target["TajMahal"]["entries"])
    check(gallery.question_list.count() == expected, f"问答条目 {gallery.question_list.count()}/{expected}")
    gallery.question_list.setCurrentRow(0)
    pump(80)
    check("泰姬陵" in gallery.answer.toPlainText(), "点击问题后显示答案")

    window.checked.clear()
    gui_qt.core.save_checkins(window.checked)
    gallery.toggle_checkin()
    saved = json.loads((ROOT / "data" / "checkins.json").read_text(encoding="utf-8"))
    check("TajMahal" in saved.get("checked", {}), "打卡持久化到 data/checkins.json")
    check("1/26" in gallery.progress_label.text(), f"进度更新：{gallery.progress_label.text()}")
    gallery.toggle_checkin()
    check("0/26" in gallery.progress_label.text(), "取消打卡后进度归零")

    print("\n" + "-" * 70)
    print("网页版原貌（内嵌 Chromium 里的真实 DOM）")
    print("-" * 70)
    # 必须让网页页签可见：QWebEngineView 隐藏时 requestAnimationFrame 不跑，
    # CSS2DRenderer 就不会把点位挂进 DOM（真实使用时切回来即恢复）。
    window.nav.setCurrentRow(0)
    pump(400)

    loaded = {"ok": False, "count": 0}

    def on_load(ok):
        loaded["ok"] = ok
        loaded["count"] += 1

    window.web_page.view.loadFinished.connect(on_load)
    window.web_page.load_app()
    check(pump_until(lambda: loaded["ok"], 90), "GET /app 加载完成")
    pump(2500)  # 等 Three.js 模块与贴图就绪

    probed: dict = {}

    def probe_page():
        probed.clear()
        window.web_page.probe(lambda data: probed.update(payload=data))
        return pump_until(lambda: "payload" in probed, 15)

    def marker_count():
        try:
            return json.loads(probed.get("payload") or "{}").get("markers", 0)
        except Exception:
            return 0

    check(probe_page(), "runJavaScript 探针返回")
    payload = json.loads(probed.get("payload") or "{}")
    log("页面探针：" + json.dumps(payload, ensure_ascii=False))
    check(payload.get("title", "").startswith("行旅识景"), f"页面标题：{payload.get('title')}")
    check(payload.get("canvas"), "Three.js canvas 已创建")
    check(payload.get("webgl"), "WebGL 上下文可用（地球能渲染）")
    check(payload.get("chatFloat"), "浮动聊天窗 #chatFloat 存在")
    check(payload.get("spotCard"), "打卡卡片 #spotCard 存在")

    # 点位随地球自转陆续挂进 DOM，轮询到出现为止
    end = time.time() + 30
    while marker_count() == 0 and time.time() < end:
        pump(1000)
        probe_page()
    check(marker_count() > 0, f"CSS2D 点击点位已挂载（当前可见 {marker_count()} 个）")

    # 注意：runJavaScript 不会等待 Promise，这里用同步 XHR 直接拿返回值
    csv_rows: dict = {}
    window.web_page.view.page().runJavaScript(
        "(() => { const x = new XMLHttpRequest();"
        " x.open('GET', 'static/spots.csv', false); x.send();"
        " return x.responseText.trim().split(/\\r?\\n/).filter(Boolean).length - 1; })()",
        lambda value: csv_rows.update(rows=value),
    )
    check(pump_until(lambda: "rows" in csv_rows, 20), "页面上下文读取 spots.csv")
    check(csv_rows.get("rows") == 26, f"spots.csv 数据行 {csv_rows.get('rows')} 条（应为 26）")

    print("\n" + "-" * 70)
    print("原生问答 / 识别 / 体检")
    print("-" * 70)
    chat = window.chat
    window.nav.setCurrentRow(2)
    pump(100)
    chat.quick_ask("埃菲尔铁塔在哪里？")
    check(pump_until(lambda: not chat.busy, 90), "问答请求完成")
    answer = chat.view.toPlainText()
    check("巴黎" in answer, f"答案含「巴黎」：…{answer[-90:].replace(chr(10), ' ')}")

    chat.quick_ask("故宫在哪里？")
    check(pump_until(lambda: not chat.busy, 90), "超纲提问完成")
    check("暂时没有找到" in chat.view.toPlainText() or "布达拉宫" in chat.view.toPlainText(),
          "超纲问题返回内容（原版行为：可能命中近似问题）")

    recognize = window.recognize
    check(len(recognize.missing_models()) == 5, f"缺失模型 {len(recognize.missing_models())} 个")
    check(not recognize.recognize_btn.isEnabled(), "缺模型时识别按钮禁用")
    recognize.image_path = str(ROOT / "static" / "earth.png")
    recognize.feature_probe()
    check(pump_until(lambda: "总维度" in recognize.result.toPlainText(), 120), "特征自检完成")
    log("特征自检：" + recognize.result.toPlainText().replace("\n", " | ")[:160])

    env = window.env
    env.refresh()
    status = env.status.toPlainText()
    check("models/bge-small-zh" in status and "知识条目" in status, "环境页状态文本完整")
    check(api.url in status, "环境页显示内嵌 Flask 地址")

    for row in range(len(gui_qt.NAV_ITEMS)):
        window.nav.setCurrentRow(row)
        pump(120)
        check(window.stack.currentIndex() == row, f"切换到页签 {row}（{gui_qt.NAV_ITEMS[row].strip()}）")

    window.checked.clear()
    gui_qt.core.save_checkins(window.checked)
    window.close()
    app.quit()

    print()
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败")
        for item in FAILS:
            print(f"  - {item}")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
