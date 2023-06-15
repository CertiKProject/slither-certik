import abc
from logging import Logger

from typing import TYPE_CHECKING, Union, List, Optional, Dict

from slither.utils import output
from slither.utils.output import SupportedOutput

if TYPE_CHECKING:
    from slither import Slither


class IncorrectPrinterInitialization(Exception):
    pass


class AbstractPrinter(metaclass=abc.ABCMeta):
    ARGUMENT = ""  # run the printer with slither.py --ARGUMENT
    HELP = ""  # help information

    WIKI = ""

    def __init__(self, slither: "Slither", logger: Logger) -> None:
        self.slither = slither
        self.filename = slither.filename
        self.logger = logger
        self._contracts = None

        if not self.HELP:
            raise IncorrectPrinterInitialization(
                f"HELP is not initialized {self.__class__.__name__}"
            )

        if not self.ARGUMENT:
            raise IncorrectPrinterInitialization(
                f"ARGUMENT is not initialized {self.__class__.__name__}"
            )

        if not self.WIKI:
            raise IncorrectPrinterInitialization(
                f"WIKI is not initialized {self.__class__.__name__}"
            )

    @staticmethod
    def uses_certik_ir() -> bool:
        """
        Does this detector expect the CertiK version of SlithIR?
        """
        return False

    @property
    def compilation_units(self):
        if type(self).uses_certik_ir():
            return self.slither.certik_compilation_units
        else:
            return self.slither.compilation_units

    @property
    def contracts(self):
        if not self._contracts:
            all_contracts = [
                compilation_unit.contracts for compilation_unit in self.compilation_units
            ]
            self._contracts = [item for sublist in all_contracts for item in sublist]

        return self._contracts

    def info(self, info: str) -> None:
        if self.logger:
            self.logger.info(info)

    def generate_output(
        self,
        info: Union[str, List[Union[str, SupportedOutput]]],
        additional_fields: Optional[Dict] = None,
    ) -> output.Output:
        if additional_fields is None:
            additional_fields = {}
        printer_output = output.Output(info, additional_fields)
        printer_output.data["printer"] = self.ARGUMENT

        return printer_output

    @abc.abstractmethod
    def output(self, filename: str) -> output.Output:
        pass
