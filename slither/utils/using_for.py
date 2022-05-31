from typing import Dict, List, Tuple, TYPE_CHECKING, Union
from slither.core.solidity_types import Type, UserDefinedType

if TYPE_CHECKING:
    from slither.core.declarations import Function, StructureContract

USING_FOR_KEY = Union[str, Type]  # "*" is wildcard
USING_FOR_ITEM = List[Union[UserDefinedType, "Function"]]  # UserDefinedType.type is a library
USING_FOR_SRC_ITEM = List[Tuple[Union[UserDefinedType, "Function"], "StructureContract"]]  # UserDefinedType.type is a library
USING_FOR = Dict[USING_FOR_KEY, USING_FOR_ITEM]
USING_FOR_SRC = Dict[USING_FOR_KEY, USING_FOR_SRC_ITEM]


def merge_using_for(uf1: USING_FOR, uf2: USING_FOR) -> USING_FOR:
    result = {**uf1, **uf2}
    for key, value in result.items():
        if key in uf1 and key in uf2:
            result[key] = value + uf1[key]
    return result
