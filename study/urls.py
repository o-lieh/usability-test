from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("s/<str:token>/", views.session_page, name="session"),
    # Participant API (the token in the URL identifies the session)
    path("api/<str:token>/begin/", views.api_begin),
    path("api/<str:token>/task/", views.api_task),
    path("api/<str:token>/event/", views.api_event),
    path("api/<str:token>/finish/", views.api_finish),
    path("api/<str:token>/recording/start/", views.api_recording_start),
    path("api/<str:token>/recording/<int:part_no>/chunk/", views.api_chunk),
    # Manager pages
    path("manage/login/", views.manager_login, name="manager_login"),
    path("manage/logout/", views.manager_logout, name="manager_logout"),
    path("manage/", views.manager_home, name="manager_home"),
    path("manage/create/", views.manager_create, name="manager_create"),
    path("manage/s/<int:pk>/", views.manager_detail, name="manager_detail"),
    path("manage/export.csv", views.manager_export, name="manager_export"),
    path("manage/recording/<int:pk>/", views.manager_recording, name="manager_recording"),
]
