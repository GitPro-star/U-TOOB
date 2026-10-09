import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_UPLOAD_DIR = INSTANCE_DIR / "uploads"
DEFAULT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".mp4", ".webm", ".ogg", ".mov", ".m4v", ".avi", ".mkv"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB; tune for your hosting plan.

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48),
    SQLALCHEMY_DATABASE_URI=(
        os.environ.get("DATABASE_URL", f"sqlite:///{(INSTANCE_DIR / 'utoob.db').as_posix()}")
        .replace("postgres://", "postgresql+psycopg://", 1)
        .replace("postgresql://", "postgresql+psycopg://", 1)
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", str(DEFAULT_UPLOAD_DIR)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
)
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    app.config["SESSION_COOKIE_SECURE"] = True

Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Zaloguj się, aby wykonać tę czynność."
login_manager.login_message_category = "info"


def utcnow():
    return datetime.now(timezone.utc)


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def safe_display_name(value, max_len=40):
    return " ".join((value or "").strip().split())[:max_len]


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(24), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(40), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.String(1000), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    videos = db.relationship("Video", backref="owner", lazy=True, cascade="all, delete-orphan")
    comments = db.relationship("Comment", backref="author", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def initials(self):
        words = [word for word in re.split(r"\s+", self.display_name.strip()) if word]
        if len(words) > 1:
            return (words[0][0] + words[-1][0]).upper()
        return (words[0][0] if words else "U").upper()

    @property
    def subscriber_count(self):
        return Subscription.query.filter_by(channel_id=self.id).count()


class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    storage_key = db.Column(db.String(500), nullable=False, unique=True)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    views = db.Column(db.Integer, nullable=False, default=0)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    comments = db.relationship("Comment", backref="video", lazy=True, cascade="all, delete-orphan", order_by="Comment.created_at.desc()")
    likes = db.relationship("VideoLike", backref="video", lazy=True, cascade="all, delete-orphan")

    @property
    def like_count(self):
        return len(self.likes)

    @property
    def comment_count(self):
        return len(self.comments)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = db.Column(db.Integer, db.ForeignKey("video.id", ondelete="CASCADE"), nullable=False, index=True)


class VideoLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("video.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "video_id", name="uq_video_like_user_video"),)


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        db.UniqueConstraint("follower_id", "channel_id", name="uq_subscription_follower_channel"),
        db.CheckConstraint("follower_id != channel_id", name="ck_no_self_subscription"),
    )


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


# ----- Video storage: local for development, Cloudflare R2 (S3 API) for hosting -----
def storage_backend():
    return os.environ.get("STORAGE_BACKEND", "local").strip().lower()


def r2_client():
    required = ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Brakuje ustawień magazynu wideo: " + ", ".join(missing) + ". Uzupełnij je w Render → Environment.")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def save_video(file_storage, key):
    content_type = file_storage.mimetype or "application/octet-stream"
    if storage_backend() == "r2":
        client = r2_client()
        client.upload_fileobj(file_storage.stream, os.environ["R2_BUCKET_NAME"], key,
                              ExtraArgs={"ContentType": content_type})
        return
    local_root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    destination = (local_root / key).resolve()
    if not destination.is_relative_to(local_root):
        raise ValueError("Nieprawidłowa ścieżka pliku.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(destination)


def video_url(video):
    if storage_backend() == "r2":
        client = r2_client()
        public_base = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")
        if public_base:
            return f"{public_base}/{quote(video.storage_key, safe='/')}"
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["R2_BUCKET_NAME"], "Key": video.storage_key},
            ExpiresIn=3600,
        )
    return url_for("local_video", key=video.storage_key)


def delete_video_file(key):
    try:
        if storage_backend() == "r2":
            r2_client().delete_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=key)
        else:
            path = (Path(app.config["UPLOAD_FOLDER"]).resolve() / key).resolve()
            if path.parent == Path(app.config["UPLOAD_FOLDER"]).resolve():
                path.unlink(missing_ok=True)
    except Exception:
        app.logger.exception("Nie udało się usunąć pliku wideo %s", key)


def video_card_data(video):
    return {
        "video": video,
        "media_url": video_url(video),
        "likes": video.like_count,
    }


def render_video_list(videos):
    return [video_card_data(video) for video in videos]


def subscription_ids(user):
    if not user.is_authenticated:
        return set()
    return {row.channel_id for row in Subscription.query.filter_by(follower_id=user.id).all()}


@app.context_processor
def inject_globals():
    return {"site_name": "U-TOOB", "current_year": datetime.now().year}


@app.route("/")
def index():
    q = request.args.get("q", "").strip()[:120]
    sort = request.args.get("sort", "newest")
    view = request.args.get("view", "home")
    if view == "library":
        if not current_user.is_authenticated:
            flash("Zaloguj się, aby zobaczyć swoje filmy.", "info")
            return redirect(url_for("login"))
        query = Video.query.filter_by(owner_id=current_user.id)
        title = "Moje filmy"
    elif view == "subscriptions":
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        channel_ids = [s.channel_id for s in Subscription.query.filter_by(follower_id=current_user.id).all()]
        query = Video.query.filter(Video.owner_id.in_(channel_ids)) if channel_ids else Video.query.filter(Video.id == -1)
        title = "Subskrypcje"
    else:
        query = Video.query
        title = "Wyniki wyszukiwania" if q else ("Popularne filmy" if sort == "popular" else "Strona główna")
    if q:
        query = query.filter(or_(Video.title.ilike(f"%{q}%"), Video.description.ilike(f"%{q}%")))
    if sort == "popular":
        query = query.order_by(Video.views.desc(), Video.uploaded_at.desc())
    else:
        query = query.order_by(Video.uploaded_at.desc())
    videos = query.limit(120).all()
    return render_template("index.html", title=title, videos=render_video_list(videos), search_query=q,
                           sort=sort, view=view, subscriptions=subscription_ids(current_user))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("account"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        display_name = safe_display_name(request.form.get("display_name", ""))
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")
        if not re.fullmatch(r"[a-z0-9_]{3,24}", username):
            flash("Nazwa użytkownika musi mieć 3–24 znaki: małe litery angielskie, cyfry lub podkreślenia.", "error")
        elif not display_name:
            flash("Podaj nazwę wyświetlaną.", "error")
        elif len(password) < 8 or len(password) > 128:
            flash("Hasło musi mieć od 8 do 128 znaków.", "error")
        elif password != confirm:
            flash("Hasła nie są takie same.", "error")
        elif User.query.filter_by(username=username).first():
            flash("Ta nazwa użytkownika jest już zajęta.", "error")
        else:
            user = User(username=username, display_name=display_name)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            flash("Konto utworzone! Witaj w U-TOOB.", "success")
            return redirect(url_for("channel", username=user.username))
    return render_template("auth.html", title="Utwórz konto", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            flash("Zalogowano pomyślnie.", "success")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("index"))
        flash("Nieprawidłowa nazwa użytkownika lub hasło.", "error")
    return render_template("auth.html", title="Zaloguj się", mode="login")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Wylogowano.", "info")
    return redirect(url_for("index"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        display_name = safe_display_name(request.form.get("display_name", ""))
        bio = (request.form.get("bio", "") or "").strip()
        if not display_name or len(display_name) > 40:
            flash("Nazwa wyświetlana musi mieć 1–40 znaków.", "error")
        elif len(bio) > 1000:
            flash("Opis kanału może mieć maksymalnie 1000 znaków.", "error")
        else:
            current_user.display_name = display_name
            current_user.bio = bio
            db.session.commit()
            flash("Profil zapisany.", "success")
            return redirect(url_for("account"))
    return render_template("account.html", title="Ustawienia konta")


@app.route("/channel/<username>")
def channel(username):
    person = User.query.filter_by(username=username.lower()).first_or_404()
    videos = Video.query.filter_by(owner_id=person.id).order_by(Video.uploaded_at.desc()).all()
    subscribed = False
    if current_user.is_authenticated and current_user.id != person.id:
        subscribed = Subscription.query.filter_by(follower_id=current_user.id, channel_id=person.id).first() is not None
    return render_template("channel.html", title=person.display_name, person=person,
                           videos=render_video_list(videos), subscribed=subscribed)


@app.route("/subscribe/<int:user_id>", methods=["POST"])
@login_required
def subscribe(user_id):
    person = db.session.get(User, user_id)
    if person is None:
        abort(404)
    if person.id == current_user.id:
        flash("Nie możesz subskrybować własnego kanału.", "error")
        return redirect(url_for("channel", username=person.username))
    existing = Subscription.query.filter_by(follower_id=current_user.id, channel_id=person.id).first()
    if existing:
        db.session.delete(existing)
        flash("Anulowano subskrypcję.", "info")
    else:
        db.session.add(Subscription(follower_id=current_user.id, channel_id=person.id))
        flash("Subskrybujesz ten kanał!", "success")
    db.session.commit()
    return redirect(url_for("channel", username=person.username))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        title = (request.form.get("title", "") or "").strip()
        description = (request.form.get("description", "") or "").strip()
        file = request.files.get("video")
        if not title or len(title) > 200:
            flash("Podaj tytuł (maksymalnie 200 znaków).", "error")
        elif len(description) > 10000:
            flash("Opis może mieć maksymalnie 10 000 znaków.", "error")
        elif not file or not file.filename:
            flash("Wybierz plik wideo.", "error")
        elif not allowed_file(file.filename):
            flash("Nieobsługiwany format. Wybierz MP4, WebM, MOV, M4V, OGG, AVI lub MKV.", "error")
        else:
            original = secure_filename(file.filename)
            suffix = Path(original).suffix.lower()
            key = f"videos/{secrets.token_hex(16)}{suffix}"
            try:
                save_video(file, key)
                video = Video(title=title, description=description, storage_key=key, owner_id=current_user.id)
                db.session.add(video)
                db.session.commit()
                flash("Film opublikowany na Twoim kanale!", "success")
                return redirect(url_for("watch", video_id=video.id))
            except Exception as exc:
                db.session.rollback()
                delete_video_file(key)
                app.logger.exception("Błąd przesyłania wideo")
                message = str(exc) if isinstance(exc, RuntimeError) else "Nie udało się zapisać pliku. Sprawdź konfigurację magazynu wideo i spróbuj ponownie."
                flash(message, "error")
    return render_template("upload.html", title="Prześlij film")


@app.route("/watch/<int:video_id>")
def watch(video_id):
    video = db.get_or_404(Video, video_id)
    video.views += 1
    db.session.commit()
    try:
        media_url = video_url(video)
    except Exception:
        app.logger.exception("Nie można utworzyć URL dla wideo")
        media_url = None
        flash("Nie można otworzyć pliku wideo. Sprawdź konfigurację magazynu.", "error")
    liked = False
    if current_user.is_authenticated:
        liked = VideoLike.query.filter_by(user_id=current_user.id, video_id=video.id).first() is not None
    comments = Comment.query.filter_by(video_id=video.id).order_by(Comment.created_at.desc()).limit(100).all()
    related = Video.query.filter(Video.id != video.id).order_by(Video.views.desc()).limit(4).all()
    return render_template("watch.html", title=video.title, video=video, media_url=media_url,
                           liked=liked, comments=comments, related=render_video_list(related))


@app.route("/like/<int:video_id>", methods=["POST"])
@login_required
def like(video_id):
    video = db.get_or_404(Video, video_id)
    existing = VideoLike.query.filter_by(user_id=current_user.id, video_id=video.id).first()
    if existing:
        db.session.delete(existing)
        flash("Usunięto polubienie.", "info")
    else:
        db.session.add(VideoLike(user_id=current_user.id, video_id=video.id))
        flash("Polubiono film.", "success")
    db.session.commit()
    return redirect(url_for("watch", video_id=video.id))


@app.route("/comment/<int:video_id>", methods=["POST"])
@login_required
def comment(video_id):
    video = db.get_or_404(Video, video_id)
    body = (request.form.get("body", "") or "").strip()
    if not body:
        flash("Komentarz nie może być pusty.", "error")
    elif len(body) > 2000:
        flash("Komentarz może mieć maksymalnie 2000 znaków.", "error")
    else:
        db.session.add(Comment(body=body, user_id=current_user.id, video_id=video.id))
        db.session.commit()
        flash("Dodano komentarz.", "success")
    return redirect(url_for("watch", video_id=video.id) + "#comments")


@app.route("/media/<path:key>")
def local_video(key):
    if storage_backend() == "r2":
        abort(404)
    # Only serve files from the configured upload folder; keys are stored as relative paths.
    if ".." in Path(key).parts:
        abort(404)
    root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    candidate = (root / key).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        abort(404)
    return send_from_directory(root, key, conditional=True, as_attachment=False)


@app.route("/healthz")
def healthz():
    return {"status": "ok", "app": "U-TOOB", "version": "5.0"}


@app.errorhandler(413)
def too_large(_error):
    flash("Plik jest większy niż limit 512 MB.", "error")
    return redirect(url_for("upload"))


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=True)
