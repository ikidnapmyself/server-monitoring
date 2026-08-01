from django.test import TestCase

from apps.orchestration.models import PipelineDefinition


class PipelineMatchesTests(TestCase):
    def _p(self, match):
        return PipelineDefinition(name="p", match=match)

    def test_empty_match_is_catch_all(self):
        self.assertTrue(self._p([]).matches({"source": "anything"}))

    def test_is(self):
        p = self._p([{"field": "source", "op": "is", "value": "grafana"}])
        self.assertTrue(p.matches({"source": "grafana"}))
        self.assertFalse(p.matches({"source": "cluster"}))

    def test_is_not(self):
        p = self._p([{"field": "source", "op": "is-not", "value": "noisy"}])
        self.assertTrue(p.matches({"source": "grafana"}))
        self.assertFalse(p.matches({"source": "noisy"}))

    def test_in(self):
        p = self._p([{"field": "severity", "op": "in", "value": ["critical", "warning"]}])
        self.assertTrue(p.matches({"severity": "critical"}))
        self.assertFalse(p.matches({"severity": "info"}))

    def test_not_in(self):
        p = self._p([{"field": "severity", "op": "not-in", "value": ["info"]}])
        self.assertTrue(p.matches({"severity": "critical"}))
        self.assertFalse(p.matches({"severity": "info"}))

    def test_label_lookup(self):
        p = self._p([{"field": "label:env", "op": "is", "value": "prod"}])
        self.assertTrue(p.matches({"labels": {"env": "prod"}}))
        self.assertFalse(p.matches({"labels": {"env": "dev"}}))
        self.assertFalse(p.matches({"labels": {}}))

    def test_multiple_conditions_are_anded(self):
        p = self._p(
            [
                {"field": "source", "op": "is", "value": "cluster"},
                {"field": "severity", "op": "in", "value": ["critical"]},
            ]
        )
        self.assertTrue(p.matches({"source": "cluster", "severity": "critical"}))
        self.assertFalse(p.matches({"source": "cluster", "severity": "info"}))

    def test_default_op_is_equality(self):
        # op omitted defaults to "is".
        p = self._p([{"field": "source", "value": "grafana"}])
        self.assertTrue(p.matches({"source": "grafana"}))
        self.assertFalse(p.matches({"source": "other"}))
