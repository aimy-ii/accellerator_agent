"""Ошибки интеграции с API акселератора."""


class AcceleratorAPIError(RuntimeError):
    """Базовая ошибка обращения к API акселератора."""


class AcceleratorAuthError(AcceleratorAPIError):
    """401/403 — токен пользователя невалиден или протух."""


class AcceleratorNotFoundError(AcceleratorAPIError):
    """404 — объект не найден."""


class DocumentDownloadError(AcceleratorAPIError):
    """Не удалось скачать файл проекта."""


class DocumentTooLargeError(DocumentDownloadError):
    """Файл превысил лимит размера."""
