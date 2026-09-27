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
    """The critic refused the draft, so it was never persisted.

    422 rather than 500: the request was fine, the model's output was not, and
    the user can meaningfully retry or edit by hand.
    """
