"""Interpret an agent-authored, bounded Python subset; never exec/eval code."""
import ast
import copy
import operator


class SkillError(ValueError):
    pass


class SkillStop(Exception):
    def __init__(self, reason, detail=None):
        self.reason, self.detail = reason, detail


class _Break(Exception): pass
class _Continue(Exception): pass


ALLOWED_NODES = (ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.If, ast.For, ast.While,
                 ast.Break, ast.Continue, ast.Pass, ast.Name, ast.Load, ast.Store, ast.Constant,
                 ast.List, ast.Tuple, ast.Dict, ast.Subscript, ast.Slice, ast.BinOp, ast.UnaryOp,
                 ast.BoolOp, ast.Compare, ast.Call, ast.Attribute, ast.keyword,
                 ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd,
                 ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                 ast.In, ast.NotIn, ast.Is, ast.IsNot)


def parse_code(source):
    if not isinstance(source, str) or not source.strip() or len(source) > 6000:
        raise SkillError('Code must contain 1-6000 characters.')
    try:
        tree = ast.parse(source, mode='exec')
    except SyntaxError as error:
        raise SkillError('Invalid Python syntax: ' + str(error)) from error
    nodes = list(ast.walk(tree))
    if len(nodes) > 1000 or any(not isinstance(node, ALLOWED_NODES) for node in nodes):
        raise SkillError('Unsupported syntax or code is too complex.')
    for node in nodes:
        if isinstance(node, ast.Name) and (node.id.startswith('_') or len(node.id) > 80):
            raise SkillError('Private/oversized names are not allowed.')
        if isinstance(node, ast.Attribute) and node.attr not in {'get', 'append'}:
            raise SkillError('Only dict.get and list.append are allowed.')
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in {'api','state','len','range','min','max','abs'} or
                    isinstance(node.func, ast.Attribute) and node.func.attr in {'get','append'}):
                raise SkillError('Call is not an allowed API or builtin.')
            if any(kw.arg is None for kw in node.keywords):
                raise SkillError('Unpacking calls is not allowed.')
    return tree


class Interpreter:
    def __init__(self, state, api, *, fuel=3000, max_calls=40):
        self.current = copy.deepcopy(state)
        self.api = api
        self.env = {}
        self.fuel, self.max_calls, self.calls = fuel, max_calls, 0

    def tick(self):
        self.fuel -= 1
        if self.fuel < 0:
            raise SkillStop('instruction_budget')

    @staticmethod
    def bound(value):
        if isinstance(value, (list, tuple, dict, str, range)) and len(value) > 2048:
            raise SkillError('Value exceeds size limit.')
        if type(value) is int and abs(value) > 1000000:
            raise SkillError('Integer exceeds magnitude limit.')
        return value

    def expr(self, n):
        self.tick()
        if isinstance(n, ast.Constant): return self.bound(n.value)
        if isinstance(n, ast.Name):
            if n.id not in self.env: raise SkillError('Unknown variable: ' + n.id)
            return self.env[n.id]
        if isinstance(n, (ast.List, ast.Tuple)):
            return self.bound([self.expr(x) for x in n.elts])
        if isinstance(n, ast.Dict): return self.bound({self.expr(k): self.expr(v) for k,v in zip(n.keys,n.values)})
        if isinstance(n, ast.Subscript):
            v=self.expr(n.value)
            if not isinstance(v,(dict,list,tuple,str)):raise SkillError('Unsupported indexing.')
            return v[self.expr(n.slice)]
        if isinstance(n, ast.Slice):
            return slice(self.expr(n.lower) if n.lower else None,self.expr(n.upper) if n.upper else None,self.expr(n.step) if n.step else None)
        if isinstance(n, ast.BinOp):
            left,right=self.expr(n.left),self.expr(n.right)
            if type(left) not in (int,float) or type(right) not in (int,float):raise SkillError('Arithmetic requires numbers.')
            fn={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod}.get(type(n.op))
            if not fn:raise SkillError('Unsupported arithmetic.')
            return self.bound(fn(left,right))
        if isinstance(n, ast.UnaryOp):
            v=self.expr(n.operand)
            if isinstance(n.op,ast.Not):return not v
            if type(v) not in (int,float):raise SkillError('Expected a number.')
            return self.bound(-v if isinstance(n.op,ast.USub) else +v)
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op,ast.And):
                for item in n.values:
                    v=self.expr(item)
                    if not v:return v
                return v
            for item in n.values:
                v=self.expr(item)
                if v:return v
            return v
        if isinstance(n, ast.Compare):
            left=self.expr(n.left)
            for op,r in zip(n.ops,n.comparators):
                right=self.expr(r)
                funcs={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,
                       ast.Gt:operator.gt,ast.GtE:operator.ge,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b,
                       ast.Is:operator.is_,ast.IsNot:operator.is_not}
                if not funcs[type(op)](left,right):return False
                left=right
            return True
        if isinstance(n, ast.Call):
            args=[self.expr(x) for x in n.args];kwargs={k.arg:self.expr(k.value) for k in n.keywords}
            if isinstance(n.func, ast.Attribute):
                obj=self.expr(n.func.value)
                if n.func.attr=='get' and isinstance(obj,dict) and not kwargs and 1<=len(args)<=2:return obj.get(*args)
                if n.func.attr=='append' and isinstance(obj,list) and not kwargs and len(args)==1:
                    self.bound(obj);obj.append(args[0]);self.bound(obj);return None
                raise SkillError('Invalid method call.')
            name=n.func.id
            if name=='state':
                if args or kwargs:raise SkillError('state() takes no arguments.')
                return copy.deepcopy(self.current)
            if name=='api':
                if kwargs or len(args)!=2 or not isinstance(args[0],str) or not isinstance(args[1],dict):
                    raise SkillError('Use api("name", {"argument": value}).')
                if self.calls >= self.max_calls:raise SkillStop('skill_api_budget')
                self.calls+=1
                result, observed = self.api(args[0],args[1])
                if observed is not None:self.current=copy.deepcopy(observed)
                return copy.deepcopy(result)
            if kwargs:raise SkillError('Keyword arguments are not supported here.')
            if name=='range':
                if not 1<=len(args)<=3 or any(type(x) is not int for x in args):raise SkillError('Invalid range.')
                result=range(*args)
                if len(result)>128:raise SkillError('Range exceeds 128 iterations.')
                return result
            funcs={'len':len,'min':min,'max':max,'abs':abs}
            return self.bound(funcs[name](*args))
        raise SkillError('Unsupported expression.')

    def statements(self, statements):
        for n in statements:
            self.tick()
            if isinstance(n, ast.Expr):self.expr(n.value)
            elif isinstance(n, ast.Assign):
                if len(n.targets)!=1 or not isinstance(n.targets[0],ast.Name):raise SkillError('Assign to a variable name only.')
                self.env[n.targets[0].id]=self.expr(n.value)
            elif isinstance(n,ast.AugAssign):
                if not isinstance(n.target,ast.Name):raise SkillError('Assign to a variable name only.')
                expr=ast.BinOp(left=ast.Name(id=n.target.id,ctx=ast.Load()),op=n.op,right=n.value)
                self.env[n.target.id]=self.expr(expr)
            elif isinstance(n,ast.If):self.statements(n.body if self.expr(n.test) else n.orelse)
            elif isinstance(n,(ast.For,ast.While)):
                if isinstance(n,ast.For):
                    if not isinstance(n.target,ast.Name):raise SkillError('Loop target must be a variable.')
                    seq=self.expr(n.iter)
                    if not isinstance(seq,(list,tuple,range)) or len(seq)>128:raise SkillError('Loop iterable exceeds limit.')
                    iterator=iter(list(seq))
                count=0
                while True:
                    self.tick()
                    if isinstance(n,ast.For):
                        try:self.env[n.target.id]=next(iterator)
                        except StopIteration:break
                    elif not self.expr(n.test):break
                    count+=1
                    if count>128:raise SkillStop('loop_budget')
                    try:self.statements(n.body)
                    except _Continue:continue
                    except _Break:break
            elif isinstance(n,ast.Break):raise _Break()
            elif isinstance(n,ast.Continue):raise _Continue()
            elif not isinstance(n,ast.Pass):raise SkillError('Unsupported statement.')

    def run(self, source):
        self.statements(parse_code(source).body)
