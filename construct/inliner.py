import os
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

_counter = itertools.count()
keep = None

class NoLambdasNoNestedFunctionsVisitor(ast.NodeVisitor):
    def __init__(self):
        self.has_lambda = False
        self.has_nested_function = False

    def visit_Lambda(self, node):
        self.has_lambda = True

    def visit_FunctionDef(self, node):
        # Skip the top-level function definition
        if hasattr(node, 'parent') and isinstance(node.parent, ast.FunctionDef):
            self.has_nested_function = True
        self.generic_visit(node)

    def visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
        super().visit(node)

    def no_nested_lambdas_or_functions(self):
        return not self.has_lambda and not self.has_nested_function

def no_nested_lambdas_or_functions(item):
    info = NoLambdasNoNestedFunctionsVisitor()
    info.visit(item)
    return info.no_nested_lambdas_or_functions()

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
    prefixer = _VariablePrefixer(prefix, excludes)
    new_function_ast = prefixer.visit(function_ast)
    ast.fix_missing_locations(new_function_ast)
    return new_function_ast


def inline_functionInFunction(ast2workOn, excludes, inlineAble):
    for item in ast2workOn:
        cols = ' '*item.col_offset
        try:
            if (isinstance(item, ast.Assign) or isinstance(item, ast.Expr) or isinstance(item, ast.Return)) and isinstance(item.value, ast.Call) and hasattr(item.value.func, "id") and item.value.func.id in inlineAble:
                fName = item.value.func.id
                prefix = f"__inlining_stage_{next(_counter)}_"
                toInline = _add_prefix_to_variables(copy.deepcopy(inlineAble[fName]), excludes,  prefix)
                if isinstance(item, ast.Assign):
                    targets  = ast.unparse(item).replace(ast.unparse(item.value), "")
                elif isinstance(item, ast.Expr):
                    targets = ""
                elif isinstance(item, ast.Return):
                    targets = "return "
                else:
                    raise ValueError(f"Unexpected item {item}")
                argNameList = inlineAble[fName].args.args
                argsDict = ({f"{prefix}{item.arg}": value for item, value in zip(argNameList, toInline.args.defaults)}|
                            {f"{prefix}{item.arg}": item.value for item in item.value.keywords}|
                            {f"{prefix}{item.arg}": value for item, value in zip(toInline.args.args, item.value.args)})
                orderedNames = [f"{prefix}{name.arg}" for name in argNameList]
                yield f"{cols}({', '.join(orderedNames)}) = ({', '.join(ast.unparse(argsDict[name]) for name in orderedNames)})"
                for element in toInline.body[:-1]:
                    yield from (f"{cols}{line}" for line in ast.unparse(element).split(os.linesep)) 
                lastEntry = toInline.body[-1]
                if isinstance(lastEntry, ast.Return):
                    yield f"{cols}{targets}{ast.unparse(lastEntry.value)}"
                else:
                    yield from (f"{cols}{item}" for item in ast.unparse(lastEntry).split(os.linesep))
                    if targets:
                        yield f"{cols}{targets}None"
            elif isinstance(item, ast.Try):
                yield f"{cols}try:"
                yield from (f"{cols}{item}" for item in inline_functionInFunction(item.body, excludes, (inlineAble)))
                for handler in item.handlers:
                    yield f"{cols}{ast.unparse(handler).split(os.linesep)[0]}"
                    yield from (f"{cols}{innerItem}" for innerItem in inline_functionInFunction(handler.body, excludes, (inlineAble)))
                if item.finalbody:
                    yield f"{cols}finally:"
                    yield from (f"{cols}{item}" for item in inline_functionInFunction(item.finalbody, excludes, (inlineAble)))
            elif hasattr(item, "body"):
                yield f"{cols}{ast.unparse(item).split(os.linesep)[0]}"
                yield from (f"{cols}{item}" for item in inline_functionInFunction((item.body), excludes, (inlineAble)))
            else:
                yield f"{cols}{ast.unparse(item)}"
        except AttributeError as e:
            logging.error(f"inlining {item} failed due to {e} traceback: {traceback.format_exc()} attempting to continue")
            yield f"{cols}{ast.unparse(item)}"


def inline_functionInOtherFunctions(tree, inlineAbles):
    ast.fix_missing_locations(tree)
    excludes = set(dir(construct) + 
                   dir(construct.lib) + 
                   ["struct."+ item for item in dir(struct)] +
                   ["collections."+ item for item in dir(collections)] +
                   ["itertools."+ item for item in dir(itertools)] + dir(builtins) +
                   list(itertools.chain.from_iterable([ast.unparse(item) for item in target.targets] for target in tree.body if isinstance(target, ast.Assign))) + 
                   [item.name for item in tree.body if isinstance(item, ast.FunctionDef)]) ^ set(["this", "list_"])

    for item in tree.body:
        if isinstance(item, ast.FunctionDef):
            yield f"{' '*item.col_offset}def {item.name}({ast.unparse(item.args)}):"
            yield from inline_functionInFunction(item.body, excludes, inlineAbles)
        else:
            yield f"{' '*item.col_offset}{ast.unparse(item)}"

def _is_item_a_inlineable_functiondef(item):
    if isinstance(item, ast.FunctionDef):
        if not no_nested_lambdas_or_functions(item):
            logging.warning(f"{item.name}: cant be inlined due to nested function/lambda")
            return False
        code = ast.unparse(item)
        if "lambda" in code:
            logging.warning(f"{item.name}: cant be inlined due to use of the word lambda")
            return False
        if code.count("return") == 0:
            return True
        if code.count("return") > 1:
            logging.warning(f"{item.name}: cant be inlined due to multiple uses of hte word return")
        if isinstance(item.body[-1], ast.Return):
            return True
        logging.warning(f"{item.name}: cant inline as return is not the last statement of the body of the function")
        return False
    return False

def inlineAllFunctions(source):
    for _ in range(2): # on first pass we inline functions, which have not yet been inlined, second time closes the gap.
        tree = ast.parse(source)
        inlineAble = list((item.name, item) for item in tree.body if (_is_item_a_inlineable_functiondef(item)))
        counted = collections.Counter(item[0] for item in inlineAble)
        if inlineAble and max(counted.values())==1 and min(counted.values()) == 1:
            inlineAble = {name: val for name, val in inlineAble}
            source = ast.unparse(ast.parse(os.linesep.join(inline_functionInOtherFunctions(tree, inlineAble))))
    return source
