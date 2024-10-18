import os
import ast
import itertools
import copy
import construct
import construct.lib
import struct
import collections
import itertools
import builtins

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
        # Set the parent attribute for each node
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
    def __init__(self, prefix, ast2WorkWith):
        self.prefix = prefix
        self._exclueds = dir(construct) + dir(construct.lib) + ["struct."+ item for item in dir(struct)] + ["collections."+ item for item in dir(collections)] + ["itertools."+ item for item in dir(itertools)] + dir(builtins)
        self._exclueds += list(itertools.chain.from_iterable([ast.unparse(item) for item in target.targets] for target in ast2WorkWith.body if isinstance(target, ast.Assign)))
        self._exclueds += [item.name for item in ast2WorkWith.body if isinstance(item, ast.FunctionDef)]


    def visit_Name(self, node):

        if isinstance(node.ctx, (ast.Store, ast.Load, ast.Del)) and node.id not in self._exclueds and not node.id.startswith("BytesIO") and not node.id.startswith("collections") and not node.id.startswith("itertools"):
            print(node.id)
            node.id = f"{self.prefix}{node.id}"
        return self.generic_visit(node)


    def visit(self, node):
        # Set the parent attribute for each node
        for child in ast.iter_child_nodes(node):
            child.parent = node
        return super().visit(node)


def _add_prefix_to_variables(function_ast, module_ast, prefix):
    prefixer = _VariablePrefixer(prefix, module_ast)
    new_function_ast = prefixer.visit(function_ast)
    ast.fix_missing_locations(new_function_ast)
    return new_function_ast


def inline_functionInFunction(ast2workOn, module_ast, origToInline):
    fName = origToInline.name
    argNameList = origToInline.args.args
    for item in ast2workOn:
        cols = ' '*item.col_offset
        try:
            if (isinstance(item, ast.Assign) or isinstance(item, ast.Expr) or isinstance(item, ast.Return)) and isinstance(item.value, ast.Call) and hasattr(item.value.func, "id") and item.value.func.id == fName:
                prefix = f"__inlining_stage_{next(_counter)}_"
                toInline = _add_prefix_to_variables(copy.deepcopy(origToInline), module_ast,  prefix)
                if isinstance(item, ast.Assign):
                    targets  = ast.unparse(item).replace(ast.unparse(item.value), "")
                elif isinstance(item, ast.Expr):
                    targets = ""
                elif isinstance(item, ast.Return):
                    targets = "return "
                else:
                    raise ValueError(f"Unexpected item {item}")
                argsDict = ({f"{prefix}{item.arg}": value for item, value in zip(argNameList, toInline.args.defaults)}|
                            {f"{prefix}{item.arg}": item.value for item in item.value.keywords}|
                            {f"{prefix}{item.arg}": value for item, value in zip(toInline.args.args, item.value.args)})
                orderedNames = [f"{prefix}{name.arg}" for name in argNameList]
                yield "#123#"
                yield f"{cols}({', '.join(orderedNames)}) = ({', '.join(ast.unparse(argsDict[name]) for name in orderedNames)})"
                for element in toInline.body[:-1]:
                    for line in ast.unparse(element).split(os.linesep):
                        yield f"{cols}{line}"
                try:
                    lastEntry = toInline.body[-1]
                    if isinstance(lastEntry, ast.Return):
                        yield f"{cols}{targets}{ast.unparse(lastEntry.value)}"
                    else:
                        yield from (f"{cols}{item}" for item in ast.unparse(lastEntry).split(os.linesep))
                        if targets:
                            yield f"{cols}{targets}None"
                except Exception as e:
                    print("returning if expression", ast.unparse(toInline.body[-1]))
                    raise
                yield "#456#"
            elif isinstance(item, ast.Try):
                #print(f"1|{cols}|")
                yield f"{cols}try: # 123"
                yield from (f"{cols}{item}" for item in inline_functionInFunction(item.body, module_ast, copy.deepcopy(origToInline)))
                for handler in item.handlers:
                    #print(f"2|{cols}|")
                    yield f"{cols}{ast.unparse(handler).split(os.linesep)[0]} # 456"
                    #print(f"3|{cols}|")
                    yield from (f"{cols}{innerItem}" for innerItem in inline_functionInFunction(handler.body, module_ast, copy.deepcopy(origToInline)))
                    #print(f"4|{cols}|")
                if item.finalbody:
                    #print(f"5|{cols}|")
                    yield f"{cols}finally:"
                    yield from (f"{cols}{item}" for item in inline_functionInFunction(item.finalbody, module_ast, copy.deepcopy(origToInline)))
            elif hasattr(item, "body"):
                yield f"{cols}{ast.unparse(item).split(os.linesep)[0]}"
                yield from (f"{cols}{item}" for item in inline_functionInFunction(copy.deepcopy(item.body), module_ast, copy.deepcopy(origToInline)))
            else:
                yield f"{cols}{ast.unparse(item)}"
        except AttributeError as e:
            import traceback 
            traceback.print_exc()
            print(item, e, 1234)
            yield f"{cols}{ast.unparse(item)}"


def inline_functionInOtherFunctions(source, function_name):
    tree = ast.parse(source)
    ast.fix_missing_locations(tree)
    toInline = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == function_name]
    assert len(toInline) == 1, f"Function {function_name} not found once but {len(toInline)} times"
    toInline = toInline[0]

    for item in tree.body:
        if isinstance(item, ast.FunctionDef) and item.name != function_name:
            yield f"{' '*item.col_offset}def {item.name}({ast.unparse(item.args)}):"
            yield from inline_functionInFunction(item.body, tree, toInline)
        else:
            yield f"{' '*item.col_offset}{ast.unparse(item)}"

def inlineAllFunctions(source):
    with open("temp.py", "wt") as f:
        f.write(source)
    for run in range(2):
        fNames = set(item.name for item in ast.parse(source).body 
                               if (isinstance(item, ast.FunctionDef) and 
                                   no_nested_lambdas_or_functions(item)))

        for nr, functionName in enumerate(fNames):
            sourceIn = source
            source = os.linesep.join(inline_functionInOtherFunctions(source, functionName))
            with open(f"temp{run}_{nr}_{functionName}.py", "wt") as f:
                f.write(source)
            if sourceIn == source:
                break

    return source
