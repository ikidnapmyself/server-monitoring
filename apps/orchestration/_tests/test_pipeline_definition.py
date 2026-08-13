# apps/orchestration/_tests/test_pipeline_definition.py
"""Tests for PipelineDefinition model."""

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.orchestration.models import PipelineDefinition


class TestPipelineDefinition(TestCase):
    """Tests for PipelineDefinition model."""

    def test_create_minimal_definition(self):
        """Test creating a minimal pipeline definition."""
        definition = PipelineDefinition.objects.create(
            name="test-pipeline",
            description="Test pipeline",
        )
        assert definition.id is not None
        assert definition.name == "test-pipeline"
        assert definition.is_active is True
        assert definition.version == 1

    def test_unique_name_constraint(self):
        """Test that pipeline names must be unique."""
        PipelineDefinition.objects.create(name="unique-test")
        with pytest.raises(Exception):  # IntegrityError
            PipelineDefinition.objects.create(name="unique-test")

    def test_str_representation(self):
        """Test string representation."""
        definition = PipelineDefinition.objects.create(name="my-pipeline")
        assert "my-pipeline" in str(definition)


class TestPipelineDefinitionStagesValidation(TestCase):
    """``stages`` is validated as a data shape: known values, canonical order, no dupes."""

    #: Fields irrelevant to these assertions (M2Ms are not validated by full_clean anyway).
    EXCLUDE = ["name", "match", "tags"]

    def _clean(self, stages):
        PipelineDefinition(name="v", stages=stages).full_clean(exclude=self.EXCLUDE)

    def test_canonical_full_list_is_valid(self):
        self._clean(["check", "analyze", "notify"])

    def test_single_stage_is_valid(self):
        self._clean(["notify"])

    def test_empty_list_is_valid(self):
        self._clean([])

    def test_ingest_is_not_a_routable_stage(self):
        """INGEST is deliberately absent: a lane is resolved after the entry stage ran."""
        assert "ingest" not in PipelineDefinition.ROUTABLE_STAGES
        with pytest.raises(ValidationError) as exc:
            self._clean(["ingest"])
        assert "stages" in exc.value.message_dict
        assert "Unknown stage" in exc.value.message_dict["stages"][0]

    def test_unknown_stage_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._clean(["check", "sparkle"])
        assert "sparkle" in exc.value.message_dict["stages"][0]

    def test_out_of_order_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._clean(["notify", "check"])
        assert "order" in exc.value.message_dict["stages"][0]

    def test_duplicates_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._clean(["check", "check"])
        assert "Duplicate" in exc.value.message_dict["stages"][0]

    def test_non_list_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._clean("notify")
        assert "must be a list" in exc.value.message_dict["stages"][0]

    def test_default_is_empty_list(self):
        definition = PipelineDefinition.objects.create(name="defaulted")
        definition.refresh_from_db()
        assert definition.stages == []

    def test_stages_round_trip_through_the_database(self):
        """The value an operator saves is the value a reader gets back."""
        PipelineDefinition.objects.create(name="lane", stages=["check", "notify"])
        assert PipelineDefinition.objects.get(name="lane").stages == ["check", "notify"]


class TestRoutableStagesNormalisation(TestCase):
    """``routable_stages()`` is the trust boundary: readers never touch the raw column."""

    def _lane(self, stages):
        # objects.create bypasses clean(), exactly like a fixture or a shell edit.
        return PipelineDefinition.objects.create(name="junk-lane", stages=stages)

    def test_clean_value_passes_through(self):
        assert self._lane(["check", "notify"]).routable_stages() == ["check", "notify"]

    def test_unknown_values_are_dropped(self):
        assert self._lane(["check", "sparkle", "notify"]).routable_stages() == ["check", "notify"]

    def test_out_of_order_value_is_forced_into_canonical_order(self):
        assert self._lane(["notify", "check"]).routable_stages() == ["check", "notify"]

    def test_duplicates_collapse(self):
        assert self._lane(["check", "check"]).routable_stages() == ["check"]

    def test_bare_string_does_not_substring_match(self):
        """A junk row holding a string must not read as containing its own substrings."""
        lane = self._lane("notify")
        assert lane.routable_stages() == []
        assert "notify" not in lane.routable_stages()

    def test_non_list_types_normalise_to_empty(self):
        for junk in [None, 42, {"check": True}]:
            assert self._lane_value(junk) == []

    def _lane_value(self, junk):
        lane = PipelineDefinition(name="x", stages=junk)
        return lane.routable_stages()
