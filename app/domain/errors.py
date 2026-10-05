class ProcessingError(Exception):
    code = "PROCESSING_ERROR"
    retryable = True


class TransientProcessingError(ProcessingError):
    code = "TRANSIENT_PROCESSING_ERROR"
    retryable = True


class PermanentProcessingError(ProcessingError):
    code = "PERMANENT_PROCESSING_ERROR"
    retryable = False


class FileValidationError(PermanentProcessingError):
    code = "FILE_VALIDATION_ERROR"
