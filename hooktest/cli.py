import os.path
import sys
from typing import List
import click
import tabulate
import textwrap
from .tester import Tester, Log
from tqdm import tqdm

def to_small_caps(text):
    small_caps_map = str.maketrans(
        "abcdefghijklmnopqrstuvwxyz",
        "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"
    )
    return text.translate(small_caps_map)


class CustomLogger:
    def __init__(self, level: str = "minimal"):
        """

        minimal
        verbose

        """
        self._eq = {
            "minimal": 10, # Only bugs and necessary info is shown
            "details": 5,  # Some information is kept
            "verbose": 0   # Everything is shown
        }
        self.level = self._eq[level]
        self.width = 88
        self._table_indent = "\n    "

    def _print(self, string, level: str = "minimal", indent: int = 0, color : str = None, **kwargs):
        if self._eq[level] >= self.level:
            click.echo(click.style(indent*"\t"+string, fg=color, **kwargs))

    def header(self, string, level: str = "minimal"):
        self._print(f"\n==== {to_small_caps(string.title())} ====", level=level, bold=True)

    def info(self, string, level: str = "minimal", indent: int = 0):
        self._print(f"INFO: {string}", level=level, indent=indent, color="cyan")

    def filter_logs(self, content: List[Log]):
        return [
            (
                f"{log.name}: " + self.green_red(log.details or "✔", log.status) if log.status else (
                    f"{log.name}: " + self.green_red(
                        self._table_indent.join(textwrap.wrap(log.details, width=self.width)),
                        log.status
                    )
                )
            )
            for log in content
            if log.status == False or self.level <= 0
        ]

    def filter_append(self, haystack: List, hay, level: str = "minimal"):
        if self._eq[level] >= self.level:
            haystack.append(hay)

    def green_red(self, string: str, status: bool):
        if not status:
            if self._table_indent in string:
                return (click.style(" ", fg="reset")+self._table_indent).join(
                    click.style(substring, fg="red")
                    for substring in string.split(self._table_indent)
                )
            return click.style(string, fg="red")
        elif status and self.level <= 5:  # Only show green in details / verbose
            return click.style(string, fg="green")
        return string

    def checkmark(self, status):
        if status:
            return self.green_red("✔", status)
        return self.green_red("✗", status)

@click.command
@click.argument("files", nargs=-1, type=click.Path(file_okay=True, dir_okay=False, exists=True))
@click.option("-m", "--include-metadata-report", is_flag=True, default=False)
@click.option("-v", "--verbosity", default="minimal", type=click.Choice(["minimal", "details", "verbose"]))
@click.option("-p", "--progress", default=False, is_flag=True, help="Enable progress bar")
@click.option("--catalog/--no-catalog", default=True, is_flag=True,
              help="Use --no-catalog when you only one to test single files")
def cli(files, include_metadata_report: bool, verbosity: str, catalog: bool, progress: bool):
    tester = Tester()
    printer = CustomLogger(verbosity)
    if catalog:
        count_collections, count_resources = tester.ingest(files)
    else:
        count_resources = tester.ingest_tei_only(files)
        count_collections = 0

    if catalog:
        printer.info(f"Found {count_collections} collection(s)")
    printer.info(f"Found {count_resources} resource(s)")

    #
    #  Collection files
    #
    if catalog:
        printer.header("Report: Catalog files")
        table = [["File", "Status", "Tests"]]
        for file, result in tester.results.items():
            printer.filter_append(
                haystack=table,
                hay=[
                    file,
                    printer.checkmark(result.status),
                    "\n".join(printer.filter_logs(result.statuses))
                ],
                level="minimal"
            )
        click.echo(tabulate.tabulate(table, tablefmt="grid"))

    #
    #  Metadata
    #
    if catalog and include_metadata_report:
        printer.header("Report: Metadata")
        table = [["Identifier", "Key", "Language", "Metadata"]]
        for identifier, collection in tester.catalog.objects.items():
            table.append([identifier, "title", "", collection.title])
            if collection.description:
                table.append([identifier, "description", "", collection.description])
            for dc in collection.dublin_core:
                table.append([identifier, f"dc:{dc.term}", dc.language or "", dc.value])
            for ex in collection.extensions:
                table.append([identifier, f"{ex.term}", ex.language or "", ex.value])
        click.echo(tabulate.tabulate(table))

    #
    #  Texts
    #
    printer.header("Report: TEI files")
    table = [["File", "Status", "Tests"]]
    global_status = []
    for test, status in tester.tests(pbar=tqdm() if progress else None) .items():
        result = tester.results[test]
        global_status.append(status)
        printer.filter_append(
            haystack=table,
            hay=[
                os.path.relpath(test),
                printer.checkmark(result.status),
                "\n".join(printer.filter_logs(result.statuses))
            ],
            level="minimal"
        )
    click.echo(tabulate.tabulate(table, tablefmt="grid"))
    if False not in global_status:
        click.echo("All tests passed")
    else:
        click.echo(f"{global_status.count(False)/len(global_status)*100:.2f}% of the files "
                   f"failed (Abs: {global_status.count(False)}/{len(files)})")
        sys.exit(1)
    return tester

if __name__ == "__main__":
    cli()