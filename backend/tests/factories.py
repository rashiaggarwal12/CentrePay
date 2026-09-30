import factory
from django.contrib.auth import get_user_model

from apps.accounts.models import Centre, Staff
from apps.billing.models import Service
from apps.customers.models import Customer


class CentreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Centre

    name = factory.Sequence(lambda n: f"Centre {n}")
    code = factory.Sequence(lambda n: f"C{n:03d}")
    city = "Bengaluru"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()

    username = factory.Sequence(lambda n: f"user{n}")
    password = factory.django.Password("pass12345")


class StaffFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Staff

    user = factory.SubFactory(UserFactory)
    centre = factory.SubFactory(CentreFactory)
    role = Staff.Role.FRONT_DESK


class ServiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Service

    centre = factory.SubFactory(CentreFactory)
    name = factory.Sequence(lambda n: f"Service {n}")
    price_paise = 100_000  # ₹1,000.00
    gst_rate_bps = 1800


class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer

    centre = factory.SubFactory(CentreFactory)
    name = factory.Faker("name")
    phone = factory.Sequence(lambda n: f"+9198{n:08d}")
