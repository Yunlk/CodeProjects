"""
项目名称: 数溯工忆：工业遗产数字活化
创建时间: 2026-01-12
"""

import base64
import json
import os
import traceback

import cv2
import numpy as np
import requests
from flask import Flask, jsonify, render_template, request


class QASystem:
    def __init__(self):
        self.load_knowledge_base()
        self.api_key = "your_deepseek_api_key"  # 需替换

    def load_knowledge_base(self):
        """加载本地知识库"""
        with open("data/qa.json", "r", encoding="utf-8") as f:
            self.qa_data = json.load(f)

    def keyword_match(self, question):
        """关键词匹配查找答案"""
        question_lower = question.lower()

        for item in self.qa_data:
            # 检查问题是否包含关键词
            for keyword in item["keywords"]:
                if keyword in question_lower:
                    return item["answer"]

            # 直接问题匹配
            if item["question"] in question:
                return item["answer"]

        return None

    def call_deepseek_api(self, question):
        """调用DeepSeek API"""
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 构建系统提示词
        system_prompt = "你是一位辽宁工业历史专家，请用准确、易懂的语言回答问题。如果问题涉及工业遗产，请结合历史背景详细说明。"

        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "max_tokens": 500,
        }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=10)
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except:
            return "抱歉，我现在无法回答这个问题。"

    def get_answer(self, question):
        """获取答案的主函数"""
        # 尝试本地匹配
        local_answer = self.keyword_match(question)
        if local_answer:
            return {"answer": local_answer, "source": "local"}

        # 调用API
        api_answer = self.call_deepseek_api(question)
        return {"answer": api_answer, "source": "api"}


class ImageRecognizer:
    def __init__(self):
        self.target_images = {}
        self.target_info = {}
        self.load_targets()
        self.load_heritage_info()

    def load_targets(self):
        """加载目标图片（保留原始尺寸）"""
        target_dir = "data/target/"
        for filename in os.listdir(target_dir):
            if filename.endswith((".jpg", ".png", ".jpeg")):
                target_id = filename.split(".")[0]
                path = os.path.join(target_dir, filename)

                # 读取图片并转为灰度图
                img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    # 不再统一缩放，直接存储原始尺寸
                    self.target_images[target_id] = img

    def load_heritage_info(self):
        """加载遗产信息"""
        with open("data/heritage_info.json", "r", encoding="utf-8") as f:
            self.target_info = json.load(f)

    def preprocess_image(self, image_data):
        """预处理上传的图片（直方图均衡化，限制最大尺寸）"""
        # 将字节数据转为numpy数组
        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

        if img is None:
            raise ValueError("无法读取图片数据")

        # 限制最大尺寸，避免计算过慢（最长边不超过1200像素）
        h, w = img.shape
        max_dim = 1200
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = cv2.resize(img, (new_w, new_h))

        # 直方图均衡化增强对比度
        img_enhanced = cv2.equalizeHist(img)

        return img_enhanced

    def template_match(self, user_img):
        """多尺度模板匹配算法，返回最佳匹配的模板尺寸"""
        best_match = None
        best_score = 0
        best_location = None
        best_tpl_shape = None  # 记录匹配时模板的实际尺寸 (w, h)

        for target_id, target_img in self.target_images.items():
            h_tpl, w_tpl = target_img.shape

            # 尝试多种缩放比例，覆盖常见的尺寸变化
            scales = [0.3, 0.5, 0.7, 0.9, 1.0, 1.2, 1.5, 2.0]
            for scale in scales:
                new_w = int(w_tpl * scale)
                new_h = int(h_tpl * scale)

                # 缩放后的模板不能大于用户图片
                if new_w > user_img.shape[1] or new_h > user_img.shape[0]:
                    continue

                # 缩放模板
                target_resized = cv2.resize(target_img, (new_w, new_h))

                # 模板匹配
                result = cv2.matchTemplate(
                    user_img, target_resized, cv2.TM_CCOEFF_NORMED
                )
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val > best_score:
                    best_score = max_val
                    best_match = target_id
                    best_location = max_loc
                    best_tpl_shape = (new_w, new_h)

        return best_match, best_score, best_location, best_tpl_shape

    def recognize(self, image_data):
        """识别主函数"""
        try:
            # 预处理
            user_img = self.preprocess_image(image_data)

            # 多尺度模板匹配，获取最佳匹配的模板尺寸
            target_id, score, location, tpl_shape = self.template_match(user_img)

            # 判断是否识别成功（阈值可根据实际测试调整）
            if score > 0.50 and target_id in self.target_info:
                info = self.target_info[target_id]

                # 在原图上标注（传入匹配到的模板尺寸）
                annotated_img = self.annotate_image(
                    image_data, location, tpl_shape, target_id
                )

                return {
                    "success": True,
                    "target_id": target_id,
                    "name": info["name"],
                    "year": info["year"],
                    "description": info["description"],
                    "location": info["location"],
                    "confidence": float(score),
                    "confidence_percent": f"{score * 100:.1f}%",
                    "annotated_image": annotated_img,  # base64编码的标注图
                }
            else:
                return {
                    "success": False,
                    "message": f"未识别到工业遗产（最高相似度：{score:.2%}）",
                    "confidence": float(score),
                }

        except Exception as e:
            return {"success": False, "message": f"识别过程中出错：{str(e)}"}

    def annotate_image(self, image_data, location, tpl_shape, target_id):
        """在识别成功的图片上添加标注，框大小与实际匹配的模板一致"""
        # 解码原始彩色图片
        nparr = np.frombuffer(image_data, np.uint8)
        color_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # 绘制识别框
        x, y = location
        w, h = tpl_shape
        top_left = (x, y)
        bottom_right = (x + w, y + h)

        cv2.rectangle(color_img, top_left, bottom_right, (0, 255, 0), 3)

        # 将图片转为base64编码，便于前端显示
        _, buffer = cv2.imencode(".jpg", color_img)
        img_base64 = base64.b64encode(buffer).decode("utf-8")

        return img_base64


def create_app():
    """创建并配置 Flask 应用"""
    app = Flask(__name__)
    qa_system = QASystem()
    recognizer = ImageRecognizer()

    @app.route("/")
    def index():
        """网站首页"""
        return render_template("index.html")

    @app.route("/chat")
    def chat_page():
        """智能问答页面"""
        return render_template("chat.html")

    @app.route("/recognize")
    def recognize_page():
        """图片识别页面"""
        return render_template("recognize.html")

    @app.route("/api/chat", methods=["POST"])
    def chat():
        """问答API接口"""
        data = request.json
        question = data.get("question", "").strip()

        if not question:
            return jsonify({"error": "问题不能为空"})

        result = qa_system.get_answer(question)
        return jsonify(result)

    @app.route("/api/recognize", methods=["POST"])
    def recognize_image():
        """图片识别API接口"""
        try:
            if "image" not in request.files:
                return jsonify({"error": "请上传图片文件"})

            file = request.files["image"]

            # 检查文件类型
            if not file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
                return jsonify({"error": "仅支持PNG、JPG格式图片"})

            # 检查文件大小（限制2MB）
            file.seek(0, 2)  # 移动到文件末尾
            file_size = file.tell()
            file.seek(0)  # 移回文件开头

            if file_size > 2 * 1024 * 1024:  # 2MB
                return jsonify({"error": "图片大小不能超过2MB"})

            # 读取图片数据
            image_data = file.read()

            # 识别图片
            result = recognizer.recognize(image_data)

            return jsonify(result)
        except Exception as e:
            app.logger.error(f"识别失败: {str(e)}\n{traceback.format_exc()}")
            return jsonify({"error": f"识别失败: {str(e)}"}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
