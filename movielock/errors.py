"""Exception types shared across Movielock."""


class MovielockError(Exception):
    """Base class for errors that should be shown to the user without a traceback."""


class ParseError(MovielockError):
    def __init__(self, message: str, line: int | None = None, source: str | None = None):
        self.line = line
        self.source = source
        self.message = message
        where = f"line {line}: " if line else ""
        super().__init__(f"{where}{message}")


class ValidationFailed(MovielockError):
    def __init__(self, issues):
        self.issues = issues
        errors = [i for i in issues if i.severity == "error"]
        lines = "\n".join(f"  {i}" for i in errors)
        super().__init__(f"script has {len(errors)} error(s):\n{lines}")


class ToolMissing(MovielockError):
    """An external program (ffmpeg, blender, rhubarb ...) could not be found."""
