import dataclasses
import os.path
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple, Union

import tqdm
from dapytains.processor import get_xpath_proc
from dapytains.metadata.classes import Collection
from dapytains.tei.citeStructure import CitableUnit, CitableStructure, CiteStructureParser
from dapytains.tei.document import Document, xpath_eval
from dapytains.metadata.xml_parser import parse, Catalog
from lxml import etree as ET


# Monkey patch for test
def _dispatch(self, child_xpath: str, structure: CitableStructure, xpath_processor, unit: CitableUnit, level: int):
    if len(structure.children) == 1:
        for element in xpath_eval(xpath_processor, child_xpath):
            self.find_refs(
                root=element,
                structure=structure.children[0],
                unit=unit,
                level=level
            )
    else:
        for element in xpath_eval(xpath_processor, child_xpath):
            self.find_refs_from_branches(
                root=element,
                structure=structure.children,
                unit=unit,
                level=level
            )
CiteStructureParser._dispatch = _dispatch

@dataclasses.dataclass
class Log:
    name: str
    status: bool
    exception: Optional[Union[Exception, str]] = None
    details: Optional[str] = None

    def __repr__(self):
        return f"<Log class='{self.name}' status={self.status}>{self.details}</Log>"

@dataclasses.dataclass
class Result:
    target: str
    statuses: List[Log] = dataclasses.field(default_factory=list)

    @property
    def status(self):
        for s in self.statuses:
            if not s.status:
                return False
        return True

    def __repr__(self):
        NL = "\n"
        TB = "\t"
        return f"<Result target='{self.target}'>\n\t{NL.join([TB+repr(log) for log in self.statuses])}\n</Result>"


def _count_tree(units: List[CitableUnit], types = None) -> str:
    types = types if types is not None else {}
    for element in units:
        if element.citeType not in types:
            types[element.citeType] = {
                "count": 0,
                "children": {}
            }
        types[element.citeType]["count"] += 1
        _count_tree(element.children, types[element.citeType]["children"])
    return types


def _stringify_tree_count(tree) -> str:
    return ", ".join([
        f"{level}({details['count']})" + (
            f"->[{_stringify_tree_count(details['children'])}]" if details["children"]
            else ""
        )
        for level, details in tree.items()
    ])

def check_naming_type(struct: CitableStructure) -> Tuple[bool, List[str]]:
    citeType = re.match(r"^\w+$", struct.citeType)
    children = [
        check_naming_type(child)
        for child in struct.children
    ]
    if not citeType:
        return False, [f"`{struct.citeType}`"]
    else:
        return False not in [a for a,b in children], [t for a, b in children for t in b]

def _get_delim(s: CitableStructure) -> List[str]:
    return ([s.delim] if s.delim else []) + [d for c in s.children for d in _get_delim(c)]

def _check_refs(
        document: Document,
        structure: CitableStructure,
        previous_delim: Optional[List[str]] = None,
        base_xpath: str = ""
) -> List[Tuple[str, str, str]]:
    if not previous_delim:
        previous_delim = _get_delim(structure)

    xproc = get_xpath_proc(document.xml, processor=document.xml_processor)
    returns: List[Tuple[str, str, str]] = []

    # There is a limit here to this approach
    # ToDo: Have something to deal with structure.xpath where we ensure that parents have the @n ???
    xpath = "/".join([base_xpath, structure.xpath]) if base_xpath else structure.xpath
    xpath_match = "/".join([base_xpath, structure.xpath_match]) if base_xpath else structure.xpath_match

    for reff in xpath_eval(xproc, xpath):
        reff = reff.get_string_value()
        for delim in previous_delim:
            if delim in reff:
                returns.append((xpath, reff, delim))

    for child in structure.children:
        returns.extend(_check_refs(document, child, previous_delim, xpath_match))

    return returns


def _check_dbl_refs(
        document: Document,
        tree: str
) -> List[Tuple[str, str, int]]:
    """The current system needs to be rerun multiple time, as document.get_refs does not evaluate multiple time the elements"""
    returns = []
    def struct_flatten(units: List[CitableUnit]) -> List[Tuple[str, str]]:
        local_units = [(u.ref, document.citeStructure[tree].generate_xpath(u.ref)) for u in units]
        for u in units:
            if u.children:
                local_units.extend(struct_flatten(u.children))
        return local_units

    counter = Counter(struct_flatten(document.get_reffs(tree)))
    for ((reference, xpath), match_count) in counter.items():
        if match_count > 1:
            count = len(list(xpath_eval(document.xpath_processor, xpath)))
            if count > 1:
                returns.append((xpath, f"`{reference}`", count))

    return returns


class Tester:
    """ Tester class, allows for retrieving results outside of the CLI
    """
    def __init__(self):
        self.catalog = Catalog()
        self.results: Dict[str, Result] = {}

        # Load the Relax NG schema
        self.catalog_schema = ET.RelaxNG(
            ET.parse(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "collection-schema.rng")
            )
        )

    def run_catalog_schema(self, filepath) -> Log:
        status = self.catalog_schema.validate(ET.parse(filepath))
        details = []
        if not status:
            for el in self.catalog_schema.error_log:
                details.append(":".join(str(el).split("\n")[0].split(":")[6:]).strip())
        return Log("schema", status, details="; ".join(details))

    def ingest_tei_only(self, files: List[str]) -> int:
        """ Ingest TEI Files as resources (does not require catalogs)

        :param files: TEI files following the Dapitains structure
        :returns: Number of resources found
        """
        self.catalog.objects = {
            os.path.relpath(file): Collection(
                title=os.path.relpath(file),
                identifier=os.path.relpath(file),
                filepath=os.path.relpath(file),
                resource=True
            )
            for file in files
        }
        return len(self.catalog.objects)

    def ingest(self, files: List[str]) -> Tuple[int, int]:
        """ Ingest catalog(s) files to test resources

        :param files: Catalog files following the Dapitains structure
        :returns: Number of collections found, number of resources found
        """

        for file in files:
            file = os.path.relpath(file)
            try:
                before = len(self.catalog.relationships)
                _, collection = parse(file, self.catalog)
            except Exception as E:
                self.results[file] = Result(file, [Log("parse", False, details=str(E))])
                continue
            self.results[file] = Result(
                file, [
                    Log("parse", True),
                    Log(
                        "relationships", True,
                        details="+ {0} element(s)".format(len(self.catalog.relationships) - before)
                    ),
                    Log(
                        "children", True,
                        details="{0} child(ren)".format(len([
                            pair
                            for pair in self.catalog.relationships
                            if collection.identifier in pair
                        ]))
                    ),
                    self.run_catalog_schema(file)
                ]
            )
        for collection in self.catalog.objects.values():
            if collection._metadata_filepath:
                file = os.path.relpath(collection._metadata_filepath)
                if file in self.results:
                    continue
                self.results[file] = Result(
                    file, [self.run_catalog_schema(file)]
                )
        return len(self.catalog.objects), len([o for o in self.catalog.objects.values() if o.resource])

    def tests(self, pbar: Optional[tqdm.tqdm] = None) -> Dict[str, bool]:
        resources = [o for o in self.catalog.objects.values() if o.resource]
        passing: Dict[str, bool] = {}
        for r in resources:
            passing[r.filepath] = True
            try:
                doc = Document(r.filepath)
            except Exception as E:
                self.results[r.filepath] = Result(
                    r.filepath,
                    [Log("parse", False, details=f"Exception at parsing time: {E}")]
                )
                passing[r.filepath] = False
                continue

            self.results[r.filepath] = Result(
                r.filepath,
                [
                    Log("parse", True),
                    Log("parse(refsDecl/@n)", True, details=f"Tree(s) found: {len(doc.citeStructure)}")
                ]
            )
            working_tree = {}
            for tree in doc.citeStructure:
                s, details = check_naming_type(doc.citeStructure[tree].structure)
                self.results[r.filepath].statuses.append(
                    Log("citeStructure/@unit", s, details=f"citeType must be matching the regex ^\\w+$. Problematic names: {', '.join(details)}" if not s else None)
                )
                working_tree[tree] = s
                passing[r.filepath] = s
            reffs = {}
            try:
            # Now check the reference / structure
                reffs = {tree: doc.get_reffs(tree) for tree in doc.citeStructure}
                self.results[r.filepath].statuses.append(
                    Log(
                        "parse(citeStructures)",
                        True,
                        details="\n".join([
                            f"Tree:{tree}->{_stringify_tree_count(_count_tree(reffs[tree]))}"
                            for tree in reffs
                        ])
                    )
                )
            except:
                self.results[r.filepath].statuses.append(
                    Log(
                        "citeStructures",
                        False,
                        details="Unable to get reffs from citeStructure"
                    )
                )
                passing[r.filepath] = False
            if reffs:
                bad_refs = {}
                double_refs = {}
                for tree in reffs:
                    if not working_tree[tree]:
                        continue
                    bad_refs[tree] = {}
                    double_refs[tree] = {}
                    for xpath, *values in _check_refs(doc, doc.citeStructure[tree].structure):
                        if xpath not in bad_refs:
                            bad_refs[tree][xpath] = []
                        bad_refs[tree][xpath].append(values)

                    for xpath, value, count in _check_dbl_refs(doc, tree):
                        double_refs[tree][xpath] = (value, count)

                    self.results[r.filepath].statuses.append(Log(
                        f"forbiddenRefs[Tree={tree}]",
                        len(bad_refs[tree]) == 0,
                        details="" if len(bad_refs[tree]) == 0 else (
                                "Reference(s) contain[s] a delimiter, which will break parsing: " + "; ".join([
                                    f"At xpath `{xpath}`: " + ", ".join([
                                        f"`{ref}` (Delim: `{delim}`)"
                                        for ref, delim in bad_refs[tree][xpath]
                                    ]) for xpath in bad_refs[tree]
                                ])
                        )
                    ))
                    if not self.results[r.filepath].statuses[-1].status:
                        passing[r.filepath] = False

                    self.results[r.filepath].statuses.append(Log(
                        f"duplicateRefs[Tree={tree}]",
                        len(double_refs[tree]) == 0,
                        details="" if len(double_refs[tree]) == 0 else (
                                "Reference(s) at following XPath(s) are found more than once: " + "; ".join([
                                    f"Reference {ref} (×{count}, xPath: `{xpath}`): " for xpath, (ref, count) in double_refs[tree].items()
                                ])
                        )
                    ))
                    if not self.results[r.filepath].statuses[-1].status:
                        passing[r.filepath] = False
            if pbar is not None:
                pbar.update(1)
        return passing


