"""
    Module printing summary of the contract using CertiK IR
"""
from typing import List

from slither.printers.summary.slithir import PrinterSlithIR

class PrinterCertiKIR(PrinterSlithIR):
    ARGUMENT = "certikir"
    HELP = "Print the Certik IR representation of the functions"

    @staticmethod
    def uses_certik_ir() -> bool:
        return True
