class ToolError(RuntimeError):
    def __init__(self, code, message, *, retryable=False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(code)


class RetryableToolError(ToolError):
    def __init__(self, message="工具暂时不可用"):
        super().__init__("unavailable", message, retryable=True)

