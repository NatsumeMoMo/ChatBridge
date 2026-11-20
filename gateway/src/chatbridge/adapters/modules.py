from dataclasses import dataclass
from typing import Callable,Any

@dataclass(frozen=True)
class ModuleOperation:
    capability:str
    read_only:bool
    validate:Callable[[dict],dict]
