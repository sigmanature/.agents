from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_SCRIPTS = Path("/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

from run_memstress_and_collect_logs import EpochPackagePool, filter_churn_packages


class EpochPackagePoolTests(unittest.TestCase):
    def test_consumes_one_epoch_without_replacement(self) -> None:
        pool = EpochPackagePool(["a", "b", "c", "d"], seed=7, reshuffle_each_epoch=False)

        first = pool.take(2, set())
        second = pool.take(2, set())

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertEqual(set(first + second), {"a", "b", "c", "d"})
        self.assertEqual(len(first + second), 4)

    def test_skips_banned_items_without_repeating_within_take(self) -> None:
        pool = EpochPackagePool(["a", "b", "c", "d"], seed=11, reshuffle_each_epoch=False)

        picked = pool.take(3, {"a", "c"})

        self.assertEqual(len(picked), 2)
        self.assertEqual(set(picked), {"b", "d"})

    def test_reshuffles_between_epochs_while_preserving_membership(self) -> None:
        pool = EpochPackagePool(["a", "b", "c", "d"], seed=19, reshuffle_each_epoch=True)

        epoch_one = pool.take(4, set())
        epoch_two = pool.take(4, set())

        self.assertEqual(set(epoch_one), {"a", "b", "c", "d"})
        self.assertEqual(set(epoch_two), {"a", "b", "c", "d"})
        self.assertEqual(pool.epoch, 2)


class VictimFilteringTests(unittest.TestCase):
    def test_excludes_victim_from_churn_by_default(self) -> None:
        churn = filter_churn_packages(
            ["victim", "a", "b"],
            victim_package="victim",
            exclude_victim=True,
        )

        self.assertEqual(churn, ["a", "b"])

    def test_keeps_victim_when_exclusion_disabled(self) -> None:
        churn = filter_churn_packages(
            ["victim", "a", "b"],
            victim_package="victim",
            exclude_victim=False,
        )

        self.assertEqual(churn, ["victim", "a", "b"])


if __name__ == "__main__":
    unittest.main()
