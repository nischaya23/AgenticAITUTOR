"""Small bounded Python interpreter for drawings. Never evals/execs generated code."""
import ast
import math
import operator
import re

METHODS = {'text','rect','circle','line','arrow','plot','lerp','ease'}
MATH = {name:getattr(math,name) for name in ('sin','cos','tan','atan2','sqrt','floor','ceil','exp','log','pi','tau','e')}
BUILTINS = {'min':min,'max':max,'abs':abs,'round':round,'int':int,'float':float,'str':str,'len':len}
BINARY = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,
    ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod,ast.Pow:operator.pow}
COMPARE = {ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,
    ast.Gt:operator.gt,ast.GtE:operator.ge}
ALLOWED = (ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Import,ast.alias,ast.Assign,ast.AugAssign,
    ast.Expr,ast.For,ast.If,ast.Return,ast.Pass,ast.Break,ast.Continue,ast.Name,ast.Load,ast.Store,
    ast.Constant,ast.List,ast.Tuple,ast.Subscript,ast.Slice,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,
    ast.IfExp,ast.Call,ast.Attribute,ast.keyword,ast.JoinedStr,ast.FormattedValue,ast.Add,ast.Sub,ast.Mult,
    ast.Div,ast.FloorDiv,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.Not,ast.And,ast.Or,
    ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)


def parse(code):
    try: tree=ast.parse(code)
    except SyntaxError as exc: raise ValueError(f'Invalid drawing syntax at line {exc.lineno}.') from None
    if len(list(ast.walk(tree)))>3000: raise ValueError('Animation script is too complex.')
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)]
    if len(functions)!=1 or functions[0].name!='draw_frame': raise ValueError('Define only draw_frame(canvas, t).')
    function=functions[0]
    if [a.arg for a in function.args.args]!=['canvas','t'] or function.decorator_list or function.args.defaults or function.args.vararg or function.args.kwarg or function.args.kwonlyargs:
        raise ValueError('Use def draw_frame(canvas, t) without decorators or defaults.')
    if any(not isinstance(n,(ast.Import,ast.Assign,ast.FunctionDef)) for n in tree.body):
        raise ValueError('Only constants, import math and draw_frame are allowed at module level.')
    for node in ast.walk(tree):
        if not isinstance(node,ALLOWED): raise ValueError(f'Unsupported animation syntax: {type(node).__name__}.')
        if isinstance(node,ast.Import) and any(a.name!='math' or a.asname for a in node.names):
            raise ValueError('Only import math is supported.')
        if isinstance(node,ast.Name) and node.id.startswith('_'): raise ValueError('Private names are not supported.')
        if isinstance(node,ast.Attribute):
            if not isinstance(node.value,ast.Name) or not ((node.value.id=='canvas' and node.attr in METHODS) or (node.value.id=='math' and node.attr in MATH)):
                raise ValueError('Only drawing helpers and approved math attributes are supported.')
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id not in {*BUILTINS,'range','enumerate'}:
            raise ValueError('Only approved drawing, math and numeric calls are supported.')
        if isinstance(node,ast.Call) and not isinstance(node.func,(ast.Name,ast.Attribute)):
            raise ValueError('Indirect calls are not supported.')
    return tree,function


class Flow(Exception):
    def __init__(self,kind): self.kind=kind


class DrawingScript:
    def __init__(self,code): self.tree,self.function=parse(code)
    def tick(self):
        self.remaining-=1
        if self.remaining<0: raise ValueError('Animation frame exceeded its instruction budget.')
    def limit(self,value):
        if isinstance(value,(list,tuple,str,range)) and len(value)>2000: raise ValueError('Drawing collection is too large.')
        if isinstance(value,(int,float)) and (not math.isfinite(value) or abs(value)>1e8):
            raise ValueError('Drawing number is out of bounds.')
        return value
    def assign(self,target,value,env):
        if isinstance(target,ast.Name):
            if target.id in {'canvas','math'}: raise ValueError('Drawing helper names cannot be reassigned.')
            env[target.id]=self.limit(value)
        elif isinstance(target,(ast.Tuple,ast.List)) and isinstance(value,(tuple,list)) and len(target.elts)==len(value):
            for child,item in zip(target.elts,value): self.assign(child,item,env)
        else: raise ValueError('Only variable assignments and tuple unpacking are supported.')
    def expression(self,node,env):
        self.tick()
        if isinstance(node,ast.Constant):
            if not isinstance(node.value,(str,int,float,bool,type(None))): raise ValueError('Unsupported constant.')
            return self.limit(node.value)
        if isinstance(node,ast.Name):
            if node.id not in env: raise ValueError(f'Undefined drawing variable: {node.id}.')
            return env[node.id]
        if isinstance(node,(ast.List,ast.Tuple)):
            return self.limit([self.expression(x,env) for x in node.elts])
        if isinstance(node,ast.Attribute):
            return MATH[node.attr] if node.value.id=='math' else getattr(env['canvas'],node.attr)
        if isinstance(node,ast.BinOp):
            a,b=self.expression(node.left,env),self.expression(node.right,env)
            if isinstance(node.op,ast.Pow) and (not isinstance(b,(int,float)) or abs(b)>10): raise ValueError('Exponent is too large.')
            if isinstance(node.op,ast.Mult) and ((isinstance(a,(str,list,tuple)) and isinstance(b,int) and len(a)*abs(b)>2000) or (isinstance(b,(str,list,tuple)) and isinstance(a,int) and len(b)*abs(a)>2000)):
                raise ValueError('Drawing collection is too large.')
            if isinstance(node.op,ast.Add) and isinstance(a,(str,list,tuple)) and isinstance(b,type(a)) and len(a)+len(b)>2000: raise ValueError('Drawing collection is too large.')
            return self.limit(BINARY[type(node.op)](a,b))
        if isinstance(node,ast.UnaryOp):
            value=self.expression(node.operand,env)
            return self.limit(-value if isinstance(node.op,ast.USub) else +value if isinstance(node.op,ast.UAdd) else not value)
        if isinstance(node,ast.BoolOp):
            value=None
            for child in node.values:
                value=self.expression(child,env)
                if isinstance(node.op,ast.And) and not value or isinstance(node.op,ast.Or) and value: break
            return value
        if isinstance(node,ast.Compare):
            left=self.expression(node.left,env)
            for op,right_node in zip(node.ops,node.comparators):
                right=self.expression(right_node,env)
                if not COMPARE[type(op)](left,right): return False
                left=right
            return True
        if isinstance(node,ast.IfExp): return self.expression(node.body if self.expression(node.test,env) else node.orelse,env)
        if isinstance(node,ast.Subscript):
            value=self.expression(node.value,env)
            if not isinstance(value,(str,list,tuple,range)): raise ValueError('Only list or text indexing is supported.')
            if isinstance(node.slice,ast.Slice):
                index=slice(*(self.expression(x,env) if x else None for x in (node.slice.lower,node.slice.upper,node.slice.step)))
            else: index=self.expression(node.slice,env)
            return self.limit(value[index])
        if isinstance(node,ast.JoinedStr):
            parts=[]
            for part in node.values:
                if isinstance(part,ast.Constant): parts.append(str(part.value))
                else:
                    value=self.expression(part.value,env)
                    spec=self.expression(part.format_spec,env) if part.format_spec else ''
                    if not re.fullmatch(r'(?:\.[0-6])?[fge%]?',spec): raise ValueError('Unsupported text format.')
                    parts.append(format(value,spec))
            return self.limit(''.join(parts))
        if isinstance(node,ast.Call):
            args=[self.expression(x,env) for x in node.args]
            kwargs={x.arg:self.expression(x.value,env) for x in node.keywords}
            if None in kwargs: raise ValueError('Expanded arguments are unsupported.')
            if isinstance(node.func,ast.Name):
                name=node.func.id
                if name=='range': return self.limit(range(*args))
                if name=='enumerate': return self.limit(list(enumerate(*args)))
                return self.limit(BUILTINS[name](*args,**kwargs))
            function=self.expression(node.func,env)
            if node.func.value.id=='math': return self.limit(function(*args,**kwargs))
            result=function(*args,**kwargs)
            return self.limit(result) if node.func.attr in ('lerp','ease') else None
        raise ValueError('Unsupported drawing expression.')
    def statements(self,statements,env):
        for node in statements:
            self.tick()
            if isinstance(node,ast.Import): continue
            if isinstance(node,ast.Assign):
                value=self.expression(node.value,env)
                for target in node.targets: self.assign(target,value,env)
            elif isinstance(node,ast.AugAssign):
                if not isinstance(node.target,ast.Name): raise ValueError('Update a variable, not an object.')
                fake=ast.BinOp(left=ast.Name(id=node.target.id,ctx=ast.Load()),op=node.op,right=node.value)
                self.assign(node.target,self.expression(fake,env),env)
            elif isinstance(node,ast.Expr): self.expression(node.value,env)
            elif isinstance(node,ast.If): self.statements(node.body if self.expression(node.test,env) else node.orelse,env)
            elif isinstance(node,ast.For):
                values=self.expression(node.iter,env)
                if not isinstance(values,(list,tuple,range,str)): raise ValueError('Loop over a bounded collection.')
                for value in values:
                    self.assign(node.target,value,env)
                    try: self.statements(node.body,env)
                    except Flow as flow:
                        if flow.kind=='break': break
                        if flow.kind=='continue': continue
                        raise
                else: self.statements(node.orelse,env)
            elif isinstance(node,ast.Return): raise Flow('return')
            elif isinstance(node,ast.Break): raise Flow('break')
            elif isinstance(node,ast.Continue): raise Flow('continue')
            elif isinstance(node,ast.Pass): continue
            else: raise ValueError('Unsupported drawing statement.')
    def draw(self,canvas,t):
        self.remaining=12000
        env={'canvas':canvas,'t':t}
        try:
            self.statements([n for n in self.tree.body if not isinstance(n,ast.FunctionDef)],env)
            self.statements(self.function.body,env)
        except Flow as flow:
            if flow.kind!='return': raise ValueError('Unexpected loop control.') from None
