from django.urls import path

from .views import DailyCollectionView

urlpatterns = [
    path("daily-collection/", DailyCollectionView.as_view(), name="daily_collection"),
]
