from typing import Any, List, Union
from slither.slithir.operations.call import Call
from slither.slithir.variables.constant import Constant


class EventCall(Call):
    def __init__(self, destination) -> None:
        super().__init__()
        self._destination = destination

    @property
    def destination(self):
        return self._destination

    @property
    def name(self) -> Union[str, Constant]:
        return self._destination.name

    @property
    def read(self) -> List[Any]:
        return self._unroll(self.arguments)

    def __str__(self):
        args = [str(a) for a in self.arguments]
        return f"Emit {self.name}({','.join(args)})"
