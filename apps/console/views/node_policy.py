from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.forms import ADD_SECTION_FIELD
from apps.alerts.node_policy import StalePolicy, editor_anchor, save_policy, scoring_changed
from apps.checkers.checkers import checker_label
from apps.console.queries.node import PolicyEditor, build_policy_editor
from apps.console.views.base import Notice
from apps.console.views.node import CHANGE_REQUIRED, NodePageView

STALE = "This node's policy changed since you opened it. Here it is now."
SCORING_CHANGED = (
    "This policy doesn't touch the alerts already open. "
    "Here is what re-evaluating them would do."
)
SAVED = "Policy saved."


class NodePolicyView(NodePageView):
    """Edit one node's policy. The rules live in ``NodePolicyForm`` and ``save_policy``."""

    template_name = "console/node_policy.html"
    partial_template_name = "console/partials/_node_policy.html"

    def landing_path(self) -> str:
        return reverse("console:node-policy", args=[self.kwargs["pk"]])

    def render_editor(
        self, editor: PolicyEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return self.not_found()
        context = self.get_context_data(editor=editor, nav="nodes", notice=notice)
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        if not self.can_change:
            return HttpResponseForbidden(CHANGE_REQUIRED)
        return self.render_editor(build_policy_editor(kwargs["pk"]))

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return HttpResponseForbidden(CHANGE_REQUIRED)
        pk = kwargs["pk"]
        digest = request.POST.get("digest", "")
        editor = build_policy_editor(pk, request.POST, digest=digest)
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        config = editor.form.policy_config
        try:
            stored = save_policy(pk, config, expected=digest)
        except StalePolicy:
            return self.render_editor(build_policy_editor(pk), Notice(STALE, "warning"), 409)
        if scoring_changed(stored, config):
            # The preview's GET, never its apply: a mistyped threshold must not resolve
            # incidents unseen.
            messages.info(request, SCORING_CHANGED)
            return HttpResponseRedirect(reverse("console:node-reeval", args=[pk]), status=303)
        added = editor.form.cleaned_data.get(ADD_SECTION_FIELD)
        if added:
            messages.success(request, f"Opened the {checker_label(added)} policy boxes.")
            return HttpResponseRedirect(f"{self.landing_path()}#{editor_anchor(added)}", status=303)
        messages.success(request, SAVED)
        return HttpResponseRedirect(reverse("console:node", args=[pk]), status=303)
