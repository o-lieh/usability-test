import secrets

from django.conf import settings
from django.db import models


def new_token() -> str:
    """Unguessable token used in the participant's invite link."""
    return secrets.token_urlsafe(16)


class Session(models.Model):
    """One participant's test session."""

    code = models.CharField(max_length=10, unique=True)  # P1, P2, ...
    token = models.CharField(max_length=40, unique=True, default=new_token)
    created_at = models.DateTimeField(auto_now_add=True)
    consent_screen = models.BooleanField(default=False)
    consent_audio = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    current_task = models.PositiveSmallIntegerField(default=0)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)  # 1..5
    suggestions = models.TextField(blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def state_label(self) -> str:
        if self.ended_at:
            return "تکمیل شد"
        if self.started_at:
            return "در حال انجام"
        return "شروع نشده"

    def __str__(self):
        return self.code


class TaskResult(models.Model):
    PENDING, DONE, FAILED = "pending", "done", "failed"
    STATUSES = [PENDING, DONE, FAILED]

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="results")
    index = models.PositiveSmallIntegerField()  # 0-based position in TASKS
    status = models.CharField(max_length=10, default=PENDING)
    first_shown_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["index"]
        constraints = [models.UniqueConstraint(fields=["session", "index"], name="uniq_session_task")]

    @property
    def duration_seconds(self):
        """Seconds from the task first being shown until it was marked done/failed."""
        if self.first_shown_at and self.finished_at and self.status != self.PENDING:
            return max(int((self.finished_at - self.first_shown_at).total_seconds()), 0)
        return None


class Event(models.Model):
    """Timestamped log used to find the matching moment in a recording."""

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="events")
    type = models.CharField(max_length=40)
    task_index = models.PositiveSmallIntegerField(null=True, blank=True)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class RecordingPart(models.Model):
    """One continuous recording file. A new part starts if sharing is stopped and resumed."""

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="parts")
    number = models.PositiveSmallIntegerField()
    mime = models.CharField(max_length=100, default="video/webm")
    has_video = models.BooleanField(default=False)
    has_audio = models.BooleanField(default=False)
    next_seq = models.PositiveIntegerField(default=0)  # next expected chunk number
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["session", "number"], name="uniq_session_part")]

    @property
    def extension(self) -> str:
        m = self.mime.lower()
        if "mp4" in m:
            return "mp4"
        if "ogg" in m:
            return "ogg"
        return "webm"

    @property
    def content_type(self) -> str:
        return self.mime.split(";")[0] or "video/webm"

    @property
    def path(self):
        return settings.RECORDINGS_DIR / self.session.code / f"part{self.number}.{self.extension}"

    @property
    def size_mb(self) -> float:
        try:
            return round(self.path.stat().st_size / 1_048_576, 1)
        except OSError:
            return 0.0
