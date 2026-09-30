from django.conf import settings
from django.db import models


class Centre(models.Model):
    name = models.CharField(max_length=120)
    # Invoice-number prefix, e.g. "BLR1" -> BLR1/2627/000042. Max 4 chars keeps the
    # full number within GST's 16-character limit.
    code = models.CharField(max_length=4, unique=True)
    city = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Staff(models.Model):
    class Role(models.TextChoices):
        FRONT_DESK = "front_desk", "Front desk"
        MANAGER = "manager", "Manager"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff"
    )
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="staff")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.FRONT_DESK)

    class Meta:
        verbose_name_plural = "staff"

    def __str__(self):
        return f"{self.user.get_username()} @ {self.centre.code} ({self.role})"

    @property
    def is_manager(self) -> bool:
        return self.role == self.Role.MANAGER
