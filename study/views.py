import csv
import hmac
import json
import re
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Event, RecordingPart, Session, TaskResult
from .tasks import TASKS

# Client-reported events we accept (server-side events are logged directly).
CLIENT_EVENTS = {"capture_lost", "recording_stop", "mic_denied"}

EVENT_LABELS = {
    "begin": "شروع آزمون",
    "resume": "ادامه پس از بازگشت به صفحه",
    "task_shown": "نمایش کار",
    "task_done": "انجام شد",
    "task_failed": "نتوانست انجام دهد",
    "task_reset": "علامت برداشته شد",
    "recording_start": "شروع ضبط",
    "recording_stop": "پایان ضبط",
    "capture_lost": "قطع اشتراک‌گذاری صفحه",
    "mic_denied": "میکروفن رد شد",
    "finish": "پایان آزمون",
}
STATUS_LABELS = {"pending": "بدون پاسخ", "done": "انجام شد", "failed": "ناموفق"}


# --- Helpers ------------------------------------------------------------------
def _json(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _err(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _log(session, type_, task_index=None, detail=""):
    Event.objects.create(session=session, type=type_, task_index=task_index, detail=detail[:200])


def _get_session(token) -> Session:
    return get_object_or_404(Session, token=token)


def _fmt_seconds(seconds):
    if seconds is None:
        return "—"
    return f"{seconds // 60}:{seconds % 60:02d}"


# --- Participant pages ----------------------------------------------------------
def index(request):
    return render(request, "study/invalid.html")


def session_page(request, token):
    session = _get_session(token)
    if session.ended_at:
        return render(request, "study/thanks.html")

    saved = {r.index: r for r in session.results.all()}
    results = []
    for i in range(len(TASKS)):
        r = saved.get(i)
        results.append({"status": r.status if r else "pending", "comment": r.comment if r else ""})

    cfg = {
        "token": session.token,
        "tasks": TASKS,
        "results": results,
        "currentTask": session.current_task,
        "appUrl": settings.APP_URL,
        "started": session.started_at is not None,
        "consentScreen": session.consent_screen,
        "consentAudio": session.consent_audio,
    }
    return render(request, "study/session.html", {"cfg": cfg, "started": cfg["started"]})


# --- Participant API --------------------------------------------------------------
@csrf_exempt
@require_POST
def api_begin(request, token):
    session = _get_session(token)
    if session.ended_at:
        return _err("finished", 403)
    data = _json(request)
    first = session.started_at is None
    session.consent_screen = bool(data.get("consent_screen"))
    session.consent_audio = bool(data.get("consent_audio"))
    if first:
        session.started_at = timezone.now()
        session.user_agent = request.headers.get("User-Agent", "")[:300]
    session.save(update_fields=["consent_screen", "consent_audio", "started_at", "user_agent"])
    for i in range(len(TASKS)):
        TaskResult.objects.get_or_create(session=session, index=i)
    _log(session, "begin" if first else "resume")
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def api_task(request, token):
    """Update one task: mark it shown, set its status and/or save the comment."""
    session = _get_session(token)
    if session.ended_at:
        return _err("finished", 403)
    data = _json(request)
    index = data.get("index")
    if not isinstance(index, int) or not 0 <= index < len(TASKS):
        return _err("bad index")

    now = timezone.now()
    result, _ = TaskResult.objects.get_or_create(session=session, index=index)

    if data.get("shown"):
        if result.first_shown_at is None:
            result.first_shown_at = now
        session.current_task = index
        session.save(update_fields=["current_task"])
        _log(session, "task_shown", index)

    if "status" in data:
        status = data["status"]
        if status not in TaskResult.STATUSES:
            return _err("bad status")
        if status != result.status:
            result.status = status
            result.finished_at = None if status == TaskResult.PENDING else now
            if result.first_shown_at is None:
                result.first_shown_at = now
            event = {"done": "task_done", "failed": "task_failed", "pending": "task_reset"}[status]
            _log(session, event, index)

    if "comment" in data:
        result.comment = str(data["comment"])[:2000]

    result.save()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def api_event(request, token):
    session = _get_session(token)
    data = _json(request)
    type_ = data.get("type")
    if type_ not in CLIENT_EVENTS:
        return _err("bad event")
    index = data.get("task_index")
    _log(session, type_, index if isinstance(index, int) else None)
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def api_finish(request, token):
    session = _get_session(token)
    data = _json(request)
    rating = data.get("rating")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        return _err("bad rating")
    if session.ended_at is None:
        session.ended_at = timezone.now()
        _log(session, "finish")
    session.rating = rating
    session.suggestions = str(data.get("suggestions", ""))[:3000]
    session.save(update_fields=["ended_at", "rating", "suggestions"])
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def api_recording_start(request, token):
    session = _get_session(token)
    if session.ended_at:
        return _err("finished", 403)
    data = _json(request)
    mime = str(data.get("mime", ""))[:100]
    if not mime.startswith(("video/", "audio/")):
        mime = "video/webm"
    with transaction.atomic():
        number = (session.parts.aggregate(m=Max("number"))["m"] or 0) + 1
        part = RecordingPart.objects.create(
            session=session,
            number=number,
            mime=mime,
            has_video=bool(data.get("has_video")),
            has_audio=bool(data.get("has_audio")),
        )
    part.path.parent.mkdir(parents=True, exist_ok=True)
    _log(session, "recording_start", detail=f"part {number}")
    return JsonResponse({"ok": True, "part": number})


@csrf_exempt
@require_POST
def api_chunk(request, token, part_no):
    """Append one MediaRecorder chunk. Chunks must arrive in order; duplicates are ignored."""
    session = _get_session(token)
    if session.ended_at:
        return _err("finished", 403)
    part = get_object_or_404(RecordingPart, session=session, number=part_no)
    try:
        seq = int(request.GET["seq"])
    except (KeyError, ValueError):
        return _err("bad seq")
    data = request.body
    if not data:
        return _err("empty chunk")

    with transaction.atomic():  # IMMEDIATE transaction mode serializes concurrent writers
        part = RecordingPart.objects.get(pk=part.pk)
        if seq < part.next_seq:  # retry of a chunk that was already stored
            return JsonResponse({"ok": True, "next_seq": part.next_seq})
        if seq > part.next_seq:  # a chunk was lost; the client cannot recover it
            return JsonResponse({"ok": False, "expected": part.next_seq}, status=409)
        part.path.parent.mkdir(parents=True, exist_ok=True)
        with open(part.path, "ab") as fh:
            fh.write(data)
        part.next_seq += 1
        part.save(update_fields=["next_seq", "updated_at"])
    return JsonResponse({"ok": True, "next_seq": part.next_seq})


# --- Manager pages ------------------------------------------------------------------
def manager_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("is_manager"):
            return redirect("manager_login")
        return view(request, *args, **kwargs)

    return wrapper


def manager_login(request):
    error = ""
    if request.method == "POST":
        supplied = request.POST.get("password", "").encode()
        expected = settings.MANAGER_PASSWORD.encode()
        if expected and hmac.compare_digest(supplied, expected):
            request.session.cycle_key()
            request.session["is_manager"] = True
            return redirect("manager_home")
        error = "رمز عبور نادرست است."
    return render(request, "study/manager_login.html", {"error": error})


def manager_logout(request):
    request.session.flush()
    return redirect("manager_login")


@manager_required
def manager_home(request):
    sessions = list(Session.objects.prefetch_related("results"))
    for s in sessions:
        s.invite_url = request.build_absolute_uri(reverse("session", args=[s.token]))
        s.done_count = sum(1 for r in s.results.all() if r.status == "done")
        s.failed_count = sum(1 for r in s.results.all() if r.status == "failed")
    return render(request, "study/manager_home.html", {"sessions": sessions, "total": len(TASKS)})


@manager_required
@require_POST
def manager_create(request):
    n = Session.objects.count() + 1
    while Session.objects.filter(code=f"P{n}").exists():
        n += 1
    Session.objects.create(code=f"P{n}")
    return redirect("manager_home")


@manager_required
def manager_detail(request, pk):
    session = get_object_or_404(Session, pk=pk)
    saved = {r.index: r for r in session.results.all()}
    rows = []
    for i, text in enumerate(TASKS):
        r = saved.get(i)
        rows.append(
            {
                "no": i + 1,
                "text": text,
                "status": r.status if r else "pending",
                "status_label": STATUS_LABELS[r.status if r else "pending"],
                "duration": _fmt_seconds(r.duration_seconds if r else None),
                "comment": r.comment if r else "",
            }
        )

    # Event times are shown relative to the recording part they belong to,
    # so they match the playback position of that part's video.
    parts = list(session.parts.all())
    events = []
    for ev in session.events.all():
        owner = None
        for p in parts:
            if p.created_at <= ev.created_at:
                owner = p
        when = "—"
        if owner:
            secs = int((ev.created_at - owner.created_at).total_seconds())
            when = f"بخش {owner.number} — {secs // 60:02d}:{secs % 60:02d}"
        label = EVENT_LABELS.get(ev.type, ev.type)
        if ev.task_index is not None:
            label += f" ({ev.task_index + 1})"
        events.append({"clock": timezone.localtime(ev.created_at).strftime("%H:%M:%S"), "when": when, "label": label, "detail": ev.detail})

    return render(request, "study/manager_detail.html", {"s": session, "rows": rows, "parts": parts, "events": events})


@manager_required
def manager_export(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="usability-results.csv"'
    response.write("\ufeff")  # BOM so Excel opens Persian text correctly
    writer = csv.writer(response)
    writer.writerow(["participant", "task_no", "task", "status", "duration_s", "first_shown", "finished", "comment", "rating", "suggestions"])
    for s in Session.objects.prefetch_related("results"):
        saved = {r.index: r for r in s.results.all()}
        for i, text in enumerate(TASKS):
            r = saved.get(i)
            writer.writerow(
                [
                    s.code,
                    i + 1,
                    text,
                    r.status if r else "pending",
                    r.duration_seconds if r and r.duration_seconds is not None else "",
                    timezone.localtime(r.first_shown_at).isoformat() if r and r.first_shown_at else "",
                    timezone.localtime(r.finished_at).isoformat() if r and r.finished_at else "",
                    r.comment if r else "",
                    s.rating or "",
                    s.suggestions,
                ]
            )
    return response


def _serve_file(request, path, content_type, download_name=None):
    """Serve a file with HTTP Range support so <video> can seek."""
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    match = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    if size and match and (match.group(1) or match.group(2)):
        first, last = match.groups()
        if first == "":  # suffix range: last N bytes
            start = max(size - int(last), 0)
        else:
            start = int(first)
            if last:
                end = min(int(last), size - 1)
        if start > end or start >= size:
            return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = max(end - start + 1, 0)

    def stream():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                data = fh.read(min(1 << 20, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    response = StreamingHttpResponse(stream(), status=status, content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(length)
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    if download_name:
        response["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return response


@manager_required
def manager_recording(request, pk):
    part = get_object_or_404(RecordingPart, pk=pk)
    if not part.path.exists():
        return HttpResponse("File not found", status=404)
    name = f"{part.session.code}-part{part.number}.{part.extension}" if request.GET.get("download") else None
    return _serve_file(request, part.path, part.content_type, name)
