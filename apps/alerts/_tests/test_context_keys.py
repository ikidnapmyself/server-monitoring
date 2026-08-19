"""Tests for the per-checker context-key registry."""

from django.test import SimpleTestCase

from apps.alerts.context_keys import MAX_PLAIN_KEY_LENGTH, context_key_for


class ContextKeyTests(SimpleTestCase):
    def test_unregistered_checker_has_no_key(self):
        self.assertEqual(context_key_for("cpu", {"percent": "91.2"}), "")

    def test_missing_checker_label_has_no_key(self):
        self.assertEqual(context_key_for("", {}), "")

    def test_listening_ports_key_is_the_sorted_unexpected_set(self):
        annotations = {"unexpected_ports": "[8080, 22]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "22,8080")

    def test_listening_ports_key_is_order_independent(self):
        first = context_key_for("listening_ports", {"unexpected_ports": "[22, 8080]"})
        second = context_key_for("listening_ports", {"unexpected_ports": "[8080, 22]"})
        self.assertEqual(first, second)

    def test_listening_ports_with_no_flagged_ports(self):
        self.assertEqual(context_key_for("listening_ports", {"unexpected_ports": "[]"}), "")

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
        self.assertEqual(context_key_for("listening_ports", annotations), "22,8080")

    def test_nested_blob_wins_over_a_flat_key_of_the_same_name(self):
        annotations = {
            "metrics": '{"unexpected_ports": [22]}',
            "unexpected_ports": "[9999]",
        }
        self.assertEqual(context_key_for("listening_ports", annotations), "22")

    def test_unparseable_metrics_blob_falls_back_to_the_flat_shape(self):
        annotations = {"metrics": "junk", "unexpected_ports": "[22]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "22")

    def test_missing_metric_key_has_no_key(self):
        self.assertEqual(context_key_for("listening_ports", {"listening_count": "9"}), "")

    def test_non_list_metric_value_has_no_key(self):
        self.assertEqual(context_key_for("listening_ports", {"unexpected_ports": "22"}), "")

    def test_non_string_non_list_metric_value_has_no_key(self):
        annotations = {"metrics": '{"unexpected_ports": 22}'}
        self.assertEqual(context_key_for("listening_ports", annotations), "")

    def test_non_integer_ports_are_ignored(self):
        annotations = {"metrics": '{"unexpected_ports": [22, "http", true, null, 8080]}'}
        self.assertEqual(context_key_for("listening_ports", annotations), "22,8080")

    def test_duplicate_ports_collapse(self):
        annotations = {"unexpected_ports": "[22, 22, 8080]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "22,8080")


class KeyLengthBoundTests(SimpleTestCase):
    """`Alert.context_key` is a CharField(max_length=255).

    Truncating would let two different port sets sharing a prefix collide, and the
    change gate would then silently skip a run for a genuinely new port. So the
    long case digests instead of truncating: bounded length, still injective.
    """

    def test_short_set_stays_readable(self):
        ports = list(range(1, 21))
        key = context_key_for("listening_ports", {"unexpected_ports": str(ports)})
        self.assertEqual(key, ",".join(str(p) for p in ports))
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


class FailOpenTests(SimpleTestCase):
    def test_a_raising_builder_degrades_to_no_key(self):
        """A broken builder must never raise into ingest — it must lose the key."""
        from apps.alerts import context_keys

        def boom(metrics):
            raise RuntimeError("bad key builder")

        original = context_keys.CONTEXT_KEYS["listening_ports"]
        context_keys.CONTEXT_KEYS["listening_ports"] = boom
        try:
            with self.assertLogs("apps.alerts.context_keys", level="ERROR"):
                self.assertEqual(context_key_for("listening_ports", {}), "")
        finally:
            context_keys.CONTEXT_KEYS["listening_ports"] = original
