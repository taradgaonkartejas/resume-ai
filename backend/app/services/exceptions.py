class DomainError(Exception):
    """Base class for all business-rule failures."""


class NotFoundError(DomainError):
    """A requested entity does not exist, or is not owned by this user."""


class ResumeNotFound(NotFoundError):
    pass


class SessionNotFound(NotFoundError):
    pass


class SuggestionNotFound(NotFoundError):
    pass


class TemplateNotFound(NotFoundError):
    pass


class JobDescriptionNotFound(NotFoundError):
    pass


class ConflictError(DomainError):
    """The request is well-formed but conflicts with current server state.

    Distinct from ValidationError (422): nothing about the request is wrong,
    the world moved underneath it. Maps to 409 so a client can tell "you asked
    for something impossible" from "you asked based on stale information".
    """


class StaleSuggestion(ConflictError):
    """The resume text changed after this suggestion was generated."""


class ValidationError(DomainError):
    """The request is well-formed but violates a business rule."""


class InvalidTargetRef(ValidationError):
    pass


class InvalidStateTransition(ValidationError):
    pass


class QuotaExceeded(ValidationError):
    pass


class ResumeNotReady(InvalidStateTransition):
    """The resume has not finished parsing, or parsing failed.

    Scoring or tailoring an unparsed resume yields a near-zero score that reads
    as "your resume is bad" instead of "we have not read it yet".
    """


class UnsupportedFormat(ValidationError):
    pass


class RewriteRejected(ValidationError):
    """The writer ran and produced nothing usable, so nothing was persisted.

    422 rather than 500: the request was fine, the model's output was not.

    `reason` is the machine-readable half of the answer. The body used to
    carry prose only, which left the client unable to tell a permanent
    decline from a one-off critic rejection -- so it offered "Try again" on
    both, and a user could sit there re-requesting a rewrite the system is
    designed never to produce.

        needs_your_fact     the writer cannot add a number that is not in the
                            resume. Retrying cannot change this.
        critic_rejected     a draft existed but failed grounding review.
                            Another attempt may well succeed.
        nothing_to_rewrite  the writer returned empty.
    """

    def __init__(self, message: str, reason: str = "nothing_to_rewrite"):
        super().__init__(message)
        self.reason = reason


class WriterUnavailable(DomainError):
    """The AI writer could not be reached, so no draft was produced.

    503, deliberately NOT 422. A 422 is a statement about the request -- "what
    you asked for cannot be processed" -- and a throttled or unreachable
    upstream is not the user's fault. It is also the difference between an
    error worth retrying and a dead end.

    This class exists because of a real defect: a rate-limited writer fell back
    to the rule engine, the rule engine had no mechanical fix, and the no-op
    was reported to the user as "This one needs a fact only you have - the AI
    writer cannot add a number that is not already in your resume." That is a
    confident product verdict about someone's resume, issued when the model was
    never consulted. The same shape of bug once made critic_report announce
    that the LLM critic added nothing over the rule engine, during a run in
    which every single call had 403'd.

    A system that cannot reach its model must say so, not invent a substantive
    explanation for the silence.
    """
