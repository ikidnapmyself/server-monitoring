"""Tests for the per-checker context-key registry."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.alerts import context_keys
from apps.alerts.context_keys import MAX_PLAIN_KEY_LENGTH, context_key_for


class ContextKeyTests(SimpleTestCase):
    def test_unregistered_checker_has_no_key(self):
        self.assertEqual(context_key_for("cpu", {"percent": "91.2"}), "")

    def test_missing_checker_label_has_no_key(self):
        self.assertEqual(context_key_for("", {}), "")

    def test_listening_ports_key_is_the_sorted_unexpected_set(self):
        annotations = {"unexpected_ports": "[8080, 22]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22,8080")

    def test_listening_ports_key_is_order_independent(self):
        first = context_key_for("listening_ports", {"unexpected_ports": "[22, 8080]"})
        second = context_key_for("listening_ports", {"unexpected_ports": "[8080, 22]"})
        self.assertEqual(first, second)

    def test_listening_ports_with_no_flagged_ports(self):
        """A clean scan is namespaced, not empty: "nothing flagged" != "no key".

        `""` means "this module cannot say"; `"listening_ports:"` is a real
        situation that must compare unequal to a set of flagged ports.
        """
        key = context_key_for("listening_ports", {"unexpected_ports": "[]"})
        self.assertEqual(key, "listening_ports:")
        self.assertNotEqual(key, "")

    def test_unparseable_metrics_fall_back_to_no_key(self):
        self.assertEqual(context_key_for("listening_ports", {"unexpected_ports": "junk"}), "")

    def test_non_dict_annotations_are_safe(self):
        self.assertEqual(context_key_for("listening_ports", None), "")


class AnnotationShapeTests(SimpleTestCase):
    """Both real producers must work: the node push and the hub-local bridge.

    `apps.alerts.drivers.cluster` stashes the whole metrics dict as a JSON string
    under `annotations["metrics"]`; `apps.alerts.check_integration` writes one
    `str(value)` annotation per metric key with no `metrics` blob at all.
    """

    def test_nested_metrics_blob_from_the_cluster_driver(self):
        annotations = {"metrics": '{"unexpected_ports": [8080, 22], "listening_count": 9}'}
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22,8080")

    def test_flat_annotations_exactly_as_the_bridge_writes_them(self):
        """Build the annotations the way `check_integration` really does.

        `apps/alerts/check_integration.py:163-165` is `{k: str(v) for k, v in
        result.metrics.items()}`. Hand-writing the expected strings would hide a
        producer change; this derives them from a realistic metrics dict, the shape
        `apps/checkers/checkers/listening_ports.py:150-171` returns.
        """
        metrics = {
            "platform": "darwin",
            "listening_count": 3,
            "allowlist": [22, 443],
            "unexpected_ports": [8080, 22],
            "listening": [
                {"port": 22, "address": "0.0.0.0", "exposed": True, "process": "sshd"},
                {"port": 8080, "address": "0.0.0.0", "exposed": True, "process": "node"},
            ],
        }
        annotations = {key: str(value) for key, value in metrics.items()}
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22,8080")

    def test_nested_blob_wins_over_a_flat_key_of_the_same_name(self):
        annotations = {
            "metrics": '{"unexpected_ports": [22]}',
            "unexpected_ports": "[9999]",
        }
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22")

    def test_unparseable_metrics_blob_falls_back_to_the_flat_shape(self):
        annotations = {"metrics": "junk", "unexpected_ports": "[22]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22")

    def test_missing_metric_key_has_no_key(self):
        self.assertEqual(context_key_for("listening_ports", {"listening_count": "9"}), "")

    def test_non_list_metric_value_has_no_key(self):
        self.assertEqual(context_key_for("listening_ports", {"unexpected_ports": "22"}), "")

    def test_non_string_non_list_metric_value_has_no_key(self):
        annotations = {"metrics": '{"unexpected_ports": 22}'}
        self.assertEqual(context_key_for("listening_ports", annotations), "")

    def test_non_integer_ports_are_ignored(self):
        annotations = {"metrics": '{"unexpected_ports": [22, "http", true, null, 8080]}'}
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22,8080")

    def test_duplicate_ports_collapse(self):
        annotations = {"unexpected_ports": "[22, 22, 8080]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "listening_ports:22,8080")


class NormalisationTests(SimpleTestCase):
    """Builders see one shape, whichever producer wrote the annotations.

    Value-level decoding happens once here rather than in every builder, so the
    next registered checker does not have to re-solve `91.2` vs `"91.2"`.
    """

    def _metrics_seen_by_builder(self, annotations):
        seen = {}

        def probe(metrics):
            seen.update(metrics)
            return "probe"

        with patch.dict(context_keys.KEY_BUILDERS, {"listening_ports": probe}):
            context_key_for("listening_ports", annotations)
        return seen

    def test_flat_strings_are_decoded_to_native_values(self):
        seen = self._metrics_seen_by_builder(
            {"percent": "91.2", "count": "3", "flag": "true", "ports": "[22]"}
        )
        self.assertEqual(seen, {"percent": 91.2, "count": 3, "flag": True, "ports": [22]})

    def test_already_native_values_are_left_alone(self):
        """`apps.alerts.drivers.cluster` passes a driver's `annotations` through as
        arbitrary JSON, so a value can already be a list or number, not a string."""
        seen = self._metrics_seen_by_builder({"ports": [22], "count": 3, "nothing": None})
        self.assertEqual(seen, {"ports": [22], "count": 3, "nothing": None})

    def test_non_json_strings_survive_unchanged(self):
        seen = self._metrics_seen_by_builder({"platform": "darwin", "host": "web-01"})
        self.assertEqual(seen, {"platform": "darwin", "host": "web-01"})

    def test_nested_blob_values_are_passed_through_already_decoded(self):
        seen = self._metrics_seen_by_builder({"metrics": '{"percent": 91.2, "host": "web-01"}'})
        self.assertEqual(seen, {"percent": 91.2, "host": "web-01"})


class KeyLengthBoundTests(SimpleTestCase):
    """`Alert.context_key` is a CharField(max_length=255).

    Truncating would let two different port sets sharing a prefix collide, and the
    change gate would then silently skip a run for a genuinely new port. So the
    long case digests instead of truncating: bounded length, still injective.
    """

    def test_short_set_stays_readable(self):
        ports = list(range(1, 21))
        key = context_key_for("listening_ports", {"unexpected_ports": str(ports)})
        self.assertEqual(key, "listening_ports:" + ",".join(str(p) for p in ports))
        self.assertLessEqual(len(key), MAX_PLAIN_KEY_LENGTH)

    def test_long_set_is_digested_and_bounded(self):
        ports = list(range(9000, 9100))
        key = context_key_for("listening_ports", {"unexpected_ports": str(ports)})
        self.assertTrue(key.startswith("sha256:"), key)
        self.assertLessEqual(len(key), 255)

    def test_different_long_sets_produce_different_keys(self):
        first = context_key_for(
            "listening_ports", {"unexpected_ports": str(list(range(9000, 9100)))}
        )
        second = context_key_for(
            "listening_ports", {"unexpected_ports": str(list(range(9000, 9099)) + [9200])}
        )
        self.assertTrue(first.startswith("sha256:"))
        self.assertTrue(second.startswith("sha256:"))
        self.assertNotEqual(first, second)

    def test_long_sets_are_still_order_independent(self):
        ports = list(range(9000, 9100))
        first = context_key_for("listening_ports", {"unexpected_ports": str(ports)})
        second = context_key_for(
            "listening_ports", {"unexpected_ports": str(list(reversed(ports)))}
        )
        self.assertEqual(first, second)

    def test_the_bound_applies_to_every_builder_not_just_this_one(self):
        """The bound is a module invariant, not a convention builders opt into.

        A builder that forgets it would otherwise write an over-255 value into
        `Alert.context_key` — silent on SQLite, `DataError` on PostgreSQL.
        """
        with patch.dict(context_keys.KEY_BUILDERS, {"listening_ports": lambda m: "x" * 500}):
            key = context_key_for("listening_ports", {})
        self.assertTrue(key.startswith("sha256:"))
        self.assertLessEqual(len(key), 255)

    def test_a_non_string_builder_return_is_coerced(self):
        with patch.dict(context_keys.KEY_BUILDERS, {"listening_ports": lambda m: 1234}):
            self.assertEqual(context_key_for("listening_ports", {}), "1234")


class ColumnFitTests(TestCase):
    def test_the_bound_leaves_headroom_under_the_column(self):
        """Pin the bound to the column so shrinking the field fails loudly here."""
        from apps.alerts.models import Alert

        max_length = Alert._meta.get_field("context_key").max_length
        self.assertLess(MAX_PLAIN_KEY_LENGTH, max_length)


class FailOpenTests(SimpleTestCase):
    def test_a_raising_builder_degrades_to_no_key(self):
        """A broken builder must never raise into ingest — it must lose the key."""

        def boom(metrics):
            raise RuntimeError("bad key builder")

        with patch.dict(context_keys.KEY_BUILDERS, {"listening_ports": boom}):
            with self.assertLogs("apps.alerts.context_keys", level="ERROR"):
                self.assertEqual(context_key_for("listening_ports", {}), "")
