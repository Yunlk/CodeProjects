#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
行旅识景桌面版 · 自检脚本

覆盖两块内容：
    1. 数据与问答：知识条目归组完整性、RAG 后端、BM25 降级后端、超纲问题护栏。
    2. 界面控件树：视图构建、地标列表、地图绘制、打卡持久化、识别/环境面板状态。

因为没有截图比对，界面部分直接断言控件状态（按钮是否禁用、标签文案、消息气泡内容）。
运行：
    cd Application/TripScape
    python selftest.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import gui

# 问题 → 期望是否命中知识库
EXPECT = {
    "介绍一下天坛": True,
    "埃菲尔铁塔在哪里？": True,
    "长城是世界遗产吗？": True,
    "阿布辛贝神庙为什么被搬迁？": True,
    "天坛用途是什么？它在哪里？": True,
    "泰姬陵是谁建造的？": True,
    "吉萨金字塔": True,
    "基督像有多高？": True,
    "崇圣寺三塔有多高？": True,
    "库库尔坎金字塔是什么？": True,
    "布达拉宫建于什么时候？": True,
    "今天天气怎么样？": False,
    "故宫在哪里？": False,
    "故宫建于什么时候？": False,
    "你会写代码吗？": False,
}
UNRELATED = {"今天天气怎么样？", "故宫在哪里？", "故宫建于什么时候？", "你会写代码吗？"}

T0 = time.time()
FAILS: list[str] = []


def log(message: str):
    print(f"[{time.time() - T0:6.1f}s] {message}", flush=True)


def check(cond, label):
    print(f"{'PASS' if cond else 'FAIL'}  {label}", flush=True)
    if not cond:
        FAILS.append(label)


def text_of(widget):
    try:
        return str(widget.cget("text"))
    except Exception:
        return str(getattr(widget, "_text", ""))


def state_of(widget):
    try:
        return str(widget.cget("state"))
    except Exception:
        return str(getattr(widget, "_state", ""))


def matched_questions(answer: str) -> str:
    hits = [
        line.replace("匹配问题：", "")
        for line in (answer or "").splitlines()
        if line.startswith("匹配问题：")
    ]
    return " / ".join(hits) if hits else "(兜底/无匹配)"


def main() -> int:
    log("主线程预加载重依赖 …")
    preload = gui.preload_heavy_imports()
    log("预加载: " + "; ".join(f"{k}={v}" for k, v in preload.items() if not v.startswith("OK")) or "全部 OK")

    print("\n" + "=" * 72)
    print("界面自检")
    print("=" * 72)
    log("构建 app …")
    app = gui.TripScapeApp()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.update()
            time.sleep(0.05)

    def pump_until(predicate, timeout):
        end = time.time() + timeout
        while time.time() < end:
            app.update()
            if predicate():
                return True
            time.sleep(0.1)
        return predicate()

    # ---- 视图与数据 -------------------------------------------------------
    check(set(app.views) == {"gallery", "chat", "recognize", "env"}, "四个视图构建成功")

    gallery = app.views["gallery"]
    check(
        len(gallery.list_frame.winfo_children()) == 26,
        f"地标列表 26 项（实际 {len(gallery.list_frame.winfo_children())}）",
    )
    check(
        text_of(gallery.progress_label).startswith("已打卡"),
        f"进度标签：{text_of(gallery.progress_label)}",
    )
    gallery.search.insert(0, "塔")
    gallery.on_search()
    filtered = len(gallery.list_frame.winfo_children())
    check(0 < filtered < 26, f"搜索过滤生效（'塔' → {filtered} 项）")
    gallery.search.delete(0, "end")
    gallery.on_search()
    check(len(gallery.list_frame.winfo_children()) == 26, "清空搜索后恢复 26 项")

    # ---- 地图渲染 ---------------------------------------------------------
    pump_until(lambda: gallery.earth is not None, 20)
    pump(1.5)
    gallery.render_map()
    pump(0.5)
    check(gallery.earth is not None, "地球底图加载完成")
    check(
        len(gallery.canvas.find_all()) > 26 * 2,
        f"地图绘制元素 {len(gallery.canvas.find_all())} 个（含 26 个点位）",
    )

    # ---- 打卡与持久化 -----------------------------------------------------
    app.checked = {}
    gallery.checked = {}
    gallery.select(app.store.by_target["TajMahal"])
    check("泰姬陵" in text_of(gallery.detail_name), f"详情面板：{text_of(gallery.detail_name)}")
    check(
        len(gallery.q_frame.winfo_children())
        == len(app.store.by_target["TajMahal"]["entries"]),
        "详情页问答条目数与数据库一致",
    )
    gallery.q_frame.winfo_children()[0]._command()
    pump(0.2)
    check("泰姬陵" in gallery.answer_box.get("1.0", "end"), "点击问题后答案框出现内容")

    gallery.toggle_checkin()
    check("TajMahal" in gallery.checked, "打卡写入内存")
    saved = json.loads(Path("data/checkins.json").read_text(encoding="utf-8"))
    check("TajMahal" in saved.get("checked", {}), "打卡持久化到 data/checkins.json")
    check("1/26" in text_of(gallery.progress_label), f"进度更新：{text_of(gallery.progress_label)}")
    check(gallery.progress.get() > 0, "进度条数值 > 0")
    gallery.toggle_checkin()
    check(gallery.checked == {}, "取消打卡后内存状态复原")
    check("0/26" in text_of(gallery.progress_label), "取消后进度归零")

    # ---- 图像识别视图 -----------------------------------------------------
    recognize = app.views["recognize"]
    recognize.on_show()
    check(len(recognize.missing_models()) == 5, f"缺失模型数 {len(recognize.missing_models())}")
    check(state_of(recognize.recognize_btn) == "disabled", "缺模型时「开始识别」按钮禁用")
    recognize.image_path = str(ROOT / "static" / "earth.png")
    recognize.feature_probe()
    check(pump_until(lambda: not recognize.busy and "总维度" in
                     (recognize.result_frame.winfo_children()[0].cget("text")
                      if recognize.result_frame.winfo_children() else ""), 60),
          "特征自检完成")
    result_text = (
        recognize.result_frame.winfo_children()[0].cget("text")
        if recognize.result_frame.winfo_children() else ""
    )
    check("HOG" in result_text and "总维度" in result_text, "特征自检输出维度信息")

    # ---- 环境体检视图 -----------------------------------------------------
    env = app.views["env"]
    env.refresh()
    check(len(env.dep_frame.winfo_children()) == len(gui.OPTIONAL_DEPS), "依赖行数与检查项一致")
    check(
        len(env.model_frame.winfo_children()) == len(gui.MODEL_NAMES) + 1,
        "模型行数 = 5 个 pkl + 1 个向量模型",
    )
    check("归组" in text_of(env.data_label), "数据面板显示归组统计")

    # ---- 问答后端 ---------------------------------------------------------
    log("等待问答后端 …")
    ready = pump_until(lambda: app.qa_backend is not None, 180)
    log(f"后端：ready={ready} label={app.qa_label}")
    if not ready:
        names = {t.ident: t.name for t in threading.enumerate()}
        for tid, frame in sys._current_frames().items():
            log(f"--- thread {tid} ({names.get(tid, '?')}) ---")
            traceback.print_stack(frame)
    check(ready, f"问答后端就绪（{app.qa_label}）")
    check(app.qa_backend is not None and "RAG" in app.qa_label, f"优先使用 RAG：{app.qa_label}")

    chat = app.views["chat"]
    before = len(chat.messages.winfo_children())
    chat.quick_ask("介绍一下天坛")
    check(pump_until(lambda: not chat.busy, 30), "正常提问完成")
    pump(0.3)
    chat.quick_ask("故宫在哪里？")
    check(pump_until(lambda: not chat.busy, 30), "超纲提问完成")
    pump(0.3)
    bubbles = chat.messages.winfo_children()
    check(len(bubbles) >= before + 4, f"消息气泡数 {len(bubbles)}")
    last_text = " ".join(
        text_of(child) for row in bubbles[-1].winfo_children() for child in row.winfo_children()
    )
    check("暂时没有找到" in last_text, f"超纲问题走兜底：{last_text[:60]}")

    for key in ("chat", "recognize", "env", "gallery"):
        app.show_view(key)
        pump(0.2)
        check(app.current == key, f"切换到 {key} 视图")

    # ---- 问答正确性（复用已就绪的后端，不再重复加载模型）-------------------
    print("\n" + "=" * 72)
    print("问答正确性")
    print("=" * 72)
    store = app.store
    grouped = store.grouped_total
    check(
        grouped == store.kb_total and not store.unmatched,
        f"知识条目归组完整：{grouped}/{store.kb_total}，未归组 {len(store.unmatched)}",
    )
    check(
        all(item["entries"] for item in store.landmarks),
        "26 个地标都有知识条目",
    )

    rag = app.qa_backend
    bm = gui.Bm25Backend(
        [entry for item in store.landmarks for entry in item["entries"]],
        store.by_name,
        store.alias_pairs,
    )

    for name, backend in (("RAG+护栏", rag), ("BM25 降级", bm)):
        passed = 0
        for question, expect in EXPECT.items():
            result = backend.ask(question)
            ok = result["hit"] is expect
            passed += 1 if ok else 0
            if not ok:
                print(
                    f"  ❌ [{name}] {question} → 期望命中={expect} 实际={result['hit']}"
                    f" 匹配={matched_questions(result['answer'])}",
                    flush=True,
                )
        check(passed == len(EXPECT), f"{name} 判定 {passed}/{len(EXPECT)} 符合预期")

    for question in sorted(UNRELATED):
        answer = bm.ask(question)["answer"]
        if "暂时没有找到" not in answer:
            check(False, f"BM25 超纲问题应兜底：{question}")
    check(True, "BM25 超纲问题全部走兜底文案")

    app.destroy()

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
