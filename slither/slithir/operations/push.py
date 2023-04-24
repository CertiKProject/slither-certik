
from typing import List
from slither.core.solidity_types.array_type import ArrayType
from slither.core.solidity_types.type import Type
from slither.core.variables.local_variable import LocalVariable
from slither.core.variables.state_variable import StateVariable
from slither.core.variables.variable import Variable
from slither.slithir.operations.lvalue import OperationWithLValue
from slither.slithir.operations.operation import Operation
from slither.slithir.utils.utils import is_valid_lvalue
from slither.slithir.variables import (ReferenceVariable, TemporaryVariable)

def _is_storage_variable(var : Variable) -> bool:
    return (
        (isinstance(var, ReferenceVariable) and isinstance(var.points_to_origin, StateVariable))
        or (
            isinstance(var, ReferenceVariable)
            and isinstance(var.points_to_origin, (LocalVariable, TemporaryVariable))
            and var.points_to_origin.location == "storage"
        )
        or (isinstance(var, StateVariable))
        or (isinstance(var, LocalVariable) and var.location == "storage")
    )

class Push(OperationWithLValue):
    def __init__(self, lvalue: Variable, array: Variable):
        """
        Allocate a new default value.

        #### Parameters
        lvalue -
            the lvalue to reference the newly initialized element
        array -
            The array to push an element to
        """
        assert is_valid_lvalue(lvalue)
        assert isinstance(array, Variable) and isinstance(array.type, ArrayType)
        super().__init__()
        self._array = array
        self._lvalue = lvalue

    @property
    def read(self) -> List[Variable]:
        return []

    @property
    def array(self) -> Variable:
        return self._array

    @property
    def storage(self) -> bool:
        """Should we allocate to storage instead of memory?"""
        return _is_storage_variable(self._array)

    @property
    def type(self):
        """Type of element to push"""
        return self._array.type.type

    def __str__(self):
        return f"{self.lvalue} = push {self._array}"
