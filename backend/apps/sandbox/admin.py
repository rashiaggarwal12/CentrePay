from django.contrib import admin

from .models import FakeLink, FakePayment, FakeRefund

admin.site.register([FakeLink, FakePayment, FakeRefund])
