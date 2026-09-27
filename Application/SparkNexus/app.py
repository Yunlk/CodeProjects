"""
项目名称: 星火云枢：班级智能协同平台
启动日期: 2026-06-02
"""

import os
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import (Blueprint, Flask, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import (BooleanField, DateTimeField, IntegerField, PasswordField,
                     SelectField, StringField, SubmitField, TextAreaField)
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange


class Config:
    """应用配置"""

    @classmethod
    def load_environment(cls):
        """加载 .env 文件（幂等）"""
        load_dotenv()

    @classmethod
    def values(cls):
        """读取环境变量，返回配置字典"""
        return {
            "SECRET_KEY": os.environ.get("SECRET_KEY", "default-secret"),
            "SQLALCHEMY_DATABASE_URI": os.environ.get(
                "DATABASE_URL", "sqlite:///dev.db"
            ),
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        }


class Extensions:
    """Flask 扩展集中管理"""

    db = SQLAlchemy()
    login_manager = LoginManager()
    migrate = Migrate()
    csrf = CSRFProtect()

    @classmethod
    def init_app(cls, app):
        """把扩展绑定到 app 上"""
        cls.db.init_app(app)
        cls.login_manager.init_app(app)
        cls.migrate.init_app(app, cls.db)
        cls.csrf.init_app(app)
        cls.login_manager.login_view = "auth.login"
        cls.login_manager.login_message = "请先登录。"


db = Extensions.db
login_manager = Extensions.login_manager


class Permissions:
    """权限校验装饰器"""

    @staticmethod
    def require_roles(*roles):
        """允许拥有任一指定角色（或更高等级）的用户访问"""

        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                if not current_user.is_authenticated:
                    abort(403)
                if not any(current_user.has_permission(role) for role in roles):
                    abort(403)
                return f(*args, **kwargs)

            return decorated_function

        return decorator


class Role:
    """角色常量与等级映射"""

    ADMIN = "admin"
    TEACHER = "teacher"
    MONITOR = "monitor"
    LEADER = "leader"
    STUDENT = "student"

    LEVELS = {
        ADMIN: 100,
        TEACHER: 80,
        MONITOR: 60,
        LEADER: 40,
        STUDENT: 20,
    }

    @classmethod
    def get_level(cls, role_name):
        """按角色名返回等级，未知角色返回 0"""
        return cls.LEVELS.get(role_name, 0)


class User(UserMixin, db.Model):
    """用户模型"""

    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=Role.STUDENT)
    class_id = db.Column(db.Integer, default=0)

    def set_password(self, password):
        """写入密码哈希"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """校验密码"""
        return check_password_hash(self.password_hash, password)

    def change_password(self, old_password, new_password):
        """先校验旧密码，通过则写入新密码"""
        if self.check_password(old_password):
            self.set_password(new_password)
            return True
        return False

    def is_admin(self):
        """是否管理员"""
        return self.role == Role.ADMIN

    def __repr__(self):
        return f"<User {self.username} {self.role}>"

    @property
    def role_level(self):
        """当前角色等级"""
        return Role.get_level(self.role)

    def has_permission(self, required_role):
        """是否满足至少 required_role 的等级"""
        return self.role_level >= Role.get_level(required_role)


class Event(db.Model):
    """日程/活动/课程"""

    __tablename__ = "events"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # course / activity / personal
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime)
    description = db.Column(db.Text)
    visibility = db.Column(db.String(20), default="public")  # public / class / private
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    class_id = db.Column(db.Integer, default=0)

    creator = db.relationship("User", backref="events")


class Registration(db.Model):
    """报名/签到"""

    __tablename__ = "registrations"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # registration / checkin
    max_participants = db.Column(db.Integer)
    deadline = db.Column(db.DateTime)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"))
    class_id = db.Column(db.Integer, default=0)

    creator = db.relationship("User", backref="registrations")


class RegistrationRecord(db.Model):
    """单条报名/签到记录"""

    __tablename__ = "registration_records"
    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(db.Integer, db.ForeignKey("registrations.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    timestamp = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    status = db.Column(db.String(20), default="signed_up")  # signed_up / checked_in

    user = db.relationship("User", backref="records")
    registration = db.relationship("Registration", backref="records")


class Announcement(db.Model):
    """通知公告"""

    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text)
    type = db.Column(db.String(20), nullable=False)  # daily / homework / activity
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    class_id = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))

    sender = db.relationship("User", backref="announcements")


class AuthForms:
    """认证模块表单集合"""

    class Login(FlaskForm):
        """登录表单"""

        username = StringField("用户名", validators=[DataRequired()])
        password = PasswordField("密码", validators=[DataRequired()])
        remember = BooleanField("记住我")
        submit = SubmitField("登录")

    class Register(FlaskForm):
        """注册表单"""

        username = StringField(
            "用户名", validators=[DataRequired(), Length(min=3, max=64)]
        )
        password = PasswordField("密码", validators=[DataRequired(), Length(min=6)])
        confirm = PasswordField(
            "确认密码", validators=[DataRequired(), EqualTo("password")]
        )
        submit = SubmitField("注册")

    class ChangePassword(FlaskForm):
        """修改密码表单"""

        old_password = PasswordField("当前密码", validators=[DataRequired()])
        new_password = PasswordField(
            "新密码", validators=[DataRequired(), Length(min=6)]
        )
        confirm = PasswordField(
            "确认新密码", validators=[DataRequired(), EqualTo("new_password")]
        )
        submit = SubmitField("修改密码")

    class EditRole(FlaskForm):
        """编辑用户角色表单"""

        role = SelectField(
            "角色",
            choices=[
                ("student", "学生"),
                ("teacher", "教师"),
                ("monitor", "班委"),
                ("leader", "临时负责人"),
                ("admin", "管理员"),
            ],
            validators=[DataRequired()],
        )
        submit = SubmitField("更新")

    class AdminResetPassword(FlaskForm):
        """管理员重置密码表单"""

        new_password = PasswordField(
            "新密码", validators=[DataRequired(), Length(min=6)]
        )
        confirm = PasswordField(
            "确认新密码", validators=[DataRequired(), EqualTo("new_password")]
        )
        submit = SubmitField("重置密码")


class NotificationForms:
    """通知模块表单集合"""

    class Announcement(FlaskForm):
        """发布通知表单"""

        title = StringField("标题", validators=[DataRequired(), Length(max=100)])
        content = TextAreaField("内容", validators=[DataRequired()])
        type = SelectField("类型", coerce=str)
        submit = SubmitField("发布")

        def set_type_choices(self, role):
            """根据角色动态设置可发布的通知类型"""
            if role == "leader":
                self.type.choices = [("activity", "活动通知")]
            else:
                self.type.choices = [
                    ("daily", "日常通知"),
                    ("homework", "作业通知"),
                    ("activity", "活动通知"),
                ]


class RegistrationForms:
    """报名模块表单集合"""

    class Create(FlaskForm):
        """创建报名/签到表单"""

        title = StringField("标题", validators=[DataRequired()])
        type = SelectField(
            "类型",
            choices=[("registration", "报名"), ("checkin", "签到")],
            validators=[DataRequired()],
        )
        max_participants = IntegerField(
            "人数上限", validators=[NumberRange(min=1)], default=50
        )
        deadline = DateTimeField(
            "截止时间", format="%Y-%m-%dT%H:%M", validators=[DataRequired()]
        )
        description = TextAreaField("描述")
        event_id = IntegerField("关联活动ID", validators=[DataRequired()])
        submit = SubmitField("创建")


class ScheduleForms:
    """日程模块表单集合"""

    class Event(FlaskForm):
        """创建/编辑日程表单"""

        title = StringField("标题", validators=[DataRequired()])
        type = SelectField(
            "类型",
            choices=[
                ("course", "课程"),
                ("activity", "活动"),
                ("personal", "个人日程"),
            ],
            validators=[DataRequired()],
        )
        start_time = DateTimeField(
            "开始时间", format="%Y-%m-%dT%H:%M", validators=[DataRequired()]
        )
        end_time = DateTimeField(
            "结束时间", format="%Y-%m-%dT%H:%M", validators=[DataRequired()]
        )
        description = TextAreaField("描述")
        visibility = SelectField(
            "可见范围",
            choices=[("public", "公开"), ("class", "班级"), ("private", "仅自己")],
        )
        submit = SubmitField("保存")


class AuthBlueprint:
    """认证蓝图"""

    @classmethod
    def create(cls):
        """创建并返回 auth 蓝图"""
        bp = Blueprint("auth", __name__, url_prefix="/auth")

        @bp.route("/login", methods=["GET", "POST"])
        def login():
            """登录"""
            if current_user.is_authenticated:
                return redirect(url_for("home"))
            form = AuthForms.Login()
            if form.validate_on_submit():
                user = User.query.filter_by(username=form.username.data).first()
                if user and user.check_password(form.password.data):
                    login_user(user, remember=form.remember.data)
                    flash("登录成功。")
                    next_page = request.args.get("next")
                    return redirect(next_page or url_for("home"))
                flash("用户名或密码错误。")
            return render_template("auth/login.html", form=form)

        @bp.route("/register", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("admin")
        def register():
            """管理员创建用户"""
            form = AuthForms.Register()
            if form.validate_on_submit():
                if User.query.filter_by(username=form.username.data).first():
                    flash("用户名已存在。")
                    return render_template("auth/register.html", form=form)

                user = User(username=form.username.data, role=Role.STUDENT)
                user.set_password(form.password.data)
                db.session.add(user)
                db.session.commit()
                flash("用户创建成功。")
                return redirect(url_for("auth.manage_users"))
            return render_template("auth/register.html", form=form)

        @bp.route("/logout")
        @login_required
        def logout():
            """退出登录"""
            logout_user()
            flash("已退出登录。")
            return redirect(url_for("home"))

        @bp.route("/profile", methods=["GET", "POST"])
        @login_required
        def profile():
            """个人资料 / 修改密码"""
            form = AuthForms.ChangePassword()
            if form.validate_on_submit():
                if current_user.change_password(
                    form.old_password.data, form.new_password.data
                ):
                    db.session.commit()
                    flash("密码修改成功。")
                    return redirect(url_for("auth.profile"))
                else:
                    flash("当前密码错误。")
            return render_template("auth/profile.html", form=form)

        @bp.route("/admin/users")
        @login_required
        @Permissions.require_roles("admin")
        def manage_users():
            """用户列表"""
            users = User.query.order_by(User.username).all()
            return render_template("auth/admin_users.html", users=users)

        @bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("admin")
        def edit_user_role(user_id):
            """编辑用户角色"""
            user = User.query.get_or_404(user_id)
            if user == current_user:
                flash("不能修改自己的角色。")
                return redirect(url_for("auth.manage_users"))
            form = AuthForms.EditRole()
            if form.validate_on_submit():
                user.role = form.role.data
                db.session.commit()
                flash(f"用户 {user.username} 的角色已更新为 {user.role}。")
                return redirect(url_for("auth.manage_users"))
            elif request.method == "GET":
                form.role.data = user.role
            return render_template("auth/edit_role.html", form=form, user=user)

        @bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
        @login_required
        @Permissions.require_roles("admin")
        def delete_user(user_id):
            """删除用户"""
            user = User.query.get_or_404(user_id)
            if user == current_user:
                flash("不能删除自己的账号。")
            else:
                db.session.delete(user)
                db.session.commit()
                flash(f"用户 {user.username} 已删除。")
            return redirect(url_for("auth.manage_users"))

        @bp.route("/admin/users/<int:user_id>/reset-password", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("admin")
        def reset_user_password(user_id):
            """管理员重置用户密码"""
            user = User.query.get_or_404(user_id)
            form = AuthForms.AdminResetPassword()
            if form.validate_on_submit():
                user.set_password(form.new_password.data)
                db.session.commit()
                flash(f"用户 {user.username} 的密码已重置。")
                return redirect(url_for("auth.manage_users"))
            return render_template("auth/reset_password.html", form=form, user=user)

        return bp


class NotificationBlueprint:
    """通知蓝图"""

    @classmethod
    def create(cls):
        """创建并返回 notification 蓝图"""
        bp = Blueprint("notification", __name__, url_prefix="/notifications")

        @bp.route("/")
        @login_required
        def list_all():
            """通知列表（支持按类型过滤、分页）"""
            page = request.args.get("page", 1, type=int)
            type_filter = request.args.get("type")
            query = Announcement.query
            if type_filter and type_filter in ["daily", "homework", "activity"]:
                query = query.filter(Announcement.type == type_filter)
            announcements = query.order_by(Announcement.created_at.desc()).paginate(
                page=page, per_page=20, error_out=False
            )
            return render_template(
                "notification/list.html",
                announcements=announcements,
                type_filter=type_filter,
            )

        @bp.route("/create", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("leader")
        def create():
            """发布通知"""
            form = NotificationForms.Announcement()
            form.set_type_choices(current_user.role)
            if form.validate_on_submit():
                announcement = Announcement(
                    title=form.title.data,
                    content=form.content.data,
                    type=form.type.data,
                    sender_id=current_user.id,
                    class_id=current_user.class_id,
                )
                db.session.add(announcement)
                db.session.commit()
                flash("通知发布成功。")
                return redirect(url_for("notification.list_all"))
            return render_template("notification/create.html", form=form)

        @bp.route("/<int:id>")
        @login_required
        def detail(id):
            """通知详情"""
            announcement = Announcement.query.get_or_404(id)
            return render_template(
                "notification/detail.html", announcement=announcement
            )

        return bp


class RegistrationBlueprint:
    """报名蓝图"""

    @classmethod
    def create(cls):
        """创建并返回 registration 蓝图"""
        bp = Blueprint("registration", __name__, url_prefix="/registrations")

        @bp.route("/")
        @login_required
        def list():
            """报名列表"""
            registrations = Registration.query.order_by(Registration.deadline).all()
            return render_template(
                "registration/list.html", registrations=registrations
            )

        @bp.route("/create", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("leader")
        def create():
            """创建报名/签到"""
            form = RegistrationForms.Create()
            if form.validate_on_submit():
                event = Event.query.get(form.event_id.data)
                if not event:
                    flash("关联活动不存在。")
                    return render_template("registration/create.html", form=form)
                reg = Registration(
                    title=form.title.data,
                    type=form.type.data,
                    max_participants=form.max_participants.data,
                    deadline=form.deadline.data,
                    creator_id=current_user.id,
                    event_id=event.id,
                    class_id=current_user.class_id,
                )
                db.session.add(reg)
                db.session.commit()
                flash("报名/签到创建成功。")
                return redirect(url_for("registration.list"))
            return render_template("registration/create.html", form=form)

        @bp.route("/<int:id>")
        @login_required
        def detail(id):
            """报名详情（POST 时直接执行报名/签到）"""
            reg = Registration.query.get_or_404(id)
            if request.method == "POST":
                return cls._handle_signup(reg)
            records = RegistrationRecord.query.filter_by(registration_id=id).all()
            return render_template("registration/detail.html", reg=reg, records=records)

        @bp.route("/<int:id>/signup", methods=["POST"])
        @login_required
        def signup(id):
            """学生点击报名/签到按钮"""
            reg = Registration.query.get_or_404(id)
            return cls._handle_signup(reg)

        return bp

    @staticmethod
    def _handle_signup(reg):
        """学生报名/签到的通用处理"""
        if current_user.role != "student":
            flash("只有学生可以参与报名/签到。")
            return redirect(url_for("registration.detail", id=reg.id))
        if reg.deadline and datetime.utcnow() > reg.deadline:
            flash("已超过截止时间。")
            return redirect(url_for("registration.detail", id=reg.id))
        count = RegistrationRecord.query.filter_by(registration_id=reg.id).count()
        if reg.max_participants and count >= reg.max_participants:
            flash("名额已满。")
            return redirect(url_for("registration.detail", id=reg.id))
        existing = RegistrationRecord.query.filter_by(
            registration_id=reg.id, user_id=current_user.id
        ).first()
        if existing:
            flash("您已经报过名/签过到。")
            return redirect(url_for("registration.detail", id=reg.id))
        record = RegistrationRecord(
            registration_id=reg.id,
            user_id=current_user.id,
            status="signed_up" if reg.type == "registration" else "checked_in",
        )
        db.session.add(record)
        db.session.commit()
        flash("操作成功！")
        return redirect(url_for("registration.detail", id=reg.id))


class ScheduleBlueprint:
    """日程/活动蓝图"""

    @classmethod
    def create(cls):
        """创建并返回 schedule 蓝图"""
        bp = Blueprint("schedule", __name__, url_prefix="/schedule")

        @bp.route("/")
        @login_required
        def view():
            """课表/日程查询：公开 + 班级 + 自己的私有事件"""
            events = (
                Event.query.filter(
                    (Event.visibility.in_(["public", "class"]))
                    | (
                        (Event.visibility == "private")
                        & (Event.creator_id == current_user.id)
                    )
                )
                .order_by(Event.start_time)
                .all()
            )
            return render_template("schedule/view.html", events=events)

        @bp.route("/manage")
        @login_required
        @Permissions.require_roles("admin", "teacher")
        def manage():
            """课表调整页面"""
            events = (
                Event.query.filter_by(type="course").order_by(Event.start_time).all()
            )
            return render_template("schedule/manage.html", events=events)

        @bp.route("/api/events", methods=["POST"])
        @login_required
        @Permissions.require_roles("admin", "teacher")
        def create_course_event():
            """AJAX 创建课程事件"""
            data = request.get_json()
            event = Event(
                title=data["title"],
                type="course",
                start_time=datetime.fromisoformat(data["start"]),
                end_time=datetime.fromisoformat(data["end"]),
                description=data.get("description", ""),
                visibility="class",
                creator_id=current_user.id,
                class_id=current_user.class_id,
            )
            db.session.add(event)
            db.session.commit()
            return jsonify({"id": event.id}), 201

        @bp.route("/api/events/<int:id>", methods=["PUT", "DELETE"])
        @login_required
        @Permissions.require_roles("admin", "teacher")
        def modify_course_event(id):
            """AJAX 更新/删除课程事件"""
            event = Event.query.get_or_404(id)
            if event.type != "course":
                abort(400)
            if request.method == "DELETE":
                db.session.delete(event)
                db.session.commit()
                return jsonify({"message": "deleted"}), 200
            data = request.get_json()
            event.title = data["title"]
            event.start_time = datetime.fromisoformat(data["start"])
            event.end_time = datetime.fromisoformat(data["end"])
            event.description = data.get("description", "")
            db.session.commit()
            return jsonify({"id": event.id}), 200

        @bp.route("/add", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("student")
        def add_personal():
            """学生添加个人日程"""
            form = ScheduleForms.Event()
            if current_user.role == "student":
                form.type.choices = [("personal", "个人日程")]
                form.visibility.choices = [("private", "仅自己")]
            if form.validate_on_submit():
                event = Event(
                    title=form.title.data,
                    type=form.type.data,
                    start_time=form.start_time.data,
                    end_time=form.end_time.data,
                    description=form.description.data,
                    visibility=form.visibility.data,
                    creator_id=current_user.id,
                    class_id=current_user.class_id,
                )
                db.session.add(event)
                db.session.commit()
                flash("日程添加成功。")
                return redirect(url_for("schedule.view"))
            return render_template("schedule/add.html", form=form)

        @bp.route("/create-event", methods=["GET", "POST"])
        @login_required
        @Permissions.require_roles("leader")
        def create_activity():
            """发布活动（type 强制为 activity）"""
            form = ScheduleForms.Event()
            form.type.data = "activity"
            form.type.render_kw = {"disabled": "disabled"}
            if form.validate_on_submit():
                event = Event(
                    title=form.title.data,
                    type="activity",
                    start_time=form.start_time.data,
                    end_time=form.end_time.data,
                    description=form.description.data,
                    visibility=form.visibility.data,
                    creator_id=current_user.id,
                    class_id=current_user.class_id,
                )
                db.session.add(event)
                db.session.commit()
                flash("活动发布成功。")
                return redirect(url_for("schedule.view"))
            return render_template("schedule/create_event.html", form=form)

        return bp


class Application:
    """应用工厂"""

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    @classmethod
    def create(cls, config_class=Config):
        """创建并配置 Flask 应用"""
        config_class.load_environment()

        app = Flask(
            __name__,
            template_folder=os.path.join(cls.BASE_DIR, "templates"),
        )
        app.config.update(config_class.values())

        Extensions.init_app(app)

        app.register_blueprint(AuthBlueprint.create())
        app.register_blueprint(ScheduleBlueprint.create())
        app.register_blueprint(RegistrationBlueprint.create())
        app.register_blueprint(NotificationBlueprint.create())

        @login_manager.user_loader
        def load_user(user_id):
            return User.query.get(int(user_id))

        @app.route("/")
        def home():
            """首页"""
            return render_template("index.html")

        return app


if __name__ == "__main__":
    app = Application.create()
    app.run(debug=False, use_reloader=False)
