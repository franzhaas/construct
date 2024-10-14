import os
import ast
import itertools
import copy


_counter = itertools.count()

source = """
def a(d=9, e=11):
    e = e + 1
    return d + e

def outer(c, b):
    d = a(e=2, d=1)
    for x in range(4):
        d = a(b, d)
    d = a()
    d = 2+3+d
    a()
    return d * 2 + 1

result = outer(c=3, b=4)
print(result)
"""

tree = ast.parse(source)
exec(ast.unparse(tree))

class _VariablePrefixer(ast.NodeTransformer):
    def __init__(self, prefix):
        self.prefix = prefix

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Load, ast.Del)):
            node.id = self.prefix + node.id
        return self.generic_visit(node)

def _add_prefix_to_variables(function_ast, prefix):
    for arg in function_ast.args.args:
        arg.arg = prefix + arg.arg
    prefixer = _VariablePrefixer(prefix)
    new_function_ast = prefixer.visit(function_ast)
    ast.fix_missing_locations(new_function_ast)
    return new_function_ast


def inline_functionInFunction(ast2workOn, origToInline):
    fName = origToInline.name
    for item in ast2workOn.body:
        cols = ' '*item.col_offset
        if (isinstance(item, ast.Assign) or isinstance(item, ast.Expr)) and isinstance(item.value, ast.Call) and item.value.func.id == fName:
            prefix = f"__inlining_stage_{next(_counter)}_"
            toInline = _add_prefix_to_variables(copy.deepcopy(origToInline), prefix)
            withoutDefault = len(toInline.args.args) - len(toInline.args.defaults)
            try:
                targets  = ", ".join(ast.unparse(item) for item in item.targets) + " = "
            except AttributeError:
                targets = ""
            args = copy.deepcopy(item.value.args + toInline.args.defaults[len(item.value.args)-withoutDefault:])
            for arg in item.value.keywords:
                arg.arg = prefix + arg.arg
            yield f"{cols}({', '.join(ast.unparse(item) for item in toInline.args.args)}) = ({', '.join(ast.unparse(item) for item in args)})"
            if item.value.keywords:
                yield f"{cols}{'; '.join(ast.unparse(item) for item in item.value.keywords)}"
            yield from (f"{cols}{ast.unparse(innerItem)}" for innerItem in toInline.body[:-1])
            yield f"{cols}{targets}{ast.unparse(toInline.body[-1].value)}"
        elif hasattr(item, "body"):
            yield f"{cols}{ast.unparse(item).split(os.linesep)[0]}"
            yield from inline_functionInFunction(copy.deepcopy(item), copy.deepcopy(origToInline))
        else:
            yield f"{cols}{ast.unparse(item)}"


def inline_functionInOtherFunctions(source, function_name):
    tree = ast.parse(source)
    toInline = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == function_name]
    assert len(toInline) == 1, f"Function {function_name} not found once but {len(toInline)} times"
    toInline = toInline[0]

    for item in tree.body:
        if isinstance(item, ast.FunctionDef) and item.name != function_name:
            yield f"{' '*item.col_offset}def {item.name}({ast.unparse(item.args)}):"
            yield from inline_functionInFunction(item, toInline)
        else:
            yield ast.unparse(item)


a = (list(inline_functionInOtherFunctions(source, "a")))

print("\n".join(a))
