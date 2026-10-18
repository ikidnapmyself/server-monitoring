"""Admin configuration for alerts models."""

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django_object_actions import DjangoObjectActions
from django_object_actions import action as object_action

from apps.alerts.forms import ADD_SECTION_FIELD, NodePolicyForm
from apps.alerts.models import (
    Alert,
    AlertHistory,
    Incident,
    Node,
)
from apps.alerts.node_overview import (
    SEVERITIES_WORST_FIRST,
    UNRESOLVED_INCIDENT_STATUSES,
    build_node_overview,
    render_severity_chips,
)
from apps.alerts.node_policy import (
    addable_checkers,
    build_effective_policy,
    editor_link,
    field_name,
    scoring_changed,
    sections_for,
    spec_for,
)
from apps.alerts.reeval_existing import (
    ReevalScope,
    apply_reeval,
    preview_reeval,
)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Read-only. Operators read alerts in the console, and nobody resolves one by hand."""

    list_display = ["name", "severity", "status", "source", "node", "incident", "received_at"]
    list_filter = ["status", "severity", "source"]
    search_fields = ["name", "fingerprint", "trace_id"]
    list_select_related = ["node", "incident"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    """Read-only. Operators read and act on incidents in the console."""

    list_display = ["subject", "instance", "severity", "status", "created_at", "resolved_at"]
    list_filter = ["status", "severity"]
    search_fields = ["subject", "instance", "summary"]
    list_select_related = ["node", "pipeline"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    """Read-only audit rows, written by the code that changes an alert."""

    list_display = ["alert", "event", "old_status", "new_status", "created_at"]
    list_filter = ["event"]
    search_fields = ["alert__name"]
    list_select_related = ["alert"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Node)
class NodeAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Registry of agents that have pushed cluster data to this hub.

    The registry fields (instance_id, hostname, …) are written only by the
    ingest path and stay read-only. The one thing an operator edits is
    ``config``, the per-checker hub-side policy used to re-evaluate alert
    severity per node (see apps/alerts/reevaluation.py), and it is edited
    through ``NodePolicyForm``'s labelled boxes rather than as raw JSON.
    """

    change_actions = ["reevaluate_open_alerts"]
    list_display = [
        "instance_id",
        "hostname",
        "incidents",
        "last_source",
        "first_seen",
        "last_seen",
    ]
    search_fields = ["instance_id", "hostname"]
    readonly_fields = [
        "instance_id",
        "hostname",
        "address",
        "last_source",
        "labels",
        "first_seen",
        "last_seen",
    ]
    # Registry only. ``config`` is deliberately absent: it is edited through
    # ``NodePolicyForm``'s per-checker boxes, and a raw JSON widget alongside
    # them would be a second writer for one column, where whichever renders last
    # silently wins.
    fields = [
        "instance_id",
        "hostname",
        "address",
        "last_source",
        "labels",
        "first_seen",
        "last_seen",
    ]
    form = NodePolicyForm
    change_form_template = "admin/alerts/node/change_form.html"
    change_list_template = "admin/alerts/node/change_list.html"

    def get_form(self, request, obj=None, change=False, **kwargs):
        """Let the form own its field list.

        ``_changeform_view`` flattens the fieldsets into
        ``modelform_factory(fields=...)``, and the policy boxes are not model
        fields, so that raises ``FieldError``. It passes ``fields`` explicitly,
        so this overrides rather than defaults. Empty rather than ``None``,
        because ``get_form`` extends ``exclude`` by this list for a staff user
        with view-but-not-change permission, and extending by ``None`` raises.
        No model field is lost either way: every one here is read-only or is
        ``config``, which ``NodePolicyForm`` owns.
        """
        kwargs["fields"] = []
        return super().get_form(request, obj, change=change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        """The registry above, then one labelled box per checker with a policy.

        The sections come from ``sections_for`` rather than from a hand-written
        list, so a node shows the checkers it actually reports and adding a
        scorer adds a section with no edit here. Fields built in the form's
        ``__init__`` never reach ``base_fields``, so the admin cannot discover
        them on its own.

        The select that adds a section goes last, below the sections it grows,
        rather than up in the registry where it would read as another read-only
        registry fact. It is omitted, exactly as the form omits it, when every
        checker already has a section: a select that can only be left blank is
        a control an operator has to work out is useless.
        """
        registry = [(None, {"fields": self.fields})]
        if obj is None or not self.has_change_permission(request, obj):
            # A reader who cannot change gets every fieldset field rendered
            # read-only, and ``AdminReadonlyField`` has no value to read for a
            # field the form adds in ``__init__``, so the boxes came out as
            # "None" directly below a panel saying "Warning at 80". The panel
            # from ``build_effective_policy`` is that reader's whole answer and
            # it is already complete, so the boxes are simply not offered.
            return registry
        sections = sections_for(obj)
        fieldsets = registry + [
            (
                f"{checker.replace('_', ' ')} policy",
                {"fields": [field_name(checker, field.name) for field in spec_for(checker)]},
            )
            for checker in sections
        ]
        if addable_checkers(sections):
            fieldsets.append(
                (
                    "Add a policy section",
                    {
                        "fields": [ADD_SECTION_FIELD],
                        "description": "Set a threshold for a checker this node has not "
                        "reported yet. Saving opens its boxes and changes no scoring.",
                    },
                )
            )
        return fieldsets

    def get_queryset(self, request):
        """Annotate unresolved incident counts, one per severity, in one query.

        ``distinct=True`` carries the weight here: an incident this node raised
        six alerts on is one incident, not six. Every aggregate rides the same
        ``alerts__incident`` join, so listing the whole fleet stays one query.
        """
        annotations = {
            f"unresolved_{severity}": Count(
                "alerts__incident",
                distinct=True,
                filter=Q(
                    alerts__incident__status__in=UNRESOLVED_INCIDENT_STATUSES,
                    alerts__incident__severity=severity,
                ),
            )
            for severity in SEVERITIES_WORST_FIRST
        }
        annotations["unresolved_total"] = Count(
            "alerts__incident",
            distinct=True,
            filter=Q(alerts__incident__status__in=UNRESOLVED_INCIDENT_STATUSES),
        )
        return super().get_queryset(request).annotate(**annotations)

    @admin.display(description="Incidents", ordering="unresolved_total")
    def incidents(self, obj):
        """Unresolved incident counts for this node, worst severity first."""
        return render_severity_chips(obj)

    def has_add_permission(self, request):
        """Nodes are written by code, never added in admin.

        Two writers: the ingest path (a cluster push registers its sender) and
        ``CheckAlertBridge`` (a local check run registers this machine).
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """Never delete Node rows from admin — that would silently drop the
        operator-authored ``config`` policy (a later push re-creates the row
        without it)."""
        return False

    @staticmethod
    def _requested_scope(obj, checker):
        """The one checker the caller asked for, or the whole node.

        ``?checker=`` is what the policy overview's per-row button carries, and a
        policy-page link can go stale between render and click. A present parameter
        is honoured whether or not it matches anything: widening a stale link to the
        whole node would re-evaluate, and now notify about, alerts nobody asked
        about. Only an absent parameter means node-wide.
        """
        if checker:
            return ReevalScope.for_checker(obj, checker)
        return ReevalScope.for_node(obj)

    @object_action(
        label="Re-evaluate open alerts",
        description="Re-score this node's open alerts against its current config",
    )
    def reevaluate_open_alerts(self, request, obj):
        """Preview (then, on POST confirm) re-evaluate this node's open alerts.

        The confirm form posts back to this same URL, so a ``?checker=`` narrowing
        survives the confirm without being re-stated in the form.
        """
        # django_object_actions gates the URL behind admin_view (is_staff only);
        # enforce model change permission before any mutation.
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        scope = self._requested_scope(obj, request.GET.get("checker", ""))
        report = preview_reeval(scope)
        # A scope with skips and no changes still owes the operator the sentences
        # saying why; only a node-wide scope that found nothing at all gets the
        # one-liner. A checker the operator named is answered on the page, even
        # when the answer is that it has no open alerts.
        if not report.changes and not report.skips and not scope.checker:
            self.message_user(request, "No open alerts need re-evaluation.")
            return
        if report.changes and request.method == "POST" and request.POST.get("confirm"):
            applied = apply_reeval(scope)
            self.message_user(
                request,
                f"Resolved {applied.resolved_count}; changed severity on "
                f"{applied.severity_changed_count}. Enqueued {applied.run_count} "
                "pipeline run(s).",
            )
            return
        return TemplateResponse(
            request,
            "admin/alerts/node/reevaluate_confirm.html",
            {
                **self.admin_site.each_context(request),
                "report": report,
                "title": "Confirm re-evaluation",
                "opts": self.model._meta,
                "intro": (
                    f"Re-evaluate the open {scope.checker} alerts for node "
                    f"{obj.instance_id} against its current config."
                    if scope.checker
                    else (
                        f"Re-evaluate the open alerts for node {obj.instance_id} against "
                        "its current config."
                    )
                ),
                "parent_url": reverse(
                    "admin:alerts_node_changelist", current_app=self.admin_site.name
                ),
                "parent_label": "Nodes",
                "subject_label": obj.instance_id,
                "back_url": reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[obj.pk],
                    current_app=self.admin_site.name,
                ),
            },
        )

    # Django's own response_change branches on these before falling through to
    # the changelist. Any one of them means the operator asked to go somewhere
    # specific, so the redirect below leaves them alone.
    _EXPLICIT_DESTINATIONS = ["_continue", "_saveasnew", "_addanother", "_popup"]

    def save_model(self, request, obj, form, change):
        """Save, and remember whether the save can re-score anything.

        The verdict has to be taken here. ``ModelAdmin._changeform_view`` calls
        ``form.save(commit=False)`` before this, so ``obj.config`` already holds
        the new policy, and the stored one only exists in the database. It is
        carried on the request rather than on ``self``: a ModelAdmin is one
        shared instance serving every request.
        """
        stored = self.model.objects.filter(pk=obj.pk).values_list("config", flat=True).first()
        request.node_policy_scoring_changed = scoring_changed(stored, obj.config)
        # Carried the same way and for the same reason: ``response_change`` needs
        # to know the operator asked for boxes, and only the form saw the select.
        request.node_policy_section_added = form.cleaned_data.get(ADD_SECTION_FIELD) or ""
        super().save_model(request, obj, form, change)

    def response_change(self, request, obj):
        """A save that changed scoring lands on the re-evaluate preview.

        Saving a threshold does nothing to the alerts already open; that takes a
        second, easily forgotten click, and a policy nobody re-evaluates is the
        same silent no-op the boxes above exist to kill. So the save hands the
        operator the preview rather than the changelist.

        Still two deliberate acts: this is the action's GET, which shows what
        would change and waits for the confirm. It must never apply the
        re-evaluation, because a fat-fingered threshold that resolves real
        incidents with no preview is worse than the button nobody presses.

        A save that only opened a section lands back on the boxes it opened,
        because "show me those boxes" is the whole content of that request and
        the changelist means finding the node again to use what you just asked
        for. An empty entry scores nothing, so one save can ask for both and the
        preview above takes it: that is the half with consequences for alerts
        already open, and the boxes are one click away either way.

        Only the plain Save is redirected. ``_continue`` and friends mean the
        operator said where they were going, and ``super`` still owns every
        other case, including an invalid form.
        """
        response = super().response_change(request, obj)
        if any(key in request.POST for key in self._EXPLICIT_DESTINATIONS):
            return response
        if getattr(request, "node_policy_scoring_changed", False):
            self.message_user(
                request,
                "This policy does not touch the alerts already open. Here is what "
                "re-evaluating them would do.",
            )
            # tools_view_name is django_object_actions' own name for the URL it
            # registers for the change actions, so the button on the page and this
            # redirect cannot drift apart.
            return HttpResponseRedirect(
                reverse(
                    self.tools_view_name,
                    kwargs={"pk": obj.pk, "tool": "reevaluate_open_alerts"},
                )
            )
        added = getattr(request, "node_policy_section_added", "")
        if added:
            # One save can do both. The preview above wins: it is the half with
            # consequences for alerts already open, and the boxes are one click
            # away and score nothing until they are filled in.
            self.message_user(request, f"Opened the {added.replace('_', ' ')} policy boxes.")
            return HttpResponseRedirect(
                reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[obj.pk],
                    current_app=self.admin_site.name,
                )
            )
        return response

    def render_change_form(self, request, context, *args, obj=None, **kwargs):
        """Attach the overview panels; the form below is unchanged.

        ``obj`` is None on the add view, which NodeAdmin forbids anyway; the
        guard costs nothing and the template's ``{% if node_overview %}``
        depends on it.
        """
        if obj is not None:
            context["node_overview"] = build_node_overview(obj)
            # Rendered for every reader, not only the view-only one. An operator
            # editing thresholds needs "what is scoring today" stated apart from
            # the boxes that will change it, and a panel that appears for some
            # users is a condition that has to stay right.
            context["node_policy"] = build_effective_policy(obj)
            # Only for a reader who will actually get boxes. get_fieldsets drops
            # every policy section for view-only, so a jump link there would
            # point at an id that is never rendered.
            if self.has_change_permission(request, obj):
                context["node_policy_editor"] = editor_link(obj)
        return super().render_change_form(request, context, *args, obj=obj, **kwargs)
