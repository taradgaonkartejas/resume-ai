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
