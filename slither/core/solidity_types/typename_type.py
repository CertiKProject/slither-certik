from typing import Tuple
from slither.core.solidity_types.type import Type

class Typename(Type):
    """
    The type of a typename expression, which appear, for example, in the second argument
    of abi.decode
    """
    def __init__(self, type: Type):
        self.type = type

    type : Type
    """The type that the typename refers to"""


    @property
    def storage_size(self) -> Tuple[int, bool]:
        #todo: Move storage_size into a subclass of Type called PhysicalType
        assert False


    @property
    def is_dynamic(self) -> bool:
        #todo: Move storage_size into a subclass of Type called PhysicalType
        assert False

    def __str__(self) -> str:
        return f"typename[{self.type}]"
