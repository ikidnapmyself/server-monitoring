"""Unit tests for the shared recommendation constants."""

from django.test import SimpleTestCase

from apps.checkers.checkers.disk import recommendations


def _all_rules():
    """Yield every rule constant defined in the recommendations module."""
    for name in dir(recommendations):
        if name.startswith("_"):
            continue
        value = getattr(recommendations, name)
        if isinstance(value, tuple) and len(value) == 2:
            yield name, value


class RecommendationsModuleTests(SimpleTestCase):
    def test_each_rule_has_keywords_and_lines(self):
        for name, (keywords, lines) in _all_rules():
            with self.subTest(rule=name):
                self.assertIsInstance(keywords, list, f"{name}: keywords must be a list")
                self.assertGreater(len(keywords), 0, f"{name}: keywords must be non-empty")
                self.assertTrue(
                    all(isinstance(k, str) for k in keywords),
                    f"{name}: every keyword must be a string",
                )
                self.assertIsInstance(lines, list, f"{name}: lines must be a list")
                self.assertGreater(len(lines), 0, f"{name}: lines must be non-empty")
                self.assertTrue(
                    all(isinstance(line, str) for line in lines),
                    f"{name}: every line must be a string",
                )

    def test_rule_titles_are_distinct(self):
        """Catches accidental copy-paste during edits."""
        titles = [lines[0] for _name, (_keywords, lines) in _all_rules()]
        duplicates = [t for t in titles if titles.count(t) > 1]
        self.assertEqual(duplicates, [], f"Duplicate titles: {sorted(set(duplicates))}")

    def test_known_keyword_substrings_match(self):
        """Canonical paths trigger the right rule."""
        cases = [
            ("/Users/me/Library/Caches/JetBrains/PyCharm", recommendations.JETBRAINS),
            ("/Users/me/.cache/pip/wheels/abc", recommendations.PIP),
            ("/Users/me/.cache/yarn/v6/deadbeef", recommendations.YARN),
            ("/var/log/journal/abc", recommendations.JOURNAL),
            ("/var/cache/apt/archives", recommendations.APT),
            ("/var/lib/docker/overlay2", recommendations.DOCKER),
            ("/Users/me/Library/Caches/composer/repo", recommendations.COMPOSER),
        ]
        for path, (keywords, _lines) in cases:
            with self.subTest(path=path):
                self.assertTrue(
                    any(kw in path for kw in keywords),
                    f"None of {keywords} matched in {path}",
                )
