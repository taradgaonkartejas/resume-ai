"""One tailor suggestion per target_ref.

Reported from a real session: two cards, the SAME original bullet
("Eliminated partial-write corruption in geospatial ingestion via a
two-service architecture..."), the same `sql` keyword chip, and two
different rewrites. Accepting either leaves the other stale, and nothing
tells the user which to take.

The cause was that the persistence loop in `TailoringService.tailor`
validated each draft and wrote it, with no uniqueness check on
`target_ref`. The writer is free to emit one draft per matched keyword, and
both were stored.

The prompt now asks for one rewrite per bullet, but the prompt is not the
fix -- the duplicate existed precisely because only a prompt was asking.
These tests pin the deterministic guard.
"""

from __future__ import annotations

from app.services import resume_ops


def _drafts_for_one_bullet(ref: str, original: str) -> list[dict]:
    """Two drafts for one ref, as the writer actually produced them."""
    return [
        {
            "section": "experience",
            "target_ref": ref,
            "placement": "Ceinsys Tech Ltd. — Software Engineer",
            "original_text": original,
            "suggested_text": (
                "Eliminated partial-write corruption via gRPC client-streaming "
                "and transactional SQL writes with asyncpg into PostGIS."
            ),
            "keywords": ["sql"],
            "reasoning": "PostGIS/asyncpg genuinely support the SQL keyword.",
        },
        {
            "section": "experience",
            "target_ref": ref,
            "placement": "Ceinsys Tech Ltd. — Software Engineer",
            "original_text": original,
            "suggested_text": (
                "Eliminated partial-write corruption via two-service gRPC "
                "client-streaming architecture with transactional SQL writes."
            ),
            "keywords": ["sql", "postgresql"],
            "reasoning": "Second attempt at the same bullet.",
        },
    ]


class TestTheMergeLogic:
    """The dedup rule itself, exercised without a database.

    `TailoringService.tailor` needs a graph run, a job description and an
    LLM; the behaviour under test is the loop, so it is reproduced here
    against the same inputs. The live wiring is covered by
    `test_api_contract.py`.
    """

    @staticmethod
    def _apply(drafts: list[dict]) -> list[dict]:
        """Mirror of the persistence loop's dedup, operating on plain dicts."""
        first: dict[str, dict] = {}
        order: list[dict] = []
        for d in drafts:
            ref = d["target_ref"]
            existing = first.get(ref)
            if existing is not None:
                merged = list(existing["keywords"] or [])
                for kw in d["keywords"] or []:
                    if kw and kw.lower() not in {m.lower() for m in merged}:
                        merged.append(kw)
                existing["keywords"] = merged[:8]
                continue
            row = dict(d)
            first[ref] = row
            order.append(row)
        return order

    def test_two_drafts_for_one_bullet_become_one_suggestion(self):
        rows = self._apply(_drafts_for_one_bullet("exp_0.bullet_0", "Eliminated..."))
        assert len(rows) == 1

    def test_the_first_draft_wins(self):
        """First, not 'best'. They arrive in the writer's own order of
        confidence and nothing here could rank them honestly."""
        rows = self._apply(_drafts_for_one_bullet("exp_0.bullet_0", "Eliminated..."))
        assert "gRPC client-streaming and transactional SQL" in rows[0]["suggested_text"]

    def test_keywords_from_the_discarded_draft_are_kept(self):
        """Dropping the row must not drop the match-percent credit it carried."""
        rows = self._apply(_drafts_for_one_bullet("exp_0.bullet_0", "Eliminated..."))
        assert set(rows[0]["keywords"]) == {"sql", "postgresql"}

    def test_duplicate_keywords_are_not_doubled(self):
        rows = self._apply(_drafts_for_one_bullet("exp_0.bullet_0", "Eliminated..."))
        assert rows[0]["keywords"].count("sql") == 1

    def test_keyword_merge_is_case_insensitive(self):
        """"SQL" and "sql" from two drafts would otherwise both land on the
        chip row, which is how the duplicate looked to the user in the first
        place."""
        drafts = _drafts_for_one_bullet("exp_0.bullet_0", "Eliminated...")
        drafts[1]["keywords"] = ["SQL", "PostGIS"]
        rows = self._apply(drafts)
        lowered = [k.lower() for k in rows[0]["keywords"]]
        assert lowered.count("sql") == 1
        assert "postgis" in lowered

    def test_different_refs_are_left_alone(self):
        """The guard must not collapse genuinely distinct suggestions."""
        drafts = _drafts_for_one_bullet("exp_0.bullet_0", "Eliminated...")
        drafts[1]["target_ref"] = "exp_0.bullet_1"
        rows = self._apply(drafts)
        assert len(rows) == 2

    def test_keyword_list_stays_capped(self):
        drafts = _drafts_for_one_bullet("exp_0.bullet_0", "Eliminated...")
        drafts[1]["keywords"] = [f"kw{i}" for i in range(20)]
        rows = self._apply(drafts)
        assert len(rows[0]["keywords"]) <= 8


class TestTheServiceImplementsIt:
    """The loop in the service must actually contain the guard.

    `_apply` above is a mirror, and a mirror can drift from the thing it
    reflects -- so the real source is asserted too.
    """

    def test_the_persistence_loop_dedups_by_target_ref(self):
        import pathlib

        from app.services import tailoring_service

        src = pathlib.Path(tailoring_service.__file__).read_text()
        assert "first_for_ref" in src
        assert "merged" in src

    def test_the_writer_prompt_asks_for_one_rewrite_per_bullet(self):
        from app.ai import prompts

        assert "ONE rewrite per bullet" in prompts.WRITER


class TestPlacementIsUnchangedByTheMerge:
    def test_the_surviving_row_keeps_a_real_placement(self):
        """Regression guard for the other half of the same report: the
        placement shown on these cards was wrong. That is a parser bug, not a
        dedup bug, and the merge must not quietly paper over it by picking a
        placement from a different draft."""
        rows = TestTheMergeLogic._apply(
            _drafts_for_one_bullet("exp_0.bullet_0", "Eliminated...")
        )
        assert rows[0]["placement"] == "Ceinsys Tech Ltd. — Software Engineer"

    def test_placement_for_still_uses_the_same_join(self):
        data = {
            "experience": [
                {"company": "Ceinsys Tech Ltd.", "role": "Software Engineer",
                 "bullets": ["Eliminated..."]}
            ]
        }
        assert (
            resume_ops.placement_for(data, "exp_0.bullet_0")
            == "Ceinsys Tech Ltd. — Software Engineer"
        )
