import copy
import logging
import uuid

from app.models import Suggestion
from app.repositories.chat_repository import ChatRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.services import resume_ops
from app.services.exceptions import (
    InvalidStateTransition,
    InvalidTargetRef,
    ResumeNotFound,
    ResumeNotReady,
    RewriteRejected,
    SessionNotFound,
    StaleSuggestion,
    SuggestionNotFound,
    ValidationError,
    WriterUnavailable,
)
from app.services.heuristics import match_keywords, score_resume
from app.services.version_service import VersionService

logger = logging.getLogger(__name__)

#: Sentinel target_ref marking the chat skills PICKER, which can land in
#: several groups and therefore has no single ref. Distinct from the
#: tailoring clarifying card, which is also section="skills" but carries an
#: empty target_ref and asks about exactly one keyword.
SKILLS_PICKER_REF = "skills"

TERMINAL = {"accepted", "rejected"}


def _finding_for(data: dict, finding_id: str, target_ref: str) -> dict:
    """The finding driving this rewrite, so the prompt knows the problem.

    Falls back to a generic instruction rather than failing: a rewrite with a
    vague brief is still useful, and the critic guards the output either way.
    """
    from app.services.heuristics import score_resume

    findings = score_resume(data).get("findings") or []
    if finding_id:
        for f in findings:
            if f.get("id") == finding_id:
                return f
    for f in findings:
        if f.get("target_ref") == target_ref:
            return f
    return {"id": finding_id, "message": "Improve this section", "fix_hint": ""}


class SuggestionService:
    """Owns the Accept / Reject / Edit lifecycle.

    Accept is the only write in the system that spans three tables. It runs as
    one transaction: patch the resume JSON, append a version, mark the
    suggestion, recompute the session match score. A suggestion marked accepted
    whose text never landed is the worst possible state, so nothing is
    committed until every step has succeeded.
    """

    def __init__(
        self,
        suggestions: SuggestionRepository,
        resumes: ResumeRepository,
        sessions: TailoringRepository,
        versions: VersionService,
        chats: ChatRepository | None = None,
    ) -> None:
        self.suggestions = suggestions
        self.resumes = resumes
        self.sessions = sessions
        self.versions = versions
        # Optional so every existing construction site keeps working; when it
        # is absent the transcript simply gains no entry. Mirroring must never
        # be able to fail an edit the user asked for.
        self.chats = chats

    @property
    def db(self):
        return self.suggestions.db

    def list_for_session(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, list[Suggestion]]:
        session = self.sessions.get_owned(session_id, user_id)
        if session is None:
            raise SessionNotFound(str(session_id))
        rows = self.suggestions.list_for_session(session_id)
        self._attach_score_delta(rows, session.resume_id, user_id)
        # Questions lead the active list.
        #
        # They are not a fourth bucket: a question IS an open item, and a
        # tab the user has to remember to visit is a tab that collects
        # unanswered questions. They sort first because they are the only
        # cards that can raise the match percent for a skill the resume has
        # no evidence of -- the drafts below them are already achievable.
        #
        # Without this they belonged to NO bucket and were invisible: the
        # three filters here were exhaustive over the old three statuses.
        return {
            "active": (
                [s for s in rows if s.status == "needs_input"]
                + [s for s in rows if s.status == "pending"]
            ),
            "matched": [s for s in rows if s.status == "accepted"],
            "rejected": [s for s in rows if s.status == "rejected"],
        }

    def _attach_score_delta(
        self, rows: list[Suggestion], resume_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Price each suggestion in RESUME points, not just keywords.

        Keyword match answered "does this look like the job ad"; it could not
        answer "does accepting this make my resume better or worse". Those are
        different questions and the user was only ever shown the first.

        Deterministic, local, zero tokens -- the same scorer the Analysis tab
        prints, so a `+2` here and a `+2` there mean the same thing.

        Set as a plain attribute, not a column: it is a function of the CURRENT
        resume, so persisting it would be caching a value that goes stale the
        moment anything else is accepted.
        """
        # get_owned, not a bare get: pricing must not become a way to read
        # another user's resume through a session id.
        resume = self.resumes.get_owned(resume_id, user_id)
        data = (resume.structured_data or {}) if resume is not None else {}
        if not data:
            for row in rows:
                row.score_delta = 0
                row.is_stale = False
            return

        base = score_resume(data)["overall_score"]
        for row in rows:
            row.is_stale = _is_stale(data, row)
            text = (row.edited_text or row.suggested_text or "").strip()
            # An accepted suggestion is ALREADY in the resume. Re-applying it
            # scores a no-op delta of 0, which would print "no score change"
            # on the very cards that earned the points -- so price those
            # against the text they replaced instead.
            original = row.original_text if row.status == "accepted" else None
            row.score_delta = _delta_for(data, row.target_ref, text, base, original)



    def _require(self, suggestion_id: uuid.UUID, user_id: uuid.UUID) -> Suggestion:
        suggestion = self.suggestions.get_owned(suggestion_id, user_id)
        if suggestion is None:
            raise SuggestionNotFound(str(suggestion_id))
        return suggestion

    # --------------------------------------------- clarifying questions (Inc 6)
    def get(self, suggestion_id: uuid.UUID, user_id: uuid.UUID) -> Suggestion:
        """One owned suggestion, or 404. No state change."""
        return self._require(suggestion_id, user_id)

    def answer_question(
        self,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
        answer: str = "",
        confirmed: bool = False,
    ) -> Suggestion:
        """Resolve a `needs_input` card with a fact only the user has.

        This is the one place in the system where something enters a resume
        that the resume did not already support. Everything else -- the
        critic, `_hard_check`, `introduced_findings`, the digits guard --
        compares a draft against existing text. Here the user is the source,
        so the job is not to verify the claim (we cannot) but to make sure it
        is RECORDED AS THEIRS and that nothing else rides in alongside it.

        Three outcomes, and declining is a first-class one:

          attested   the answer names the skill -- or, for a yes/no question,
                     the user pressed the button. Becomes a normal pending
                     suggestion that the ordinary accept path applies.
          declined   blank or unrelated answer. The card is REJECTED, not
                     softened into "familiar with X". The answer is still
                     stored: "they were asked and said no" is a fact worth
                     keeping, and it stops the same question being useful to
                     ask again.
          refused    `requires_text` and nothing was typed. 422 -- the user
                     has not actually made the claim the button would make
                     for them.
        """
        from app.ai.agents import unverifiable_numbers
        from app.services.clarifying import answer_supports
        from app.services.text_match import canonical_aliases

        suggestion = self._require(suggestion_id, user_id)
        if suggestion.status != "needs_input":
            raise InvalidStateTransition(
                f"This card is {suggestion.status}, not a question."
            )

        # A skills PICKER is a multiple choice over a server-supplied list,
        # not a free-text claim about one keyword, so it resolves on its own
        # path. Discriminated by target_ref, not by section: the tailoring
        # clarifying-question flow also uses section="skills" (with an empty
        # target_ref), and matching on section hijacked all of it.
        if suggestion.target_ref == SKILLS_PICKER_REF:
            return self._answer_skills(suggestion, user_id, answer or "")

        keyword = (suggestion.keywords or [""])[0] or ""
        answer = (answer or "").strip()

        # A duration claim cannot be made by a button. Saying "yes" to
        # "3+ years of Redis" would write a specific number into the resume
        # as the user's own word when the user never typed a number at all.
        if suggestion.requires_text and not answer:
            raise ValidationError(
                "This one asks how long and where, so a yes is not enough - "
                "type what you did, or reject the card."
            )

        # Verbatim, before any branch. The audit record is the answer as
        # given, including the one that declines.
        suggestion.answer = answer

        attested = answer_supports(answer, keyword) or (
            confirmed and not suggestion.requires_text
        )
        if not attested:
            suggestion.status = "rejected"
            self.db.commit()
            return suggestion

        resume = self.resumes.get_owned(suggestion.resume_id, user_id)
        if resume is None:
            raise SuggestionNotFound(str(suggestion_id))
        data = resume.structured_data or {}

        # An attested skill lands in the SKILLS LIST, never in an experience
        # bullet. A skills entry claims a capability and nothing else -- no
        # employer, no dates, no outcome -- which is exactly the scope of
        # what the user just told us. Writing "Led GitOps adoption at Acme"
        # would invent an employer and a seniority out of the word "yes".
        target_ref = self._skills_ref(data)
        if target_ref is None:
            raise ValidationError(
                "This resume has no skills section to add it to. Add one "
                "first, then answer this question."
            )

        label = canonical_aliases(keyword)[0]
        original_text = resume_ops.resolve(data, target_ref)
        items = [s.strip() for s in original_text.split(",") if s.strip()]
        if any(i.lower() == label.lower() for i in items):
            # It arrived some other way between asking and answering.
            suggestion.status = "rejected"
            suggestion.critic_notes = f"{label} is already listed"
            self.db.commit()
            return suggestion
        suggested_text = ", ".join([*items, label])

        # The guard, with the user's sentence added to the corpus of truth --
        # and nothing else added. A number the user supplied is now
        # permitted; a number the model or this code invented around it still
        # is not. Today the draft is a comma join so this cannot fire, which
        # is the point: it is the invariant that must survive the day this
        # path starts producing prose.
        invented = unverifiable_numbers(
            resume_ops.to_plain_text(data) + "\n" + answer, suggested_text
        )
        if invented:
            raise RewriteRejected(
                f"Could not add this without inventing {', '.join(sorted(invented))}.",
                reason="critic_rejected",
            )

        suggestion.target_ref = target_ref
        suggestion.section = "skills"
        suggestion.original_text = original_text
        suggestion.suggested_text = suggested_text
        suggestion.reasoning = (
            f"You confirmed {label} when the job description asked for it. "
            f"Added to your skills list."
        )
        # NOT grounded -- the resume never supported this. `attested` is the
        # separate provenance, and keeping the two apart is what stops a
        # user's word being laundered into a verified fact.
        suggestion.grounded = False
        suggestion.attested = True
        suggestion.status = "pending"
        self.db.commit()
        return suggestion

    @staticmethod
    def _skills_ref(data: dict) -> str | None:
        """The skills group to append to, or None if there is nowhere to put it."""
        groups = data.get("skills") or []
        if not groups:
            return None
        return "skills.0.items"

    # ------------------------------------------------------- targeted rewrite
    def rewrite_section(
        self,
        resume_id: uuid.UUID,
        user_id: uuid.UUID,
        target_ref: str,
        finding_id: str = "",
        regenerate: bool = False,
    ) -> Suggestion:
        """Draft an AI fix for one finding and persist it as a Suggestion.

        Lives here, not in AnalysisService, because the thing it produces is a
        Suggestion and this class owns that lifecycle. It returns the ordinary
        Suggestion shape so the frontend reuses PATCH /suggestions/{id}: there
        is exactly one accept/reject/edit path, not two.
        """
        from app.ai import agents

        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        status = getattr(resume, "parse_status", "ready")
        if status != "ready":
            raise ResumeNotReady(
                "This resume has not been read successfully, so there is "
                "nothing to rewrite yet."
            )

        data = resume.structured_data or {}
        if not resume_ops.exists(data, target_ref):
            raise InvalidTargetRef(f"{target_ref}: nothing to rewrite there")

        # One pending analysis draft per ref. Re-clicking returns the existing
        # one rather than stacking duplicates on the same bullet; Regenerate
        # supersedes it explicitly.
        existing = self._pending_analysis(resume.id, target_ref)
        if existing is not None:
            if not regenerate:
                return existing
            existing.status = "rejected"
            self.db.flush()

        finding = _finding_for(data, finding_id, target_ref)
        outcome = agents.rewrite_for_finding(
            data, target_ref, finding, user_id=user_id, resume_id=resume.id
        )
        draft = dict(outcome.value)
        draft["target_ref"] = target_ref

        if not (draft.get("suggested_text") or "").strip():
            raise RewriteRejected(
                "Could not draft a rewrite for this section.",
                reason="nothing_to_rewrite",
            )
        if draft["suggested_text"].strip() == draft["original_text"].strip():
            # A no-op has two completely different causes and they must not
            # share a message.
            #
            #   degraded  the writer LLM was never reached (rate limit, timeout,
            #             outage) and the deterministic fallback had no
            #             mechanical fix. Nothing has been learned about the
            #             user's resume. Transient, upstream, worth retrying.
            #
            #   ran       the writer answered and genuinely could not improve
            #             the text -- most often because the fix requires a
            #             number only the user knows.
            #
            # Conflating them produced the original defect: a throttled request
            # was reported as a confident verdict about someone's resume.
            if outcome.status == "degraded":
                raise WriterUnavailable(
                    "The AI writer could not be reached just now, so this "
                    "rewrite was not attempted. Nothing is wrong with your "
                    "resume - try again in a minute."
                )
            #   not configured
            #             no writer exists at all. The rule engine ran on
            #             purpose rather than as a fallback, so a no-op IS a
            #             real statement -- but only about what MECHANICAL
            #             rewriting can do, never about numbers.
            #
            # That third case stays a 422: with no key, retrying changes
            # nothing, and the client's isFinalRefusal correctly suppresses a
            # pointless retry. Only the stated CAUSE was wrong. "The writer
            # cannot add a number that is not in your resume" is a verdict
            # about the user's facts, and no model formed it -- the rule
            # engine simply has no mechanical fix for, say, a weak verb.
            #
            # used_llm is the honest discriminator: True only when a model
            # actually answered.
            if not outcome.used_llm:
                raise RewriteRejected(
                    "Automatic rewriting could not improve this line. No AI "
                    "writer is configured, so only mechanical fixes were "
                    "available - add a provider API key to backend/.env, or "
                    "edit the text directly.",
                    reason="writer_not_configured",
                )
            raise RewriteRejected(
                "This one needs a fact only you have - the AI writer cannot "
                "add a number that is not already in your resume. Edit the "
                "text directly instead.",
                reason="needs_your_fact",
            )

        # The same grounding guard the tailoring graph uses. An ungrounded
        # draft is never persisted, so the user never sees it.
        verdict = agents.review_draft(draft, data).value
        if not verdict.approved:
            raise RewriteRejected(
                f"The reviewer rejected this rewrite: {verdict.notes}",
                reason="critic_rejected",
            )

        suggestion = self.suggestions.create(
            resume_id=resume.id,
            session_id=None,
            origin="analysis",
            section=target_ref.split(".")[0],
            target_ref=target_ref,
            # The real placement, not the finding message. This column is
            # what the card shows under the heading, and -- since the
            # identity half of the staleness check reads it -- a message
            # here is an anchor that can never match, which silently
            # switches that protection off for every analysis rewrite.
            placement=resume_ops.placement_for(resume.structured_data or {}, target_ref),
            original_text=draft["original_text"],
            suggested_text=draft["suggested_text"].strip(),
            keywords=[k for k in (draft.get("keywords") or []) if k][:8],
            reasoning=draft.get("reasoning") or "",
            status="pending",
            grounded=True,
            # Degraded writer: say so on the row rather than in a log line, so
            # the card can label it "written without AI".
            critic_notes="" if outcome.used_llm else "written without AI",
        )
        self._log_fix_requested(resume, target_ref, finding, suggestion)
        self.db.commit()
        return suggestion

    def _log_fix_requested(self, resume, target_ref, finding, suggestion) -> None:
        """Mirror a Fix/Rewrite with AI click into the chat transcript.

        Two rows, matching what the user asked for: their click recorded as a
        TURN, then the draft as an event.

        The turn is written with role "user" deliberately. It is a thing the
        user did, and it is also useful context -- the history builder replays
        `content` into the next prompt, so the assistant knows a fix for this
        section was already requested. `meta.kind` lets the pane mark it as a
        button press rather than typing, so the transcript does not claim they
        typed something they clicked.

        The draft's explanation is the writer's OWN `reasoning`, not freshly
        generated prose: a second model call per click would spend the 45s
        budget again for text we already have.
        """
        if self.chats is None:
            return
        try:
            label = (finding or {}).get("message") or f"Improve {target_ref}"
            self.chats.append(
                resume.id,
                "user",
                f"Fix with AI: {label}",
                meta={"kind": "fix_requested", "target_ref": target_ref},
            )
            self.chats.append_event(
                resume.id,
                "suggestion_drafted",
                suggestion.reasoning or "Drafted a revision for your review.",
                suggestion_id=suggestion.id,
                target_ref=target_ref,
                origin="analysis",
                placement=suggestion.placement or "",
                draft_text=suggestion.suggested_text or "",
                # Nothing is applied yet. The pane renders the ordinary
                # Accept/Reject card off suggestion_id, so the Epic 5 review
                # path stays the only way text reaches the resume.
                applied=False,
            )
        except Exception as exc:  # noqa: BLE001 - logging must not break the fix
            logger.warning("chat mirror failed for %s: %s", target_ref, exc)

    def _pending_analysis(self, resume_id: uuid.UUID, target_ref: str):
        return (
            self.db.query(Suggestion)
            .filter(
                Suggestion.resume_id == resume_id,
                Suggestion.target_ref == target_ref,
                Suggestion.origin == "analysis",
                Suggestion.status == "pending",
            )
            .first()
        )


    # ------------------------------------------------------------------
    @staticmethod
    def _norm(text: str) -> str:
        return "".join(ch for ch in str(text).lower() if ch.isalnum())

    @classmethod
    def _group_for(cls, keyword: str, groups: list[dict]) -> int | None:
        """Index of the skill group this keyword belongs with, or None.

        Matched against what the user has ALREADY written rather than a
        hard-coded taxonomy: "java8/17/21" belongs with "Java" because the
        resume says so, not because a lookup table claims Java is a language.
        A taxonomy would be one more thing to be wrong and to maintain.
        """
        target = cls._norm(keyword)
        if not target:
            return None
        best, best_score = None, 0
        for i, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            for item in group.get("items") or []:
                a = cls._norm(item)
                if not a:
                    continue
                if a == target:
                    return i
                if (a in target or target in a) and len(a) >= 3 and len(a) > best_score:
                    best, best_score = i, len(a)
            label = cls._norm(group.get("label") or "")
            if label and (label in target or target in label) and best_score == 0:
                best, best_score = i, 1
        return best

    def _answer_skills(self, suggestion, user_id: uuid.UUID, answer: str):
        """Apply exactly the skills the user selected -- and nothing else.

        The selection arrives as text so the existing answer endpoint and its
        decline semantics work unchanged. Every entry is checked back against
        the keywords the SERVER put on the card: a client cannot widen the
        list into "add whatever I post".

        Applied here rather than left pending. The user already made an
        explicit per-skill choice; asking them to accept a card afterwards
        would be a second review of a decision they just made.
        """
        by_norm = {self._norm(k): str(k) for k in (suggestion.keywords or [])}

        selected: list[str] = []
        for raw in str(answer).replace("\n", ",").split(","):
            key = self._norm(raw)
            if key and key in by_norm and by_norm[key] not in selected:
                selected.append(by_norm[key])

        suggestion.answer = str(answer).strip()

        # Declining is a first-class outcome, not an error.
        if not selected:
            suggestion.status = "rejected"
            self.db.commit()
            return suggestion

        resume = self.resumes.get_owned(suggestion.resume_id, user_id)
        if resume is None:
            raise SuggestionNotFound(str(suggestion.id))

        data = resume.structured_data or {}
        before = copy.deepcopy(data)
        groups = [g for g in (data.get("skills") or []) if isinstance(g, dict)]

        patched = data
        spillover: list[str] = []
        for skill in selected:
            idx = self._group_for(skill, groups)
            if idx is None:
                spillover.append(skill)
                continue
            ref = f"skills.{idx}.items"
            current = (
                resume_ops.resolve(patched, ref)
                if resume_ops.exists(patched, ref)
                else ""
            )
            items = [i.strip() for i in str(current).split(",") if i.strip()]
            if any(self._norm(i) == self._norm(skill) for i in items):
                continue
            items.append(skill)
            patched = resume_ops.apply_patch(patched, ref, ", ".join(items))

        if spillover:
            skills = list(patched.get("skills") or [])
            skills.append({"label": "Additional Skills", "items": spillover})
            patched = {**patched, "skills": skills}

        resume.structured_data = patched
        self.versions.record(
            resume,
            patched,
            # Never "chat": these are the user's own claims, and the version
            # log has to stay able to answer "did a model write this?".
            change_source="attested",
            label=f"Added skills: {', '.join(selected)}",
        )
        suggestion.suggested_text = ", ".join(selected)
        suggestion.attested = True
        suggestion.status = "accepted"

        self._log_accepted(resume, suggestion, before, patched, ", ".join(selected))
        self.db.commit()
        return suggestion

    def act(
        self,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        edited_text: str | None = None,
    ) -> Suggestion:
        action = action.lower()
        if action not in {"accept", "reject", "edit"}:
            raise ValidationError(f"Unknown action: {action}")

        suggestion = self._require(suggestion_id, user_id)
        if suggestion.status in TERMINAL and action != "edit":
            raise InvalidStateTransition(
                f"Suggestion already {suggestion.status}"
            )
        # A question has no draft to apply or edit -- suggested_text is "".
        # Rejecting it is allowed and is how the user says "no, I have not
        # done that"; answering it goes through answer_question.
        if suggestion.status == "needs_input" and action != "reject":
            raise InvalidStateTransition(
                "Answer this question before accepting it."
            )

        if action == "reject":
            suggestion.status = "rejected"
            self.db.commit()
            return suggestion

        if action == "edit":
            if not edited_text or not edited_text.strip():
                raise ValidationError("edited_text is required for edit")
            suggestion.edited_text = edited_text.strip()
            suggestion.status = "pending"
            self.db.commit()
            return suggestion

        # --- accept: one transaction across three tables ---
        resume = self.resumes.get_owned(suggestion.resume_id, user_id)
        if resume is None:
            raise SuggestionNotFound(str(suggestion_id))

        text = suggestion.edited_text or suggestion.suggested_text
        if not text.strip():
            raise ValidationError("Suggestion has no text to apply")

        data = resume.structured_data or {}

        # The suggestion was written against original_text. If the field no
        # longer holds that, someone edited it in the meantime -- by hand, via
        # chat, or by accepting an overlapping suggestion -- and applying this
        # patch would silently destroy that newer work. apply_patch only checks
        # that the *path* still resolves, not that the *content* is unchanged.
        if resume_ops.exists(data, suggestion.target_ref):
            current = resume_ops.resolve(data, suggestion.target_ref)
            if current != suggestion.original_text:
                raise StaleSuggestion(
                    "This text changed after the suggestion was generated, so "
                    "accepting it would overwrite the newer version. Reject it "
                    "and re-run tailoring to get a suggestion for the current "
                    "text."
                )

        # Raises InvalidTargetRef if the path vanished since generation.
        patched = resume_ops.apply_patch(data, suggestion.target_ref, text)

        resume.structured_data = patched
        self.versions.record(
            resume,
            patched,
            # "attested" keeps a user-supplied fact distinguishable from an
            # AI rewrite forever, in the one log that outlives the suggestion
            # row's usefulness.
            change_source="attested" if suggestion.attested else (suggestion.origin or "tailor"),
            label=f"Accepted: {suggestion.target_ref}",
        )
        suggestion.status = "accepted"

        if suggestion.session_id:
            session = self.sessions.get_owned(suggestion.session_id, user_id)
            if session is not None and session.all_keywords:
                scores = match_keywords(patched, list(session.all_keywords))
                session.match_percent = scores["match_percent"]
                session.matched_keywords = scores["matched_keywords"]
                session.gap_keywords = scores["gap_keywords"]

        self._log_accepted(resume, suggestion, data, patched, text)

        self.db.commit()
        return suggestion

    def _log_accepted(
        self, resume, suggestion, before: dict, after: dict, applied_text: str = ""
    ) -> None:
        """Mirror an accepted edit into the chat transcript.

        Inside the same transaction as the write, so the log cannot record an
        edit that was rolled back. Wrapped anyway: a transcript entry is never
        worth failing the edit the user actually asked for.
        """
        if self.chats is None:
            return
        try:
            before_score = score_resume(before)["overall_score"]
            after_score = score_resume(after)["overall_score"]
            self.chats.append_event(
                resume.id,
                "suggestion_accepted",
                f"Applied to {suggestion.target_ref}.",
                suggestion_id=suggestion.id,
                target_ref=suggestion.target_ref,
                origin=suggestion.origin or "tailor",
                score_before=before_score,
                score_after=after_score,
                # The pane shows a score card only when the number MOVED.
                # Emitting "+0 points" after every accept trains the user to
                # ignore the card.
                score_delta=after_score - before_score,
                # Snapshotted, not looked up through suggestion_id at render
                # time. The transcript is a log: it must keep showing what was
                # applied AT THIS MOMENT even after the row is edited again,
                # reverted, or the text replaced by a later suggestion.
                applied_text=applied_text,
                placement=suggestion.placement or "",
            )
        except Exception as exc:  # noqa: BLE001 - logging must not break editing
            logger.warning("chat mirror failed for %s: %s", suggestion.id, exc)

    # ------------------------------------------------------------------
    def accept_all(
        self,
        resume_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> dict:
        """Apply every applicable pending suggestion, as ONE version.

        One version, not one per suggestion, because "Accept All" is a
        single decision by the user and a single undo should put it back.
        Recording twelve versions would make the user press undo twelve
        times to escape one click.

        Nothing is forced. A suggestion that is stale, awaiting an answer,
        or empty is skipped and named in the result -- the point of the
        button is to save clicks on the uncontroversial ones, not to
        overrule the checks that exist to stop a suggestion landing on text
        it was not written against.
        """
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))

        rows = self.suggestions.list_pending_for_resume(resume_id, session_id)
        # Deterministic order. Dict iteration order over a query result is
        # stable in practice but not promised, and "which of two
        # suggestions on the same bullet won" must not depend on that.
        rows.sort(key=lambda r: (r.target_ref, r.created_at or ""))

        data = resume.structured_data or {}
        applied: list[str] = []
        skipped: list[dict] = []

        for row in rows[:BULK_APPLY_LIMIT]:
            text = (row.edited_text or row.suggested_text or "").strip()
            if not text:
                skipped.append({"id": str(row.id), "reason": "empty"})
                continue
            if row.status == "needs_input":
                skipped.append({"id": str(row.id), "reason": "needs_input"})
                continue

            # Re-checked against the ACCUMULATING data, not the snapshot we
            # started from. Two suggestions on one bullet is the ordinary
            # case -- the analysis pane and the tailor pane both draft
            # against the same line -- and after the first lands the second
            # is genuinely stale. Judging them all against the original
            # would apply the second on top of the first and destroy it.
            if not resume_ops.exists(data, row.target_ref):
                skipped.append({"id": str(row.id), "reason": "missing_target"})
                continue
            # _is_stale covers BOTH arms -- the text no longer matching, and
            # the entry having moved under a positional ref. An earlier draft
            # of this loop also compared the text inline, which was dead code
            # hiding behind this call: a negative control that broke the
            # inline check still passed, because this one caught it anyway.
            if _is_stale(data, row):
                skipped.append({"id": str(row.id), "reason": "stale"})
                continue

            data = resume_ops.apply_patch(data, row.target_ref, text)
            row.status = "accepted"
            applied.append(row.target_ref)

        if len(rows) > BULK_APPLY_LIMIT:
            for row in rows[BULK_APPLY_LIMIT:]:
                skipped.append({"id": str(row.id), "reason": "over_limit"})

        baseline = resume.version_cursor
        if applied:
            resume.structured_data = data
            self.versions.record(
                resume,
                data,
                change_source="accept_all",
                label=f"Accepted {len(applied)} suggestions",
            )
            if session_id:
                session = self.sessions.get_owned(session_id, user_id)
                if session is not None and session.all_keywords:
                    scores = match_keywords(data, list(session.all_keywords))
                    session.match_percent = scores["match_percent"]
                    session.matched_keywords = scores["matched_keywords"]
                    session.gap_keywords = scores["gap_keywords"]

        self.db.commit()
        return {
            "applied": applied,
            "applied_count": len(applied),
            "skipped": skipped,
            "skipped_count": len(skipped),
            # The cursor as it stood BEFORE this batch -- what Decline All
            # restores to. Returned rather than inferred so the client never
            # has to guess which version "before" was.
            "undo_to_version": baseline,
            "version_cursor": resume.version_cursor,
        }


BULK_APPLY_LIMIT = 50


def _delta_for(
    data: dict,
    target_ref: str,
    text: str,
    base: int,
    revert_to: str | None = None,
) -> int:
    """Score change from applying `text` at `target_ref`.

    `revert_to` prices an ALREADY-APPLIED suggestion: score the resume with
    the original text put back, and report what the acceptance was worth.
    """
    if not text or not resume_ops.exists(data, target_ref):
        return 0
    try:
        if revert_to is not None:
            before = score_resume(
                resume_ops.apply_patch(data, target_ref, revert_to)
            )["overall_score"]
            return base - before
        after = score_resume(
            resume_ops.apply_patch(data, target_ref, text)
        )["overall_score"]
        return after - base
    except Exception:  # noqa: BLE001
        # A ref that no longer resolves is a staleness problem, not a scoring
        # one, and it has its own handling on accept. Never let pricing break
        # the list.
        return 0


def _is_stale(data: dict, row: Suggestion) -> bool:
    """Has the text this suggestion was written against moved on?

    `act()` already refuses to apply a suggestion whose target no longer holds
    `original_text` -- applying it would silently destroy whatever replaced it.
    But that check fires at the moment the user clicks Accept, AFTER they have
    read the card, compared the two bullets and decided. Someone who works the
    Analysis tab first, fixing bullets with "Fix with AI", then switches to
    Tailor, meets a wall of 409s and reasonably concludes the feature is
    broken.

    The suggestion really IS obsolete -- that is not the bug. The bug is that
    we discover it last instead of first. Computing it here is a dict lookup
    and a string compare, so the card can say so before it is clicked.

    Only PENDING suggestions can be stale. An accepted one is supposed to
    differ from `original_text` -- that is what accepting did -- and a
    rejected one is never going to be applied.
    """
    if row.status != "pending":
        return False
    if not resume_ops.exists(data, row.target_ref):
        # The section was deleted out from under it. Unapplicable for a
        # different reason, but identical from the user's point of view.
        return True

    if resume_ops.resolve(data, row.target_ref) != row.original_text:
        return True

    # --- identity, not just text -----------------------------------------
    # The text check above misses one case, documented as a known gap since
    # the rerank work: two roles carrying a byte-identical bullet. Reorder
    # them and `original_text` still matches, while the ref now addresses a
    # different job -- so accepting applies the suggestion to the wrong
    # employer, silently and with no warning anywhere.
    #
    # The placement recorded when the suggestion was written is the anchor.
    # If it names a DIFFERENT entry than the ref now points at, the entry
    # moved.
    stored = (row.placement or "").strip()
    if not stored:
        return False
    current = resume_ops.placement_for(data, row.target_ref)
    if not current or stored == current:
        return False

    # Only conclude "moved" when the stored string is recognisably a
    # placement for this section. Analysis-origin rows used to store the
    # finding message in this column, and an anchor that matches nothing
    # would mark every one of them stale -- turning a missing protection
    # into a worse bug than the one being fixed.
    return stored in resume_ops.placements_in_section(data, row.target_ref)
