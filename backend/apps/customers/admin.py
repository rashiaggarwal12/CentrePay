from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "email", "centre", "created_at"]
    list_filter = ["centre"]
    search_fields = ["name", "phone", "email"]
