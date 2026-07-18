"""Modelo de datos §3. La DB es la fuente de verdad de toda la configuración;
el renderer (Fase 2) la materializa a los archivos que Claude Code lee."""

from __future__ import annotations

import uuid

from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ModelProfile(TimestampedModel):
    class Provider(models.TextChoices):
        ANTHROPIC = "anthropic"
        MINIMAX = "minimax"
        CUSTOM = "custom"

    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=20, choices=Provider.choices)
    base_url = models.URLField(null=True, blank=True)  # null = default del provider
    auth_token_enc = models.BinaryField(null=True, blank=True)  # Fernet
    model = models.CharField(max_length=200)
    extra_env = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.name


class PermissionPolicy(TimestampedModel):
    class Mode(models.TextChoices):
        AUTO = "auto"
        APPROVE = "approve"

    name = models.CharField(max_length=100, unique=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.APPROVE)
    # Patrones estilo "Bash(git commit:*)".
    allowed_tools = models.JSONField(default=list, blank=True)
    # Se SUMAN a las deny obligatorias, nunca las reemplazan.
    deny_rules = models.JSONField(default=list, blank=True)

    def __str__(self) -> str:
        return self.name


class Project(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    path = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    # Timeout de aprobaciones de permisos (§4.2). null = usa el default global
    # settings.PERMISSION_TIMEOUT_SECONDS (15 min).
    permission_timeout_seconds = models.PositiveIntegerField(null=True, blank=True)
    telegram_topic_id = models.IntegerField(null=True, blank=True)
    github_repo = models.CharField(max_length=200, null=True, blank=True)
    model_profile = models.ForeignKey(
        ModelProfile, on_delete=models.PROTECT, related_name="projects"
    )
    permission_policy = models.ForeignKey(
        PermissionPolicy, on_delete=models.PROTECT, related_name="projects"
    )

    def __str__(self) -> str:
        return self.slug


class Skill(TimestampedModel):
    class Scope(models.TextChoices):
        GLOBAL = "global"
        PROJECT = "project"

    name = models.CharField(max_length=100)
    scope = models.CharField(max_length=10, choices=Scope.choices)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, null=True, blank=True, related_name="skills"
    )
    content = models.TextField()
    enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.scope})"


class McpServer(TimestampedModel):
    class Scope(models.TextChoices):
        GLOBAL = "global"
        PROJECT = "project"

    class Transport(models.TextChoices):
        STDIO = "stdio"
        HTTP = "http"

    name = models.CharField(max_length=100)
    scope = models.CharField(max_length=10, choices=Scope.choices)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, null=True, blank=True, related_name="mcp_servers"
    )
    transport = models.CharField(max_length=10, choices=Transport.choices)
    # stdio: {command, args, env}; http: {url}
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.scope})"


class Session(TimestampedModel):
    class Status(models.TextChoices):
        STARTING = "starting"
        RUNNING = "running"
        WAITING_APPROVAL = "waiting_approval"
        IDLE = "idle"
        STOPPED = "stopped"
        CRASHED = "crashed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="sessions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STARTING)
    sdk_session_id = models.CharField(max_length=200, null=True, blank=True)
    model_reported = models.CharField(max_length=200, null=True, blank=True)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return str(self.id)


class Event(TimestampedModel):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="events")
    seq = models.BigIntegerField()  # monotónico por sesión, lo asigna el worker
    type = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    ts = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "seq"], name="uniq_session_seq"),
        ]
        indexes = [models.Index(fields=["session", "seq"])]
        ordering = ["session", "seq"]

    def __str__(self) -> str:
        return f"{self.session_id}#{self.seq} {self.type}"


class PermissionRequest(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        ALLOWED = "allowed"
        DENIED = "denied"
        ALLOWED_ALWAYS = "allowed_always"
        EXPIRED = "expired"

    class ResolvedBy(models.TextChoices):
        WEB = "web"
        TELEGRAM = "telegram"
        TIMEOUT = "timeout"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="permission_requests"
    )
    tool = models.CharField(max_length=100)
    input_full = models.JSONField(default=dict)
    input_preview = models.CharField(max_length=500)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    resolved_by = models.CharField(max_length=10, choices=ResolvedBy.choices, null=True, blank=True)
    tg_message_id = models.IntegerField(null=True, blank=True)
    expires_at = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.tool} [{self.status}]"


class Config(TimestampedModel):
    """Config key-value de plataforma (Fase 4): secret del webhook de Telegram,
    chat_id del grupo, topic 'sistema'. No para secretos de modelo (esos van
    cifrados en ModelProfile)."""

    key = models.CharField(max_length=100, primary_key=True)
    value = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.key

    @classmethod
    def get(cls, key: str, default: str | None = None) -> str | None:
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        cls.objects.update_or_create(key=key, defaults={"value": value})


class PortRegistry(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active"
        RELEASED = "released"

    port = models.IntegerField(unique=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ports")
    purpose = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    allocated_by_session = models.UUIDField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.port} ({self.status})"
