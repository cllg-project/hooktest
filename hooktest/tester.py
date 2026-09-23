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
    # A self-closing unit (<pb/>, <milestone/>) owns the following siblings up to the next one:
    # dapytains needs its match to bound them, otherwise it looks for children inside the empty element.
    milestone_match = structure.match if structure.milestone else None
    if len(structure.children) == 1:
        for element in xpath_eval(xpath_processor, child_xpath):
            self.find_refs(
                root=element,
                structure=structure.children[0],
                unit=unit,
                level=level,
                milestone_match=milestone_match
            )
    else:
        for element in xpath_eval(xpath_processor, child_xpath):
            self.find_refs_from_branches(
                root=element,
                structure=structure.children,
                unit=unit,
                level=level,
                milestone_match=milestone_match
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

def _parse_for_check(filepath: str, check_name: str):
    """ Parse `filepath` for one of the pre-dapytains, lxml-only structural checks.

    :returns: (tree, None) on success, (None, failing Log) when the file cannot be parsed.
    """
    try:
        return ET.parse(filepath), None
    except Exception as E:
        return None, Log(
            check_name, False,
            details=f"Unable to parse XML to check {check_name} ({type(E).__name__}: {E})"
        )


def _direct_cite_structures(parent) -> List:
    """ Direct citeStructure children of `parent`, skipping comments and processing
    instructions (which have no QName and would crash ET.QName). """
    return [
        child for child in parent
        if isinstance(child.tag, str) and ET.QName(child).localname == "citeStructure"
    ]


def check_citestructure_delims(filepath: str) -> Log:
    """ dapytains requires every citeStructure nested within another citeStructure
    to carry a @delim attribute (the top-level citeStructure(s) of a refsDecl do not,
    since there is no parent reference to prepend a delimiter to). A missing @delim
    on a non-top citeStructure crashes dapytains' regex building with a cryptic
    TypeError, so we check for it upfront with a clear message.
    """
    tree, error = _parse_for_check(filepath, "citeStructure/@delim")
    if tree is None:
        return error

    missing = []
    # iter() on a tag pattern skips comments and processing instructions, which have no
    # QName and used to crash this check.
    for el in tree.iter("{*}citeStructure"):
        parent = el.getparent()
        if parent is not None and ET.QName(parent).localname == "citeStructure" and not el.get("delim"):
            missing.append(el.get("unit") or "?")

    status = len(missing) == 0
    return Log(
        "citeStructure/@delim",
        status,
        details=(
            "Non-top citeStructure(s) are missing the required @delim attribute "
            f"(unit(s): {', '.join(missing)})"
        ) if not status else None
    )

def check_ignored_citestructure(filepath: str) -> Log:
    """ dapytains only ever reads the *first* citeStructure of a refsDecl
    (`./citeStructure[1]`, in CiteStructureParser.__init__) and then recurses through that
    element's own `./citeStructure` children. Any further citeStructure sitting directly
    under the refsDecl is silently dropped: no XPath is ever built or evaluated for it, so
    nothing raises. The citation tree is simply one level shallower than the file looks.

    The usual cause is a self-closed parent -- `<citeStructure ... />` followed by an
    indented sibling that was meant to be nested inside it. The failure is quiet and easy to
    miss: top-level references keep resolving, while deeper ones return nothing, because the
    one surviving unit's regex (`.+`) then swallows the delimiters as part of its own value.
    """
    tree, error = _parse_for_check(filepath, "citeStructure/ignored")
    if tree is None:
        return error

    ignored = [
        (extra.get("unit") or "?", extra.get("match") or "?", extra.sourceline)
        for refs_decl in tree.iter("{*}refsDecl")
        for extra in _direct_cite_structures(refs_decl)[1:]
    ]

    status = len(ignored) == 0
    return Log(
        "citeStructure/ignored",
        status,
        details=(
            "citeStructure(s) never read by dapytains, which only uses the first "
            "citeStructure of each refsDecl: "
            + "; ".join([
                f"unit `{unit}` (match `{match}`, line {line})"
                for unit, match, line in ignored
            ])
            + ". Nest it inside the preceding citeStructure (which is probably self-closed) "
              "or move it to its own refsDecl."
        ) if not status else None
    )


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


def _join_xpath(base: str, xpath: str) -> str:
    """ Join a parent match with a child match. A child match can be written ".//x" or "//x",
    both of which must end up as a single descendant step. """
    if not base:
        return xpath
    return f"{base}/{xpath}".replace("///", "//")


def _absolute_path(document: Document, node) -> str:
    """ Positional XPath of a concrete node (e.g. /TEI[1]/text[1]/body[1]/div[8]), so a report
    points at one element rather than at the whole match. """
    return str(get_xpath_proc(node, processor=document.xml_processor).evaluate_single(
        "string-join(for $n in (ancestor-or-self::*) "
        "return concat('/', name($n), '[', 1 + count($n/preceding-sibling::*[name() = name($n)]), ']'), '')"
    ))


def _bare_match(structure: CitableStructure) -> str:
    """ The element match of a citeStructure, without the `[@n]`-style predicate dapytains
    appends to it: that predicate hides the very elements we want to report, the ones missing
    their @use attribute altogether. """
    match = getattr(structure, "match", "")
    if match:
        return match
    suffix = f"[{structure.use}]"
    if structure.xpath_match.endswith(suffix):
        return structure.xpath_match[:-len(suffix)]
    return structure.xpath_match


def _check_empty_values(
        document: Document,
        structure: CitableStructure,
        base_xpath: str = ""
) -> List[Tuple[str, str, str]]:
    """ Find matched nodes carrying an empty citeStructure/@use value (typically a
    `<div n="">` left behind by a conversion). Such a node yields an empty reference, which
    dapytains cannot cite, cannot resolve, and which takes down every unit nested under it.

    A node missing the attribute entirely is not reported: dapytains matches on `[@use]`, so
    it is simply not part of the citation tree (a bare `<lb/>` inside a heading, say), which
    is legitimate encoding rather than broken data.

    :returns: List of (citeType, positional xpath of the node, human readable reason)
    """
    xproc = get_xpath_proc(document.xml, processor=document.xml_processor)
    returns: List[Tuple[str, str, str]] = []

    match = _join_xpath(base_xpath, _bare_match(structure))

    if structure.use != "position()":
        for node in xpath_eval(xproc, match):
            local = get_xpath_proc(node, processor=document.xml_processor)
            if str(local.evaluate_single(f"string({structure.use})")):
                continue
            # Matched on the bare match, so that nodes nested under an uncited one are still
            # inspected; only an attribute that is present and empty is a defect.
            if local.effective_boolean_value(f"boolean({structure.use})"):
                returns.append((
                    structure.citeType,
                    _absolute_path(document, node),
                    f"`{structure.use}` is empty"
                ))

    for child in structure.children:
        # Children of a milestone (e.g. <lb/> under <cb/>) are siblings of it, not descendants:
        # scoping them under their parent's match would match nothing. `milestone` only exists
        # in newer dapytains releases, hence the getattr.
        milestone = getattr(structure, "milestone", False)
        returns.extend(_check_empty_values(document, child, "" if milestone else match))

    return returns


def _check_dbl_refs(
        document: Document,
        tree: str
) -> List[Tuple[str, str, int]]:
    """ Duplicates are counted within each list of siblings (the children of one unit), not over the
    flattened tree: a duplicated parent carries the same children list once per match, which would
    over-report them. The children of a self-closing unit (<lb/> under <pb/>) have no XPath of their
    own that matches them all (dapytains returns the position of the first one), so the sibling count
    is kept when the XPath finds fewer nodes. """
    returns: Dict[str, Tuple[str, str, int]] = {}

    def walk(units: List[CitableUnit]):
        for reference, sibling_count in Counter(u.ref for u in units).items():
            if sibling_count > 1 and reference not in returns:
                xpath = document.citeStructure[tree].generate_xpath(reference)
                count = max(sibling_count, len(list(xpath_eval(document.xpath_processor, xpath))))
                returns[reference] = (xpath, f"`{reference}`", count)
        for u in units:
            walk(u.children)

    walk(document.get_reffs(tree))
    return list(returns.values())


class Tester:
    """ Tester class, allows for retrieving results outside of the CLI
    """
    def __init__(self, resource_schema: Optional[str] = None):
        self.catalog = Catalog()
        self.results: Dict[str, Result] = {}

        # Load the Relax NG schema
        self.catalog_schema = ET.RelaxNG(
            ET.parse(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "collection-schema.rng")
            )
        )

        # Optional user-supplied schema to validate TEI resource files against
        self.resource_schema: Optional[ET.RelaxNG] = (
            ET.RelaxNG(ET.parse(resource_schema)) if resource_schema else None
        )

    def _validate_against_schema(self, schema: ET.RelaxNG, filepath: str) -> Log:
        try:
            status = schema.validate(ET.parse(filepath))
            details = []
            if not status:
                for el in schema.error_log:
                    details.append(":".join(str(el).split("\n")[0].split(":")[6:]).strip())
            return Log("schema", status, details="; ".join(details))
        except Exception as E:
            return Log(
                "schema", False,
                details=f"Unable to validate against schema ({type(E).__name__}: {E}); check the file is well-formed XML"
            )

    def run_catalog_schema(self, filepath) -> Log:
        return self._validate_against_schema(self.catalog_schema, filepath)

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
                self.results[file] = Result(
                    file, [Log(
                        "parse", False,
                        details=f"Unable to parse catalog file ({type(E).__name__}: {E}); "
                                f"check it is well-formed XML following the Dapytains catalog structure"
                    )]
                )
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
            delim_log = check_citestructure_delims(r.filepath)
            ignored_log = check_ignored_citestructure(r.filepath)
            self.results[r.filepath] = Result(r.filepath, [delim_log, ignored_log])
            if not delim_log.status or not ignored_log.status:
                passing[r.filepath] = False

            if self.resource_schema is not None:
                schema_log = self._validate_against_schema(self.resource_schema, r.filepath)
                self.results[r.filepath].statuses.append(schema_log)
                if not schema_log.status:
                    passing[r.filepath] = False

            try:
                doc = Document(r.filepath)
            except Exception as E:
                self.results[r.filepath].statuses.append(
                    Log(
                        "parse", False,
                        details=f"Unable to build a document/citeStructure model from this file "
                                f"({type(E).__name__}: {E}); check the TEI is well-formed XML with a "
                                f"valid refsDecl/citeStructure"
                    )
                )
                passing[r.filepath] = False
                continue

            self.results[r.filepath].statuses.extend([
                Log("parse", True),
                Log("parse(refsDecl/@n)", True, details=f"Tree(s) found: {len(doc.citeStructure)}")
            ])
            try:
                working_tree = {}
                for tree in doc.citeStructure:
                    s, details = check_naming_type(doc.citeStructure[tree].structure)
                    self.results[r.filepath].statuses.append(
                        Log("citeStructure/@unit", s, details=f"citeType must be matching the regex ^\\w+$. Problematic names: {', '.join(details)}" if not s else None)
                    )
                    working_tree[tree] = s
                    passing[r.filepath] = passing[r.filepath] and s

                # Checked before get_reffs(): an empty value raises there, with no way to tell
                # which element is at fault.
                empty_trees = set()
                for tree in doc.citeStructure:
                    empty_values = _check_empty_values(doc, doc.citeStructure[tree].structure)
                    self.results[r.filepath].statuses.append(Log(
                        f"emptyRefs[Tree={tree}]",
                        len(empty_values) == 0,
                        details="" if len(empty_values) == 0 else (
                            "Matched node(s) cannot be cited, their @use value is unusable: "
                            + "; ".join([
                                f"{citeType} at `{path}` ({reason})"
                                for citeType, path, reason in empty_values
                            ])
                        )
                    ))
                    if not self.results[r.filepath].statuses[-1].status:
                        passing[r.filepath] = False
                        working_tree[tree] = False
                        empty_trees.add(tree)

                reffs = {}
                try:
                # Now check the reference / structure
                    # A tree with an empty value cannot be walked (get_reffs raises on it):
                    # it is already reported above, no need for a second, vaguer failure.
                    reffs = {
                        tree: doc.get_reffs(tree)
                        for tree in doc.citeStructure
                        if tree not in empty_trees
                    }
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
                except Exception:
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
            except Exception as E:
                self.results[r.filepath].statuses.append(
                    Log(
                        "error", False,
                        details=f"Unexpected error while checking citeStructure references "
                                f"({type(E).__name__}: {E})"
                    )
                )
                passing[r.filepath] = False
            if pbar is not None:
                pbar.update(1)
        return passing


