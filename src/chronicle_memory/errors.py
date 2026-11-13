"""Errors shared by CLI and MCP adapters."""


class ConflictError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


class ForgottenError(ValueError):
    pass


class NotInitializedError(RuntimeError):
    pass


EXIT_CODES = {'invalid_arguments':2,'unknown_tool':2,'version_conflict':3,
              'source_conflict':3,'provider_error':4,'storage_error':4,'not_initialized':4,'memory_forgotten':3}


def error_result(error):
    from neo4j.exceptions import DriverError, Neo4jError
    from .provider import ProviderError
    if isinstance(error,ConflictError):
        detail={'code':error.code,'message':str(error),**error.details}
    elif isinstance(error,ForgottenError):
        detail={'code':'memory_forgotten','message':str(error)}
    elif isinstance(error,NotInitializedError):
        detail={'code':'not_initialized','message':str(error)}
    elif isinstance(error,ProviderError):
        detail={'code':'provider_error','message':str(error)}
    elif isinstance(error,(DriverError,Neo4jError)):
        detail={'code':'storage_error','message':'Neo4j 操作失败，请检查数据库服务和连接配置'}
    else:
        detail={'code':'invalid_arguments','message':str(error)}
    return {'ok':False,'error':detail}
