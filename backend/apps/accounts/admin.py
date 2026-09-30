from django.contrib import admin

from .models import Centre, Staff


@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "city", "is_active"]
    list_filter = ["is_active", "city"]
    search_fields = ["name", "code"]


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ["user", "centre", "role"]
    list_filter = ["centre", "role"]
    search_fields = ["user__username", "user__first_name", "user__last_name"]
    autocomplete_fields = ["user", "centre"]
