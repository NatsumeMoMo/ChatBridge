class BridgeError(Exception):
    def __init__(self,code,message='',retryable=False):
        self.code=code;self.message=message or code;self.retryable=retryable
        super().__init__(self.message)

def require(condition,code='INVALID_ARGUMENT',message=''):
    if not condition:raise BridgeError(code,message)
