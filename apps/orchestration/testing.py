"""Test helpers for routing state owned by this app.

Not imported by production code. It lives beside the models rather than under
``_tests/`` because tests in other apps (checkers preflight, alerts admin) need
it too, and importing another app's private test package would be worse coupling
than one small public helper.
"""


def clear_lanes() -> None:
    """Empty the routing table, seeded lanes included.

    Migration ``0012`` seeds ``cluster-nodes`` and ``catch-all``, so every test
    database starts with two active lanes — the same state a fresh install has.
    A test that asserts "nothing matches", or on an exact count or list of
    definitions, has to say so explicitly. Naming it puts that contract in one
    place instead of asking each future test to rediscover why a bare
    ``PipelineDefinition.objects.all().delete()`` was needed.
    """
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.all().delete()
