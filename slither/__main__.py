#!/usr/bin/env python3

import argparse
import cProfile
import glob
import inspect
import json
import logging
import os
import pickle
import pstats
import sys
import traceback
from importlib import metadata
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Type, Union


from crytic_compile import cryticparser, CryticCompile
from crytic_compile.platform.standard import generate_standard_export
from crytic_compile.platform.etherscan import SUPPORTED_NETWORK
from crytic_compile import compile_all, is_supported

from slither.detectors import all_detectors
from slither.detectors.abstract_detector import AbstractDetector, DetectorClassification
from slither.printers import all_printers
from slither.printers.abstract_printer import AbstractPrinter
from slither.slither import Slither
from slither.utils import codex
from slither.utils.output import (
    output_to_json,
    output_to_zip,
    output_to_sarif,
    ZIP_TYPES_ACCEPTED,
    Output,
)
from slither.utils.output_capture import StandardOutputCapture
from slither.utils.colors import red, set_colorization_enabled
from slither.utils.command_line import (
    FailOnLevel,
    output_detectors,
    output_results_to_markdown,
    output_detectors_json,
    output_printers,
    output_printers_json,
    output_to_markdown,
    output_wiki,
    defaults_flag_in_config,
    read_config_file,
    JSON_OUTPUT_TYPES,
    DEFAULT_JSON_OUTPUT_TYPES,
    check_and_sanitize_markdown_root,
)
from slither.exceptions import SlitherException

logging.basicConfig()
logger = logging.getLogger("Slither")


###################################################################################
###################################################################################
# region Process functions
###################################################################################
###################################################################################


def process_single(
    target: Union[str, CryticCompile],
    args: argparse.Namespace,
    detector_classes: List[Type[AbstractDetector]],
    printer_classes: List[Type[AbstractPrinter]],
) -> Tuple[Slither, List[Dict], List[Output], int]:
    """
    The core high-level code for running Slither static analysis.

    Returns:
        list(result), int: Result list and number of contracts analyzed
    """
    ast = "--ast-compact-json"
    if args.legacy_ast:
        ast = "--ast-json"
    slither = Slither(target, ast_format=ast, **vars(args))

    if args.sarif_input:
        slither.sarif_input = args.sarif_input
    if args.sarif_triage:
        slither.sarif_triage = args.sarif_triage

    return _process(slither, detector_classes, printer_classes)


def process_all(
    target: str,
    args: argparse.Namespace,
    detector_classes: List[Type[AbstractDetector]],
    printer_classes: List[Type[AbstractPrinter]],
) -> Tuple[List[Slither], List[Dict], List[Output], int]:
    if args.compilations_pkl_path is None:
        compilations = compile_all(target, **vars(args))
    else:
        pkl_path = os.path.join(target, args.compilations_pkl_path)
        logger.info(f"Importing existing compilations from {pkl_path}")
        with open(pkl_path, 'rb') as f:
            compilations = pickle.load(f)
    slither_instances = []
    results_detectors = []
    results_printers = []
    analyzed_contracts_count = 0
    for compilation in compilations:
        (
            slither,
            current_results_detectors,
            current_results_printers,
            current_analyzed_count,
        ) = process_single(compilation, args, detector_classes, printer_classes)
        results_detectors.extend(current_results_detectors)
        results_printers.extend(current_results_printers)
        slither_instances.append(slither)
        analyzed_contracts_count += current_analyzed_count
    return (
        slither_instances,
        results_detectors,
        results_printers,
        analyzed_contracts_count,
    )


def _process(
    slither: Slither,
    detector_classes: List[Type[AbstractDetector]],
    printer_classes: List[Type[AbstractPrinter]],
) -> Tuple[Slither, List[Dict], List[Output], int]:
    for detector_cls in detector_classes:
        slither.register_detector(detector_cls)

    for printer_cls in printer_classes:
        slither.register_printer(printer_cls)

    analyzed_contracts_count = len(slither.contracts)

    results_detectors = []
    results_printers = []

    if not printer_classes:
        detector_resultss = slither.run_detectors()
        detector_resultss = [x for x in detector_resultss if x]  # remove empty results
        detector_results = [item for sublist in detector_resultss for item in sublist]  # flatten
        results_detectors.extend(detector_results)

    else:
        printer_results = slither.run_printers()
        printer_results = [x for x in printer_results if x]  # remove empty results
        results_printers.extend(printer_results)

    return slither, results_detectors, results_printers, analyzed_contracts_count


# endregion
###################################################################################
###################################################################################

# region Detectors and printers
###################################################################################
###################################################################################


def get_detectors_and_printers() -> Tuple[
    List[Type[AbstractDetector]], List[Type[AbstractPrinter]]
]:
    detectors_ = [getattr(all_detectors, name) for name in dir(all_detectors)]
    detectors = [d for d in detectors_ if inspect.isclass(d) and issubclass(d, AbstractDetector)]

    printers_ = [getattr(all_printers, name) for name in dir(all_printers)]
    printers = [p for p in printers_ if inspect.isclass(p) and issubclass(p, AbstractPrinter)]

    # Handle plugins!
    if sys.version_info >= (3, 10):
        entry_points = metadata.entry_points(group="slither_analyzer.plugin")
    else:
        from pkg_resources import iter_entry_points  # pylint: disable=import-outside-toplevel

        entry_points = iter_entry_points(group="slither_analyzer.plugin", name=None)

    for entry_point in entry_points:
        make_plugin = entry_point.load()

        plugin_detectors, plugin_printers = make_plugin()

        detector = None
        if not all(issubclass(detector, AbstractDetector) for detector in plugin_detectors):
            raise ValueError(
                f"Error when loading plugin {entry_point}, {detector} is not a detector"
            )
        printer = None
        if not all(issubclass(printer, AbstractPrinter) for printer in plugin_printers):
            raise ValueError(f"Error when loading plugin {entry_point}, {printer} is not a printer")

        # We convert those to lists in case someone returns a tuple
        detectors += list(plugin_detectors)
        printers += list(plugin_printers)

    return detectors, printers


# pylint: disable=too-many-branches
def choose_detectors(
    args: argparse.Namespace, all_detector_classes: List[Type[AbstractDetector]]
) -> List[Type[AbstractDetector]]:
    # If detectors are specified, run only these ones

    detectors_to_run = []
    detectors = {d.ARGUMENT: d for d in all_detector_classes}

    if args.detectors_to_run == "all":
        detectors_to_run = all_detector_classes
    else:
        detectors_to_run = __include_detectors(
            set(detectors_to_run), args.detectors_to_run, detectors
        )
        return detectors_to_run

    classification_map = {
        DetectorClassification.HIGH: args.exclude_high,
        DetectorClassification.MEDIUM: args.exclude_medium,
        DetectorClassification.LOW: args.exclude_low,
        DetectorClassification.INFORMATIONAL: args.exclude_informational,
        DetectorClassification.OPTIMIZATION: args.exclude_optimization,
    }
    excluded_classification = [
        classification for classification, included in classification_map.items() if included
    ]
    detectors_to_run = [d for d in detectors_to_run if d.IMPACT not in excluded_classification]

    if args.detectors_to_exclude:
        detectors_to_run = [
            d for d in detectors_to_run if d.ARGUMENT not in args.detectors_to_exclude
        ]

    if args.detectors_to_include:
        detectors_to_run = __include_detectors(
            set(detectors_to_run), args.detectors_to_include, detectors
        )

    detectors_to_run = sorted(detectors_to_run, key=lambda x: x.IMPACT)
    return detectors_to_run


def __include_detectors(
    detectors_to_run: Set[Type[AbstractDetector]],
    detectors_to_include: str,
    detectors: Dict[str, Type[AbstractDetector]],
) -> List[Type[AbstractDetector]]:
    include_detectors = detectors_to_include.split(",")

    for detector in include_detectors:
        if detector in detectors:
            detectors_to_run.add(detectors[detector])
        else:
            raise ValueError(f"Error: {detector} is not a detector")

    return detectors_to_run


def choose_printers(
    args: argparse.Namespace, all_printer_classes: List[Type[AbstractPrinter]]
) -> List[Type[AbstractPrinter]]:
    printers_to_run = []

    # disable default printer
    if args.printers_to_run is None:
        return []

    if args.printers_to_run == "all":
        return all_printer_classes

    printers = {p.ARGUMENT: p for p in all_printer_classes}
    for printer in args.printers_to_run.split(","):
        if printer in printers:
            printers_to_run.append(printers[printer])
        else:
            raise ValueError(f"Error: {printer} is not a printer")
    return printers_to_run


# endregion
###################################################################################
###################################################################################
# region Command line parsing
###################################################################################
###################################################################################


def parse_filter_paths(args: argparse.Namespace, filter_path: bool) -> List[str]:
    paths = args.filter_paths if filter_path else args.include_paths
    if paths:
        return paths.split(",")
    return []


# pylint: disable=too-many-statements
def parse_args(
    detector_classes: List[Type[AbstractDetector]], printer_classes: List[Type[AbstractPrinter]]
) -> argparse.Namespace:
    usage = "slither target [flag]\n"
    usage += "\ntarget can be:\n"
    usage += "\t- file.sol // a Solidity file\n"
    usage += "\t- project_directory // a project directory. See https://github.com/crytic/crytic-compile/#crytic-compile for the supported platforms\n"
    usage += "\t- 0x.. // a contract on mainnet\n"
    usage += f"\t- NETWORK:0x.. // a contract on a different network. Supported networks: {','.join(x[:-1] for x in SUPPORTED_NETWORK)}\n"

    parser = argparse.ArgumentParser(
        description="For usage information, see https://github.com/crytic/slither/wiki/Usage",
        usage=usage,
    )

    parser.add_argument("filename", help=argparse.SUPPRESS)

    cryticparser.init(parser)

    parser.add_argument(
        "--import-from-autobuild",
        help="Import compilations from autobuild and skip building",
        action="store",
        dest="compilations_pkl_path",
        default=None
    )

    parser.add_argument(
        "--version",
        help="displays the current version",
        version=metadata.version("slither-analyzer"),
        action="version",
    )

    group_detector = parser.add_argument_group("Detectors")
    group_printer = parser.add_argument_group("Printers")
    group_checklist = parser.add_argument_group(
        "Checklist (consider using https://github.com/crytic/slither-action)"
    )
    group_misc = parser.add_argument_group("Additional options")
    group_filters = parser.add_mutually_exclusive_group()

    group_detector.add_argument(
        "--detect",
        help="Comma-separated list of detectors, defaults to all, "
        f"available detectors: {', '.join(d.ARGUMENT for d in detector_classes)}",
        action="store",
        dest="detectors_to_run",
        default=defaults_flag_in_config["detectors_to_run"],
    )

    group_printer.add_argument(
        "--print",
        help="Comma-separated list of contract information printers, "
        f"available printers: {', '.join(d.ARGUMENT for d in printer_classes)}",
        action="store",
        dest="printers_to_run",
        default=defaults_flag_in_config["printers_to_run"],
    )

    group_printer.add_argument(
        "--include-interfaces",
        help="Include interfaces from inheritance-graph printer",
        action="store_true",
        dest="include_interfaces",
        default=False,
    )

    group_detector.add_argument(
        "--list-detectors",
        help="List available detectors",
        action=ListDetectors,
        nargs=0,
        default=False,
    )

    group_printer.add_argument(
        "--list-printers",
        help="List available printers",
        action=ListPrinters,
        nargs=0,
        default=False,
    )

    group_detector.add_argument(
        "--exclude",
        help="Comma-separated list of detectors that should be excluded",
        action="store",
        dest="detectors_to_exclude",
        default=defaults_flag_in_config["detectors_to_exclude"],
    )

    group_detector.add_argument(
        "--exclude-dependencies",
        help="Exclude results that are only related to dependencies",
        action="store_true",
        default=defaults_flag_in_config["exclude_dependencies"],
    )

    group_detector.add_argument(
        "--exclude-optimization",
        help="Exclude optimization analyses",
        action="store_true",
        default=defaults_flag_in_config["exclude_optimization"],
    )

    group_detector.add_argument(
        "--exclude-informational",
        help="Exclude informational impact analyses",
        action="store_true",
        default=defaults_flag_in_config["exclude_informational"],
    )

    group_detector.add_argument(
        "--exclude-low",
        help="Exclude low impact analyses",
        action="store_true",
        default=defaults_flag_in_config["exclude_low"],
    )

    group_detector.add_argument(
        "--exclude-medium",
        help="Exclude medium impact analyses",
        action="store_true",
        default=defaults_flag_in_config["exclude_medium"],
    )

    group_detector.add_argument(
        "--exclude-high",
        help="Exclude high impact analyses",
        action="store_true",
        default=defaults_flag_in_config["exclude_high"],
    )

    group_detector.add_argument(
        "--include-detectors",
        help="Comma-separated list of detectors that should be included",
        action="store",
        dest="detectors_to_include",
        default=defaults_flag_in_config["detectors_to_include"],
    )

    fail_on_group = group_detector.add_mutually_exclusive_group()
    fail_on_group.add_argument(
        "--fail-pedantic",
        help="Fail if any findings are detected",
        action="store_const",
        dest="fail_on",
        const=FailOnLevel.PEDANTIC,
    )
    fail_on_group.add_argument(
        "--fail-low",
        help="Fail if any low or greater impact findings are detected",
        action="store_const",
        dest="fail_on",
        const=FailOnLevel.LOW,
    )
    fail_on_group.add_argument(
        "--fail-medium",
        help="Fail if any medium or greater impact findings are detected",
        action="store_const",
        dest="fail_on",
        const=FailOnLevel.MEDIUM,
    )
    fail_on_group.add_argument(
        "--fail-high",
        help="Fail if any high impact findings are detected",
        action="store_const",
        dest="fail_on",
        const=FailOnLevel.HIGH,
    )
    fail_on_group.add_argument(
        "--fail-none",
        "--no-fail-pedantic",
        help="Do not return the number of findings in the exit code",
        action="store_const",
        dest="fail_on",
        const=FailOnLevel.NONE,
    )
    fail_on_group.set_defaults(fail_on=FailOnLevel.PEDANTIC)

    group_detector.add_argument(
        "--show-ignored-findings",
        help="Show all the findings",
        action="store_true",
        default=defaults_flag_in_config["show_ignored_findings"],
    )

    group_checklist.add_argument(
        "--checklist",
        help="Generate a markdown page with the detector results",
        action="store_true",
        default=False,
    )

    group_checklist.add_argument(
        "--checklist-limit",
        help="Limit the number of results per detector in the markdown file",
        action="store",
        default="",
    )

    group_checklist.add_argument(
        "--markdown-root",
        type=check_and_sanitize_markdown_root,
        help="URL for markdown generation",
        action="store",
        default="",
    )

    group_misc.add_argument(
        "--json",
        help='Export the results as a JSON file ("--json -" to export to stdout)',
        action="store",
        default=defaults_flag_in_config["json"],
    )

    group_misc.add_argument(
        "--sarif",
        help='Export the results as a SARIF JSON file ("--sarif -" to export to stdout)',
        action="store",
        default=defaults_flag_in_config["sarif"],
    )

    group_misc.add_argument(
        "--sarif-input",
        help="Sarif input (beta)",
        action="store",
        default=defaults_flag_in_config["sarif_input"],
    )

    group_misc.add_argument(
        "--sarif-triage",
        help="Sarif triage (beta)",
        action="store",
        default=defaults_flag_in_config["sarif_triage"],
    )

    group_misc.add_argument(
        "--json-types",
        help="Comma-separated list of result types to output to JSON, defaults to "
        + f'{",".join(output_type for output_type in DEFAULT_JSON_OUTPUT_TYPES)}. '
        + f'Available types: {",".join(output_type for output_type in JSON_OUTPUT_TYPES)}',
        action="store",
        default=defaults_flag_in_config["json-types"],
    )

    group_misc.add_argument(
        "--zip",
        help="Export the results as a zipped JSON file",
        action="store",
        default=defaults_flag_in_config["zip"],
    )

    group_misc.add_argument(
        "--zip-type",
        help=f'Zip compression type. One of {",".join(ZIP_TYPES_ACCEPTED.keys())}. Default lzma',
        action="store",
        default=defaults_flag_in_config["zip_type"],
    )

    group_misc.add_argument(
        "--disable-color",
        help="Disable output colorization",
        action="store_true",
        default=defaults_flag_in_config["disable_color"],
    )

    group_misc.add_argument(
        "--triage-mode",
        help="Run triage mode (save results in triage database)",
        action="store_true",
        dest="triage_mode",
        default=False,
    )

    group_misc.add_argument(
        "--triage-database",
        help="File path to the triage database (default: slither.db.json)",
        action="store",
        dest="triage_database",
        default=defaults_flag_in_config["triage_database"],
    )

    group_misc.add_argument(
        "--config-file",
        help="Provide a config file (default: slither.config.json)",
        action="store",
        dest="config_file",
        default=None,
    )

    group_misc.add_argument(
        "--change-line-prefix",
        help="Change the line prefix (default #) for the displayed source codes (i.e. file.sol#1).",
        action="store",
        dest="change_line_prefix",
        default="#",
    )

    group_misc.add_argument(
        "--solc-ast",
        help="Provide the contract as a json AST",
        action="store_true",
        default=False,
    )

    group_misc.add_argument(
        "--generate-patches",
        help="Generate patches (json output only)",
        action="store_true",
        default=False,
    )

    group_misc.add_argument(
        "--no-fail",
        help="Do not fail in case of parsing (echidna mode only)",
        action="store_true",
        default=defaults_flag_in_config["no_fail"],
    )

    group_filters.add_argument(
        "--filter-paths",
        help="Regex filter to exclude detector results matching file path e.g. (mocks/|test/)",
        action="store",
        dest="filter_paths",
        default=defaults_flag_in_config["filter_paths"],
    )

    group_filters.add_argument(
        "--include-paths",
        help="Regex filter to include detector results matching file path e.g. (src/|contracts/). Opposite of --filter-paths",
        action="store",
        dest="include_paths",
        default=defaults_flag_in_config["include_paths"],
    )

    codex.init_parser(parser)

    # debugger command
    parser.add_argument("--debug", help=argparse.SUPPRESS, action="store_true", default=False)

    parser.add_argument("--markdown", help=argparse.SUPPRESS, action=OutputMarkdown, default=False)

    parser.add_argument(
        "--wiki-detectors", help=argparse.SUPPRESS, action=OutputWiki, default=False
    )

    parser.add_argument(
        "--list-detectors-json",
        help=argparse.SUPPRESS,
        action=ListDetectorsJson,
        nargs=0,
        default=False,
    )

    parser.add_argument(
        "--legacy-ast",
        help=argparse.SUPPRESS,
        action="store_true",
        default=defaults_flag_in_config["legacy_ast"],
    )

    parser.add_argument(
        "--skip-assembly",
        help=argparse.SUPPRESS,
        action="store_true",
        default=defaults_flag_in_config["skip_assembly"],
    )

    parser.add_argument(
        "--perf",
        help=argparse.SUPPRESS,
        action="store_true",
        default=False,
    )

    # Disable the throw/catch on partial analyses
    parser.add_argument(
        "--disallow-partial", help=argparse.SUPPRESS, action="store_true", default=False
    )

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()
    read_config_file(args)

    args.filter_paths = parse_filter_paths(args, True)
    args.include_paths = parse_filter_paths(args, False)

    # Verify our json-type output is valid
    args.json_types = set(args.json_types.split(","))  # type:ignore
    for json_type in args.json_types:
        if json_type not in JSON_OUTPUT_TYPES:
            raise ValueError(f'Error: "{json_type}" is not a valid JSON result output type.')

    return args


class ListDetectors(argparse.Action):  # pylint: disable=too-few-public-methods
    def __call__(
        self, parser: Any, *args: Any, **kwargs: Any
    ) -> None:  # pylint: disable=signature-differs
        detectors, _ = get_detectors_and_printers()
        output_detectors(detectors)
        parser.exit()


class ListDetectorsJson(argparse.Action):  # pylint: disable=too-few-public-methods
    def __call__(
        self, parser: Any, *args: Any, **kwargs: Any
    ) -> None:  # pylint: disable=signature-differs
        detectors, _ = get_detectors_and_printers()
        detector_types_json = output_detectors_json(detectors)
        print(json.dumps(detector_types_json))
        parser.exit()


class ListPrinters(argparse.Action):  # pylint: disable=too-few-public-methods
    def __call__(
        self, parser: Any, *args: Any, **kwargs: Any
    ) -> None:  # pylint: disable=signature-differs
        _, printers = get_detectors_and_printers()
        output_printers(printers)
        parser.exit()


class OutputMarkdown(argparse.Action):  # pylint: disable=too-few-public-methods
    def __call__(
        self,
        parser: Any,
        args: Any,
        values: Optional[Union[str, Sequence[Any]]],
        option_string: Any = None,
    ) -> None:
        detectors, printers = get_detectors_and_printers()
        assert isinstance(values, str)
        output_to_markdown(detectors, printers, values)
        parser.exit()


class OutputWiki(argparse.Action):  # pylint: disable=too-few-public-methods
    def __call__(
        self,
        parser: Any,
        args: Any,
        values: Optional[Union[str, Sequence[Any]]],
        option_string: Any = None,
    ) -> None:
        detectors, _ = get_detectors_and_printers()
        assert isinstance(values, str)
        output_wiki(detectors, values)
        parser.exit()


# endregion
###################################################################################
###################################################################################
# region CustomFormatter
###################################################################################
###################################################################################


class FormatterCryticCompile(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # for i, msg in enumerate(record.msg):
        if record.msg.startswith("Compilation warnings/errors on "):
            txt = record.args[1]  # type:ignore
            txt = txt.split("\n")  # type:ignore
            txt = [red(x) if "Error" in x else x for x in txt]
            txt = "\n".join(txt)
            record.args = (record.args[0], txt)  # type:ignore
        return super().format(record)


# endregion
###################################################################################
###################################################################################
# region Main
###################################################################################
###################################################################################


def main() -> None:
    # Codebase with complex domninators can lead to a lot of SSA recursive call
    sys.setrecursionlimit(1500)

    detectors, printers = get_detectors_and_printers()

    main_impl(all_detector_classes=detectors, all_printer_classes=printers)


# pylint: disable=too-many-statements,too-many-branches,too-many-locals
def main_impl(
    all_detector_classes: List[Type[AbstractDetector]],
    all_printer_classes: List[Type[AbstractPrinter]],
) -> None:
    """
    :param all_detector_classes: A list of all detectors that can be included/excluded.
    :param all_printer_classes: A list of all printers that can be included.
    """
    # Set logger of Slither to info, to catch warnings related to the arg parsing
    logger.setLevel(logging.INFO)
    args = parse_args(all_detector_classes, all_printer_classes)

    cp: Optional[cProfile.Profile] = None
    if args.perf:
        cp = cProfile.Profile()
        cp.enable()

    # Set colorization option
    set_colorization_enabled(False if args.disable_color else sys.stdout.isatty())

    # Define some variables for potential JSON output
    json_results: Dict[str, Any] = {}
    output_error = None
    outputting_json = args.json is not None
    outputting_json_stdout = args.json == "-"
    outputting_sarif = args.sarif is not None
    outputting_sarif_stdout = args.sarif == "-"
    outputting_zip = args.zip is not None
    if args.zip_type not in ZIP_TYPES_ACCEPTED:
        to_log = f'Zip type not accepted, it must be one of {",".join(ZIP_TYPES_ACCEPTED.keys())}'
        logger.error(to_log)

    # If we are outputting JSON, capture all standard output. If we are outputting to stdout, we block typical stdout
    # output.
    if outputting_json or outputting_sarif:
        StandardOutputCapture.enable(outputting_json_stdout or outputting_sarif_stdout)

    printer_classes = choose_printers(args, all_printer_classes)
    detector_classes = choose_detectors(args, all_detector_classes)

    default_log = logging.INFO if not args.debug else logging.DEBUG

    for (l_name, l_level) in [
        ("Slither", default_log),
        ("Contract", default_log),
        ("Function", default_log),
        ("Node", default_log),
        ("Parsing", default_log),
        ("Detectors", default_log),
        ("FunctionSolc", default_log),
        ("ExpressionParsing", default_log),
        ("TypeParsing", default_log),
        ("SSA_Conversion", default_log),
        ("Printers", default_log),
        # ('CryticCompile', default_log)
    ]:
        logger_level = logging.getLogger(l_name)
        logger_level.setLevel(l_level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    console_handler.setFormatter(FormatterCryticCompile())

    crytic_compile_error = logging.getLogger(("CryticCompile"))
    crytic_compile_error.addHandler(console_handler)
    crytic_compile_error.propagate = False
    crytic_compile_error.setLevel(logging.INFO)

    results_detectors: List[Dict] = []
    results_printers: List[Output] = []
    try:
        filename = args.filename

        # Determine if we are handling ast from solc
        if args.solc_ast or (filename.endswith(".json") and not is_supported(filename)):
            globbed_filenames = glob.glob(filename, recursive=True)
            filenames = glob.glob(os.path.join(filename, "*.json"))
            if not filenames:
                filenames = globbed_filenames
            number_contracts = 0

            slither_instances = []
            for filename in filenames:
                (
                    slither_instance,
                    results_detectors_tmp,
                    results_printers_tmp,
                    number_contracts_tmp,
                ) = process_single(filename, args, detector_classes, printer_classes)
                number_contracts += number_contracts_tmp
                results_detectors += results_detectors_tmp
                results_printers += results_printers_tmp
                slither_instances.append(slither_instance)

        # Rely on CryticCompile to discern the underlying type of compilations.
        else:
            (
                slither_instances,
                results_detectors,
                results_printers,
                number_contracts,
            ) = process_all(filename, args, detector_classes, printer_classes)

        # Determine if we are outputting JSON
        if outputting_json or outputting_zip or output_to_sarif:
            # Add our compilation information to JSON
            if "compilations" in args.json_types:
                compilation_results = []
                for slither_instance in slither_instances:
                    assert slither_instance.crytic_compile
                    compilation_results.append(
                        generate_standard_export(slither_instance.crytic_compile)
                    )
                json_results["compilations"] = compilation_results

            # Add our detector results to JSON if desired.
            if results_detectors and "detectors" in args.json_types:
                json_results["detectors"] = results_detectors

            # Add our printer results to JSON if desired.
            if results_printers and "printers" in args.json_types:
                json_results["printers"] = results_printers

            # Add our detector types to JSON
            if "list-detectors" in args.json_types:
                detectors, _ = get_detectors_and_printers()
                json_results["list-detectors"] = output_detectors_json(detectors)

            # Add our detector types to JSON
            if "list-printers" in args.json_types:
                _, printers = get_detectors_and_printers()
                json_results["list-printers"] = output_printers_json(printers)

        # Output our results to markdown if we wish to compile a checklist.
        if args.checklist:
            output_results_to_markdown(
                results_detectors, args.checklist_limit, args.show_ignored_findings
            )

        # Don't print the number of result for printers
        if number_contracts == 0:
            logger.warning(red("No contract was analyzed"))
        if printer_classes:
            logger.info("%s analyzed (%d contracts)", filename, number_contracts)
        else:
            logger.info(
                "%s analyzed (%d contracts with %d detectors), %d result(s) found",
                filename,
                number_contracts,
                len(detector_classes),
                len(results_detectors),
            )

    except SlitherException as slither_exception:
        output_error = str(slither_exception)
        traceback.print_exc()
        logging.error(red("Error:"))
        logging.error(red(output_error))
        logging.error("Please report an issue to https://github.com/crytic/slither/issues")

    # If we are outputting JSON, capture the redirected output and disable the redirect to output the final JSON.
    if outputting_json:
        if "console" in args.json_types:
            json_results["console"] = {
                "stdout": StandardOutputCapture.get_stdout_output(),
                "stderr": StandardOutputCapture.get_stderr_output(),
            }
        StandardOutputCapture.disable()
        output_to_json(None if outputting_json_stdout else args.json, output_error, json_results)

    if outputting_sarif:
        StandardOutputCapture.disable()
        output_to_sarif(
            None if outputting_sarif_stdout else args.sarif, json_results, detector_classes
        )

    if outputting_zip:
        output_to_zip(args.zip, output_error, json_results, args.zip_type)

    if args.perf and cp:
        cp.disable()
        stats = pstats.Stats(cp).sort_stats("cumtime")
        stats.print_stats()

    fail_on = FailOnLevel(args.fail_on)
    if fail_on == FailOnLevel.HIGH:
        fail_on_detection = any(result["impact"] == "High" for result in results_detectors)
    elif fail_on == FailOnLevel.MEDIUM:
        fail_on_detection = any(
            result["impact"] in ["Medium", "High"] for result in results_detectors
        )
    elif fail_on == FailOnLevel.LOW:
        fail_on_detection = any(
            result["impact"] in ["Low", "Medium", "High"] for result in results_detectors
        )
    elif fail_on == FailOnLevel.PEDANTIC:
        fail_on_detection = bool(results_detectors)
    else:
        fail_on_detection = False

    # Exit with them appropriate status code
    if output_error or fail_on_detection:
        sys.exit(-1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

# endregion
