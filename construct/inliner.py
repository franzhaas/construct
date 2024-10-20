import ast
import itertools
import copy
import construct
import construct.lib
import struct
import collections
import builtins
import logging
import traceback
from dataclasses import dataclass

_linesep = "\n"
_counter = itertools.count()

class _isInlineableResearcher(ast.NodeVisitor):
    def __init__(self):
        self.lambdas = 0
        self.returns = 0
        self.nestedFunctions = 0

    def generic_visit(self, node):
        if isinstance(node, ast.Lambda):
            self.lambdas += 1
        if isinstance(node, ast.Return):
            self.returns += 1
        if isinstance(node, ast.FunctionDef):
            self.nestedFunctions += 1
        super().generic_visit(node)


def _isInlineableInfo(code):
    tree = ast.parse(code)
    finder = _isInlineableResearcher()
    finder.visit(tree)
    return finder


class _VariablePrefixer(ast.NodeTransformer):
    def __init__(self, prefix, excludes):
        self.prefix = prefix
        self._exclueds = excludes

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Load, ast.Del)) and node.id not in self._exclueds and not node.id.startswith("BytesIO") and not node.id.startswith("collections") and not node.id.startswith("itertools"):
            node.id = f"{self.prefix}{node.id}"
        return self.generic_visit(node)

    def visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
        return super().visit(node)


def _add_prefix_to_variables(function_ast, excludes, prefix):
    new_function_ast = _VariablePrefixer(prefix, excludes).visit(function_ast)
    ast.fix_missing_locations(new_function_ast)
    return new_function_ast


def _inline_functionInFunction(ast2workOn, excludes, inlineAble):
    for item in ast2workOn:
        cols = ' '*item.col_offset
        try:
            if (isinstance(item, ast.Assign) or isinstance(item, ast.Expr) or isinstance(item, ast.Return)) and isinstance(item.value, ast.Call) and hasattr(item.value.func, "id") and item.value.func.id in inlineAble:
                fName = item.value.func.id
                function2workOn = copy.deepcopy(inlineAble[fName].item)
                if isinstance(item, ast.Return) or inlineAble[fName].returns == 0 or (inlineAble[fName].returns == 1 and isinstance(function2workOn.body[-1], ast.Return)):
                    prefix = f"__inlining_stage_{next(_counter)}_"
                    toInline = _add_prefix_to_variables(function2workOn, excludes,  prefix)
                    if isinstance(item, ast.Assign):
                        targets  = ast.unparse(item).replace(ast.unparse(item.value), "")
                    elif isinstance(item, ast.Expr):
                        targets = ""
                    elif isinstance(item, ast.Return):
                        targets = "return "
                    else:
                        raise ValueError(f"Unexpected item {item}")
                    argNameList = function2workOn.args.args
                    argsDict = ({f"{prefix}{item.arg}": value for item, value in zip(argNameList, toInline.args.defaults)}|
                                {f"{prefix}{item.arg}": item.value for item in item.value.keywords}|
                                {f"{prefix}{item.arg}": value for item, value in zip(toInline.args.args, item.value.args)})
                    orderedNames = [f"{prefix}{name.arg}" for name in argNameList]
                    yield f"{cols}({', '.join(orderedNames)}) = ({', '.join(ast.unparse(argsDict[name]) for name in orderedNames)})"
                    for element in toInline.body[:-1]:
                        yield from (f"{cols}{line}" for line in ast.unparse(element).split(_linesep)) 
                    lastEntry = toInline.body[-1]
                    if isinstance(lastEntry, ast.Return):
                        yield f"{cols}{targets}{ast.unparse(lastEntry.value)}"
                    else:
                        yield from (f"{cols}{item}" for item in ast.unparse(lastEntry).split(_linesep))
                        if targets:
                            yield f"{cols}{targets}None"
                else:
                    yield f"{cols}{ast.unparse(item)}"
            elif isinstance(item, ast.Try):
                yield f"{cols}try:"
                yield from (f"{cols}{item}" for item in _inline_functionInFunction(item.body, excludes, inlineAble))
                for handler in item.handlers:
                    yield f"{cols}{ast.unparse(handler).split(_linesep)[0]}"
                    yield from (f"{cols}{innerItem}" for innerItem in _inline_functionInFunction(handler.body, excludes, inlineAble))
                if item.finalbody:
                    yield f"{cols}finally:"
                    yield from (f"{cols}{item}" for item in _inline_functionInFunction(item.finalbody, excludes, inlineAble))
            elif hasattr(item, "body"):
                yield f"{cols}{ast.unparse(item).split(_linesep)[0]}"
                yield from (f"{cols}{item}" for item in _inline_functionInFunction((item.body), excludes, inlineAble))
            else:
                yield f"{cols}{ast.unparse(item)}"
        except AttributeError as e:
            logging.error(f"inlining {item} failed due to {e} traceback: {traceback.format_exc()} attempting to continue")
            yield f"{cols}{ast.unparse(item)}"


def _inline_functionInOtherFunctions(tree, inlineAbles):
    ast.fix_missing_locations(tree)
    excludes = set(itertools.chain(dir(construct),  dir(construct.lib), ("struct."+ item for item in dir(struct)),
                   ("collections."+ item for item in dir(collections)), ("itertools."+ item for item in dir(itertools)), 
                   dir(builtins), itertools.chain.from_iterable([ast.unparse(item) for item in target.targets] for target in tree.body if isinstance(target, ast.Assign)),
                   (item.name for item in tree.body if isinstance(item, ast.FunctionDef)))) ^ set(["this", "list_"])
    for item in tree.body:
        if isinstance(item, ast.FunctionDef):
            yield f"{' '*item.col_offset}def {item.name}({ast.unparse(item.args)}):"
            yield from _inline_functionInFunction(item.body, excludes, inlineAbles)
        else:
            yield f"{' '*item.col_offset}{ast.unparse(item)}"


@dataclass
class _inlineableInfo:
    returns: int
    name: str
    item: ast.FunctionDef


def inlineAllFunctions(source):
    for _ in range(2): # on first pass we inline functions, which have not yet been inlined, second time closes the gap.
        tree = ast.parse(source)
        inlineAble = ((item.name, item, _isInlineableInfo(item)) for item in tree.body if isinstance(item, ast.FunctionDef))
        inlineAble = [_inlineableInfo(returns=info.returns, name=name, item=item) for name, item, info in inlineAble if (info.lambdas == 0 and info.nestedFunctions)]
        counted = collections.Counter(item.name for item in inlineAble)
        inlineAble = {item.name: item for item in inlineAble if counted[item.name] == 1}
        if inlineAble and max(counted.values())==1 and min(counted.values()) == 1:
            source = ast.unparse(ast.parse(_linesep.join(_inline_functionInOtherFunctions(tree, inlineAble)))) # format code...
    return source
