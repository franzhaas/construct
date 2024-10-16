import os
import ast
import itertools
import copy


_counter = itertools.count()


class _VariablePrefixer(ast.NodeTransformer):
    def __init__(self, prefix):
        self.prefix = prefix


    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Load, ast.Del)):
            node.id = f"{self.prefix}{node.id}"
        return self.generic_visit(node)


def _add_prefix_to_variables(function_ast, prefix):
    prefixer = _VariablePrefixer(prefix)
    new_function_ast = prefixer.visit(function_ast)
    ast.fix_missing_locations(new_function_ast)
    return new_function_ast


def inline_functionInFunction(ast2workOn, origToInline):
    fName = origToInline.name
    argNameList = origToInline.args.args
    for item in ast2workOn:
        cols = ' '*item.col_offset
        try:
            if (isinstance(item, ast.Assign) or isinstance(item, ast.Expr)) and isinstance(item.value, ast.Call) and hasattr(item.value.func, "id") and item.value.func.id == fName:
                prefix = f"__inlining_stage_{next(_counter)}_"
                toInline = _add_prefix_to_variables(copy.deepcopy(origToInline), prefix)
                targets  = ast.unparse(item).replace(ast.unparse(item.value), "") if isinstance(item, ast.Assign) else ""
                argsDict = ({f"{prefix}{item.arg}": value for item, value in zip(argNameList, toInline.args.defaults)}|
                            {f"{prefix}{item.arg}": item.value for item in item.value.keywords}|
                            {f"{prefix}{item.arg}": value for item, value in zip(toInline.args.args, item.value.args)})
                orderedNames = [f"{prefix}{name.arg}" for name in argNameList]
                yield f"{cols}({', '.join(orderedNames)}) = ({', '.join(ast.unparse(argsDict[name]) for name in orderedNames)})"
                yield from (f"{cols}{ast.unparse(innerItem)}" for innerItem in toInline.body[:-1])
                yield f"{cols}{targets}{ast.unparse(toInline.body[-1].value)}"
            elif isinstance(item, ast.Try):
                yield f"{cols}try:"
                yield from inline_functionInFunction(item.body, copy.deepcopy(origToInline))
                for handler in item.handlers:
                    yield f"{cols}{ast.unparse(handler).split(os.linesep)[0]}"
                    yield from inline_functionInFunction(handler.body, copy.deepcopy(origToInline))
                if item.finalbody:
                    yield f"{cols}finally:"
                    yield from inline_functionInFunction(item.finalbody, copy.deepcopy(origToInline))
            elif hasattr(item, "body"):
                yield f"{cols}{ast.unparse(item).split(os.linesep)[0]}"
                yield from inline_functionInFunction(copy.deepcopy(item.body), copy.deepcopy(origToInline))
            else:
                yield f"{cols}{ast.unparse(item)}"
        except AttributeError as e:
            yield f"{cols}{ast.unparse(item)}"


def inline_functionInOtherFunctions(source, function_name):
    tree = ast.parse(source)
    toInline = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == function_name]
    assert len(toInline) == 1, f"Function {function_name} not found once but {len(toInline)} times"
    toInline = toInline[0]

    for item in tree.body:
        if isinstance(item, ast.FunctionDef) and item.name != function_name:
            yield f"{' '*item.col_offset}def {item.name}({ast.unparse(item.args)}):"
            yield from inline_functionInFunction(item.body, toInline)
        else:
            yield ast.unparse(item)

def inlineAllFunctions(source):
    for _ in range(10):
        fNames = set(item.name for item in ast.parse(source).body 
                               if isinstance(item, ast.FunctionDef))

        for functionName in fNames:
            print(functionName)
            source = os.linesep.join(inline_functionInOtherFunctions(source, functionName))
    return source
